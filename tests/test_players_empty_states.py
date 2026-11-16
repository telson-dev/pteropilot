"""空状态：区分「没采过」与「采过但不在窗口内」。

以前两种情况输出完全一样（都是「还没有记录」），用户没法判断该补采还是该放大
--days。现在 stats 带 ``total`` / ``last_any``（全时段），format_stats 据此分流。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


from pteropilot import playerlog

BASE = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _write(directory: Path, server_id: str, rows: list[tuple[datetime, list[str]]]) -> None:
    path = playerlog.log_path(server_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '{"t": "%s", "p": %s}' % (ts.strftime("%Y-%m-%dT%H:%M:%SZ"), str(pl).replace("'", '"'))
        for ts, pl in rows
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ stats 新键

def test_stats_empty_file_has_totals(tmp_path: Path) -> None:
    d = playerlog.stats("s", directory=tmp_path, now=BASE)
    assert d["total"] == 0 and d["last_any"] is None
    assert d["samples"] == 0


def test_stats_counts_all_time_not_just_window(tmp_path: Path) -> None:
    """窗口外的采样进 total 但不进 samples——这正是要区分的信息。"""
    _write(tmp_path, "s", [
        (BASE - timedelta(days=30), ["old"]),
        (BASE - timedelta(days=29), ["old", "older"]),
        (BASE - timedelta(hours=1), ["fresh"]),
    ])
    d = playerlog.stats("s", days=7, directory=tmp_path, now=BASE)
    assert d["samples"] == 1, "只有 1 条在 7 天窗口内"
    assert d["total"] == 3, "全时段是 3 条"
    assert d["last_any"] == (BASE - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_stats_all_outside_window(tmp_path: Path) -> None:
    _write(tmp_path, "s", [(BASE - timedelta(days=30), ["ghost"])])
    d = playerlog.stats("s", days=7, directory=tmp_path, now=BASE)
    assert d["samples"] == 0
    assert d["total"] == 1
    assert d["last_any"] is not None


# ------------------------------------------------------------------ 文案分流

def test_format_never_sampled_points_at_record() -> None:
    text = playerlog.format_stats("srv", {"samples": 0, "players": [], "total": 0})
    assert "还没有任何采样记录" in text
    assert "--record" in text, "要告诉用户怎么补采，而不是只说没数据"


def test_format_stale_says_widen_window() -> None:
    """有数据但在窗口外：应该建议放大 --days，而不是叫他再去补采。"""
    text = playerlog.format_stats(
        "srv",
        {"samples": 0, "players": [], "total": 5, "last_any": "2026-11-01T00:00:00Z"},
        days=7,
    )
    assert "5 条采样" in text
    assert "2026-11-01T00:00:00Z" in text
    assert "--days 30" in text
    assert "--record" not in text, "这种情况不是没采过，不该叫他补采"


def test_format_stale_without_last_any() -> None:
    """last_any 缺失时也不能崩，退化成问号。"""
    text = playerlog.format_stats("srv", {"samples": 0, "players": [], "total": 2}, days=7)
    assert "2 条采样" in text and "?" in text


# ------------------------------------------------------------------ 端到端

def test_cli_stale_data_message(tmp_path: Path, monkeypatch, capsys) -> None:
    from pteropilot import server

    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    monkeypatch.setattr(server.playerlog, "DEFAULT_DIR", tmp_path)
    _write(tmp_path, "s", [(BASE - timedelta(days=30), ["ghost"])])

    rc = server._run_players(["--players", "s", "--days", "7"])
    out = capsys.readouterr().out
    assert rc == 1, "窗口内没数据仍返回 1（cron 语义不变）"
    assert "--days 30" in out


def test_cli_never_sampled_message(tmp_path: Path, monkeypatch, capsys) -> None:
    from pteropilot import server

    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    monkeypatch.setattr(server.playerlog, "DEFAULT_DIR", tmp_path)

    rc = server._run_players(["--players", "never"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "--record" in out
