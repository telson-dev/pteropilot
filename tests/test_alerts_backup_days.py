"""``pteropilot-alerts --backup-days``：定时告警里也管备份过期。

上一轮给 ``--status`` 加了备份年龄，但那要用户主动看。真正会丢数据的人不会
每天看状态——所以接进定时告警：备份太旧就推一条。
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

import pteropilot.alerts as alerts

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def _servers_payload(ids: list[str]) -> dict:
    return {
        "data": [{"attributes": {"identifier": i, "name": i}} for i in ids],
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(ids)}},
    }


def _b(created: str, ok: bool | None = True) -> dict:
    a: dict = {"created_at": created}
    if ok is not None:
        a["is_successful"] = ok
    return {"attributes": a}


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel")
    monkeypatch.setenv("PANEL_API_KEY", "test")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


@pytest.fixture
def sent(monkeypatch):
    out: list[str] = []

    async def spy(webhook, content, token=None):
        out.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    return out


# ------------------------------------------------------------------ 纯函数

def test_disabled_returns_empty() -> None:
    assert alerts.check_backup_age([], 0) == []
    assert alerts.check_backup_age([_b("2020-01-01T00:00:00Z")], 0) == []


def test_never_backed_up() -> None:
    out = alerts.check_backup_age([], 7, now=NOW)
    assert len(out) == 1 and "从未有过成功备份" in out[0]


def test_fresh_backup_is_quiet() -> None:
    assert alerts.check_backup_age([_b("2026-11-15T00:00:00Z")], 7, now=NOW) == []


def test_stale_backup_alerts() -> None:
    out = alerts.check_backup_age([_b("2026-08-01T00:00:00Z")], 7, now=NOW)
    assert len(out) == 1
    # 2026-08-01 00:00 → 2026-11-16 12:00 = 107.5 天，格式化后四舍五入到 108
    assert "108 天前" in out[0]
    assert "阈值 7 天" in out[0]


def test_exactly_at_threshold_is_quiet() -> None:
    """正好等于阈值不算超标（和 CPU/内存阈值的口径一致）。"""
    b = [_b(NOW.isoformat())]
    assert alerts.check_backup_age(b, 7, now=NOW) == []


def test_failed_backups_do_not_count() -> None:
    """只有失败的备份 → 等价于从未备份。"""
    out = alerts.check_backup_age([_b("2026-11-15T00:00:00Z", ok=False)], 7, now=NOW)
    assert "从未有过成功备份" in out[0]


def test_failed_ignored_when_a_good_one_exists() -> None:
    backups = [_b("2026-11-15T00:00:00Z", ok=False), _b("2026-11-14T00:00:00Z", ok=True)]
    assert alerts.check_backup_age(backups, 7, now=NOW) == []


def test_counts_successful_backups_in_message() -> None:
    backups = [_b("2026-01-01T00:00:00Z"), _b("2025-12-01T00:00:00Z")]
    out = alerts.check_backup_age(backups, 7, now=NOW)
    assert "共 2 个成功备份" in out[0]


# ------------------------------------------------------------------ 整条链路

async def test_stale_backup_notifies(fake, env_panel, sent):
    fake.route("GET", "s1/backups", {
        "data": [_b("2026-01-01T00:00:00Z"), _b("2025-12-01T00:00:00Z")]
    })
    fake.route("GET", "s1/resources", {"current_state": "running"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, include_log_signals=False,
    )
    assert result["abnormal"] == 1
    assert result["notified"] == 1
    assert "stale_backup" in sent[0], "合成状态要写 stale_backup"
    assert "备份" in sent[0]


async def test_fresh_backup_stays_quiet(fake, env_panel, sent):
    from datetime import datetime as _dt
    fresh = _dt.now(timezone.utc).isoformat()
    fake.route("GET", "s1/backups", {"data": [_b(fresh)]})
    fake.route("GET", "s1/resources", {"current_state": "running"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, include_log_signals=False,
    )
    assert result["abnormal"] == 0
    assert sent == []


async def test_backup_api_failure_is_silent(fake, env_panel, sent):
    """备份接口挂了不能推「从未备份」——假警报会让人直接关掉告警。"""
    fake.route("GET", "s1/backups", httpx.Response(500))
    fake.route("GET", "s1/resources", {"current_state": "running"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, include_log_signals=False,
    )
    assert result["abnormal"] == 0
    assert sent == []


async def test_disabled_does_not_call_backup_api(fake, env_panel, sent):
    fake.route("GET", "s1/resources", {"current_state": "running"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=0, include_log_signals=False,
    )
    assert all("backups" not in str(r.url) for r in fake.requests), \
        "backup_days=0 时不该请求备份接口"


async def test_offline_wins_over_stale_backup(fake, env_panel, sent):
    fake.route("GET", "s1/backups", {"data": []})
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, include_log_signals=False,
    )
    assert "offline" in sent[0]
    assert "stale_backup" not in sent[0], "真·异常状态优先，不该被合成为备份过期"


async def test_stale_backup_respects_cooldown(fake, env_panel, sent, tmp_path):
    state_file = tmp_path / "state.json"
    fake.route("GET", "s1/backups", {"data": []})
    fake.route("GET", "s1/resources", {"current_state": "running"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, cooldown_minutes=60,
        state_file=state_file, include_log_signals=False,
    )
    second = await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, cooldown_minutes=60,
        state_file=state_file, include_log_signals=False,
    )
    assert second["suppressed"] == 1
    assert len(sent) == 1


async def test_dry_run_plans_stale_backup(fake, env_panel, sent):
    fake.route("GET", "s1/backups", {"data": []})
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", backup_days=7, include_log_signals=False, dry_run=True,
    )
    assert result["planned"] == 1
    assert result["plans"][0]["state"] == "stale_backup"
    assert sent == [], "演练模式不该真的发"
