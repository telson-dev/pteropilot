"""告警推送失败必须与查询失败区分开。

为什么重要：服务器真的挂了、webhook 又推送失败时，用户会以为「有监控」，
实际没人收到通知。这种静默失败必须醒目提示，并让退出码非 0（便于 cron/监控发现）。
"""

from __future__ import annotations

import asyncio

import pytest

from pteropilot import alerts


class _FakeCfg:
    def validate(self):
        return None


class _FakeClient:
    """最小面板客户端：1 台离线服务器。"""

    def __init__(self):
        self.closed = False

    async def client_servers(self):
        return [{"attributes": {"identifier": "srv1", "name": "srv1", "uuid": "u1"}}]

    async def resources(self, ident):
        return {"current_state": "offline", "is_suspended": False, "is_installing": False}

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_panel(monkeypatch):
    monkeypatch.setattr(alerts, "Config", _FakeCfg)
    monkeypatch.setattr(alerts, "PanelClient", lambda cfg, timeout=30.0: _FakeClient())


def _scan(monkeypatch, tmp_path, send_boom: bool):
    async def good_webhook(url, message):
        return None

    async def bad_webhook(url, message):
        raise RuntimeError("HTTP 500 from webhook")

    monkeypatch.setattr(alerts, "send_webhook", bad_webhook if send_boom else good_webhook)
    return asyncio.run(
        alerts.scan_and_notify(
            webhook="https://hook.example/x",
            min_level="abnormal",
            timeout=5.0,
            include_log_signals=False,
            state_file=tmp_path / "state.json",
            config_file=None,
        )
    )


def test_send_failure_is_reported_separately(monkeypatch, tmp_path):
    result = _scan(monkeypatch, tmp_path, send_boom=True)
    assert result["send_failed"] == 1
    assert result["notified"] == 0, "推送失败不能算已推送"
    assert result["send_errors"], "应记录推送失败明细"
    # 查询错误列表不应被推送错误污染
    assert not any("webhook" in e for e in result["errors"])


def test_successful_send_counts_notified(monkeypatch, tmp_path):
    result = _scan(monkeypatch, tmp_path, send_boom=False)
    assert result["send_failed"] == 0
    assert result["notified"] == 1


def test_send_failure_exit_code_nonzero(monkeypatch, tmp_path, capsys):
    """推送失败必须让退出码非 0，否则 cron/监控发现不了「告警没送达」。"""
    result = {"checked": 1, "abnormal": 1, "notified": 0, "planned": 0, "plans": [],
              "suppressed": 0, "pending": 0, "errors": [],
              "send_failed": 1, "send_errors": ["srv1（webhook）: HTTP 500"]}
    monkeypatch.setattr(alerts, "scan_and_notify", _fake_scan(result))
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts", "--webhook", "https://hook.example/x"])
    with pytest.raises(SystemExit) as exc:
        alerts.main()
    assert int(exc.value.code or 0) == 3
    out = capsys.readouterr().out
    assert "告警没能送达" in out
    assert "webhook" in out


def _fake_scan(result):
    async def _inner(**kwargs):
        return result
    return _inner


def test_query_failure_does_not_trip_send_exit_code(monkeypatch, capsys):
    """只有查询失败、没有推送失败时，退出码保持 0。"""
    result = {"checked": 1, "abnormal": 0, "notified": 0, "planned": 0, "plans": [],
              "suppressed": 0, "pending": 0, "errors": ["srv1: connect timeout"],
              "send_failed": 0, "send_errors": []}
    monkeypatch.setattr(alerts, "scan_and_notify", _fake_scan(result))
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts", "--webhook", "https://hook.example/x"])
    alerts.main()  # 不应抛 SystemExit
    assert "查询失败" in capsys.readouterr().out
