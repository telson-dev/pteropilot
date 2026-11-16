"""``--status --backups``：把「最新备份有多旧」摆到台面上。

起因：服务器挂了才发现上次备份是三个月前——这是真正会丢数据的事故，
但看状态时完全看不出来。备份接口本来就有，只是没人把它和状态放一起看。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pteropilot import server

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ 时间解析

@pytest.mark.parametrize("raw,expect", [
    ("2026-11-16T12:00:00Z", datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)),
    ("2026-11-16T12:00:00.123456Z", datetime(2026, 11, 16, 12, 0, 0, 123456, tzinfo=timezone.utc)),
    ("2026-11-16T12:00:00+00:00", datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)),
    ("2026-11-16T20:00:00+08:00", datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)),
])
def test_parse_iso_utc(raw: str, expect: datetime) -> None:
    got = server._parse_iso_utc(raw)
    assert got is not None
    assert got == expect


@pytest.mark.parametrize("raw", ["", "   ", "not-a-date", None])
def test_parse_iso_utc_garbage(raw) -> None:
    assert server._parse_iso_utc(raw) is None


def test_parse_iso_utc_naive_treated_as_utc() -> None:
    """没有时区的串按 UTC 处理，别让本地时区把天数算偏。"""
    got = server._parse_iso_utc("2026-11-16T12:00:00")
    assert got is not None and got.tzinfo is not None


# ------------------------------------------------------------------ 挑最新备份

def _b(created: str, ok: bool | None = True) -> dict:
    a: dict = {"created_at": created}
    if ok is not None:
        a["is_successful"] = ok
    return {"attributes": a}


def test_empty_backups() -> None:
    assert server._newest_backup_age([], NOW) == (None, None, 0)


def test_picks_newest() -> None:
    backups = [_b("2026-11-01T00:00:00Z"), _b("2026-11-15T00:00:00Z"), _b("2026-11-10T00:00:00Z")]
    newest, days, ok = server._newest_backup_age(backups, NOW)
    assert ok == 3
    assert days == pytest.approx(1.5)   # 15 日 00:00 → 16 日 12:00
    assert newest.startswith("2026-11-15")


def test_failed_backups_ignored() -> None:
    """失败备份带 created_at，但恢复不了——拿它当「有备份」会误导用户。"""
    backups = [_b("2026-11-15T00:00:00Z", ok=False), _b("2026-11-01T00:00:00Z", ok=True)]
    newest, days, ok = server._newest_backup_age(backups, NOW)
    assert ok == 1
    assert days == pytest.approx(15.5)


def test_only_failed_backups_counts_as_never() -> None:
    backups = [_b("2026-11-15T00:00:00Z", ok=False)]
    assert server._newest_backup_age(backups, NOW) == (None, None, 0)


def test_missing_is_successful_treated_as_ok() -> None:
    """老面板没有 is_successful 字段：宁可当作有备份，也不要全报「从未」。"""
    backups = [_b("2026-11-15T00:00:00Z", ok=None)]
    newest, days, ok = server._newest_backup_age(backups, NOW)
    assert ok == 1 and days == pytest.approx(1.5)


def test_flat_attributes_accepted() -> None:
    """有的返回没有 attributes 包装，直接是属性字典。"""
    backups = [{"created_at": "2026-11-15T00:00:00Z", "is_successful": True}]
    assert server._newest_backup_age(backups, NOW)[2] == 1


def test_future_timestamp_clamped_to_zero() -> None:
    """面板时钟比本地快时别出现负天数。"""
    backups = [_b("2026-11-20T00:00:00Z")]
    _, days, _ = server._newest_backup_age(backups, NOW)
    assert days == 0.0


# ------------------------------------------------------------------ 单元格文案

@pytest.mark.parametrize("row,expect", [
    ({"backup_age_days": None, "backup_ok_count": 0}, "从未"),
    ({"backup_age_days": None, "backup_ok_count": None}, "未知"),
    ({"backup_age_days": 0.2, "backup_ok_count": 3}, "今天"),
    ({"backup_age_days": 3.4, "backup_ok_count": 3}, "3 天前"),
    ({"backup_age_days": 90.0, "backup_ok_count": 1}, "90 天前"),
])
def test_fmt_backup_age(row: dict, expect: str) -> None:
    assert server._fmt_backup_age(row) == expect


def test_fmt_backup_age_without_field() -> None:
    assert server._fmt_backup_age({"id": "s1"}) == "-"


# ------------------------------------------------------------------ 表格

def _row(**kw) -> dict:
    base = {
        "id": "smp", "name": "SMP", "state": "running", "health": "healthy",
        "cpu": 1.0, "memory_bytes": 100 * 1024 * 1024,
        "memory_limit": 1024 * 1024 * 1024, "disk_bytes": 0, "players": 1,
    }
    base.update(kw)
    return base


def test_table_has_no_backup_column_by_default() -> None:
    out = server._format_status({"servers": [_row()], "errors": []})
    assert "最新备份" not in out


def test_table_adds_backup_column_when_present() -> None:
    out = server._format_status(
        {"servers": [_row(backup_age_days=3.0, backup_ok_count=2)], "errors": []}
    )
    assert "最新备份" in out
    assert "3 天前" in out


def test_table_shows_never() -> None:
    out = server._format_status(
        {"servers": [_row(backup_age_days=None, backup_ok_count=0)], "errors": []}
    )
    assert "从未" in out


# ------------------------------------------------------------------ 提醒段

def test_warn_lists_never_stale_and_unknown(capsys) -> None:
    data = {"servers": [
        _row(id="good", backup_age_days=1.0, backup_ok_count=2),
        _row(id="never", backup_age_days=None, backup_ok_count=0),
        _row(id="old", backup_age_days=90.0, backup_ok_count=1),
        _row(id="unknown", backup_age_days=None, backup_ok_count=None),
    ], "errors": []}
    server._warn_stale_backups(data, max_days=7)
    out = capsys.readouterr().out
    assert "从未有过成功备份" in out and "never" in out
    assert "90 天前" in out and "old" in out
    assert "查询失败" in out and "unknown" in out
    assert "good" not in out, "健康的不用列出来"


def test_warn_all_fine(capsys) -> None:
    server._warn_stale_backups(
        {"servers": [_row(backup_age_days=1.0, backup_ok_count=1)], "errors": []}, max_days=7
    )
    assert "全部在 7 天内有成功备份" in capsys.readouterr().out


def test_warn_ignores_rows_without_backup_field(capsys) -> None:
    server._warn_stale_backups({"servers": [_row()], "errors": []}, max_days=7)
    assert "全部在 7 天内有成功备份" in capsys.readouterr().out


# ------------------------------------------------------------------ 端到端

@pytest.fixture
def panel_env(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


def test_run_status_passes_with_backups_flag(panel_env, monkeypatch, capsys) -> None:
    seen: list[bool] = []

    async def fake_status(sid, with_backups=False):
        seen.append(with_backups)
        return {"servers": [_row(backup_age_days=30.0, backup_ok_count=1)], "errors": []}

    monkeypatch.setattr(server, "_status_data", fake_status)
    rc = server._run_status(["--status", "--backups", "--backup-days", "7"])
    out = capsys.readouterr().out
    assert rc == 0
    assert seen == [True]
    assert "最新备份" in out
    assert "30 天前" in out


def test_run_status_default_does_not_ask_backups(panel_env, monkeypatch, capsys) -> None:
    seen: list[bool] = []

    async def fake_status(sid, with_backups=False):
        seen.append(with_backups)
        return {"servers": [_row()], "errors": []}

    monkeypatch.setattr(server, "_status_data", fake_status)
    server._run_status(["--status"])
    assert seen == [False], "不加 --backups 时不该多打一次备份接口"
    assert "最新备份" not in capsys.readouterr().out


@pytest.mark.parametrize("backup_ok,expect", [
    (0, "从未"),
    (None, "未知"),
])
def test_backup_fields_marks_failure_as_unknown(panel_env, backup_ok, expect) -> None:
    """备份接口失败与「从未备份」必须分开，否则用户会误判数据状态。"""
    import asyncio

    class Client:
        async def list_backups(self, sid):
            if backup_ok is None:
                raise RuntimeError("boom")
            return []

    fields = asyncio.run(server._backup_fields("smp", Client()))
    assert server._fmt_backup_age(fields) == expect
