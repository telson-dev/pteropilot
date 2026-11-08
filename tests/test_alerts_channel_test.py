"""`pteropilot-alerts --test` 通道自检测试：成功/失败/JSON/双渠道/不写状态。"""

from __future__ import annotations

import json
import sys

import pytest

import pteropilot.alerts as alerts


@pytest.fixture
def no_state_write(monkeypatch, tmp_path):
    """确保 --test 不会碰冷却状态文件。"""
    state = tmp_path / "should_not_exist.json"
    monkeypatch.setenv("ALERTS_STATE_FILE", str(state))
    return state


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["pteropilot-alerts"] + argv)
    with pytest.raises(SystemExit) as ei:
        alerts.main()
    return ei.value.code


def test_test_mode_webhook_ok(monkeypatch, capsys, no_state_write):
    sent = []

    async def fake_webhook(url, content, token=None):
        sent.append((url, content, token))

    monkeypatch.setattr(alerts, "send_webhook", fake_webhook)
    code = _run_main(monkeypatch, ["--webhook", "https://hook.example/send", "--test"])
    out = capsys.readouterr().out
    assert code == 0
    assert len(sent) == 1
    assert "告警通道测试" in sent[0][1]
    assert "渠道配置正常" in out
    # 测试消息不应包含服务器数据
    assert "服务器" not in sent[0][1] or "不含服务器数据" in sent[0][1]
    assert not no_state_write.exists(), "--test 不应写状态文件"


def test_test_mode_webhook_failure_exit_1(monkeypatch, capsys, no_state_write):
    async def boom(url, content, token=None):
        raise RuntimeError("机器人被移出群")

    monkeypatch.setattr(alerts, "send_webhook", boom)
    code = _run_main(monkeypatch, ["--webhook", "https://hook.example/send", "--test"])
    out = capsys.readouterr().out
    assert code == 1
    assert "发送失败" in out
    assert "机器人被移出群" in out
    assert "常见原因" in out


def test_test_mode_json_pure(monkeypatch, capsys, no_state_write):
    async def boom(url, content, token=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(alerts, "send_webhook", boom)
    code = _run_main(monkeypatch, ["--webhook", "https://hook.example/send", "--test", "--json"])
    out = capsys.readouterr().out.strip()
    assert code == 1
    data = json.loads(out)  # 必须是纯 JSON，不能混人类可读文本
    assert data["test"] is True
    assert data["failures"] == 1
    assert data["results"][0]["channel"] == "webhook"
    assert data["results"][0]["ok"] is False
    assert "hints" in data["results"][0]


def test_test_mode_email_channel(monkeypatch, capsys, no_state_write):
    calls = []

    def fake_email(to, subject, content, host, user, password, port=465, frm=None):
        calls.append({"to": to, "host": host, "password": password, "port": port})

    monkeypatch.setattr(alerts, "send_email", fake_email)
    code = _run_main(monkeypatch, [
        "--smtp-host", "smtp.example.com", "--smtp-user", "u@x.com",
        "--smtp-pass", "secret-code", "--mail-to", "ops@x.com", "--test",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "smtp.example.com"
    assert "邮件" in out


def test_test_mode_both_channels_one_fails(monkeypatch, capsys, no_state_write):
    async def ok_webhook(url, content, token=None):
        return None

    def boom_email(**kw):
        raise alerts.smtplib.SMTPAuthenticationError(535, b"auth failed")

    monkeypatch.setattr(alerts, "send_webhook", ok_webhook)
    monkeypatch.setattr(alerts, "send_email", boom_email)
    code = _run_main(monkeypatch, [
        "--webhook", "https://hook.example/send",
        "--smtp-host", "smtp.example.com", "--smtp-user", "u@x.com",
        "--smtp-pass", "bad", "--mail-to", "ops@x.com", "--test",
    ])
    out = capsys.readouterr().out
    assert code == 1  # 任一渠道失败即退出 1
    assert "✅ webhook" in out
    assert "❌ 邮件" in out


def test_test_mode_does_not_require_panel_env(monkeypatch, capsys, no_state_write):
    """--test 只验证渠道，没有面板配置也应该能跑。"""
    monkeypatch.delenv("PANEL_URL", raising=False)
    monkeypatch.delenv("PANEL_API_KEY", raising=False)

    async def ok_webhook(url, content, token=None):
        return None

    monkeypatch.setattr(alerts, "send_webhook", ok_webhook)
    # 未设置 PANEL_URL 时，--test 不应因面板缺失而报错
    code = _run_main(monkeypatch, ["--webhook", "https://hook.example/send", "--test"])
    assert code == 0
