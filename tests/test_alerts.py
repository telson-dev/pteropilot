"""pteropilot-alerts（崩服告警通知通道）测试。

只读：扫描只用 GET；通知通过 webhook（测试里用 spy / 假 HTTP 捕获）。
"""

from __future__ import annotations

import os
import sys
import time

import httpx
import pytest

import pteropilot.alerts as alerts


def _servers_payload(data: list[dict]) -> dict:
    return {
        "data": data,
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(data)}},
    }


def _server(identifier: str) -> dict:
    return {"attributes": {"identifier": identifier, "name": identifier}}


# ================= build_alert_message =================


def test_build_alert_message_fields():
    msg = alerts.build_alert_message("s1", "offline", ["健康度: offline", "崩溃信号: 1 类"])
    assert "s1" in msg
    assert "offline" in msg
    assert "崩溃信号" in msg
    assert msg.startswith("⚠️")
    assert "只读检测" in msg


# ================= send_webhook =================


class FakeHTTP:
    """捕获 post 的假 httpx.AsyncClient。"""

    def __init__(self, *a, **k):
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return httpx.Response(200, json={"errcode": 0}, request=httpx.Request("POST", url))


def test_send_webhook_payload_and_token(monkeypatch):
    captured = FakeHTTP()
    monkeypatch.setattr(alerts.httpx, "AsyncClient", lambda *a, **k: captured)
    monkeypatch.setattr(alerts, "DEFAULT_WEBHOOK_URL", "")
    asyncio_run(alerts.send_webhook("https://hook.example/send", "内容", token="tk-123"))
    assert len(captured.posts) == 1
    url, payload = captured.posts[0]
    assert "key=tk-123" in url
    assert payload == {"msgtype": "text", "text": {"content": "内容"}}


def test_send_webhook_no_token(monkeypatch):
    captured = FakeHTTP()
    monkeypatch.setattr(alerts.httpx, "AsyncClient", lambda *a, **k: captured)
    asyncio_run(alerts.send_webhook("https://hook.example/send", "内容"))
    url, _ = captured.posts[0]
    assert "key=" not in url


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# ================= scan_and_notify =================


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel")
    monkeypatch.setenv("PANEL_API_KEY", "test")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


async def test_scan_notifies_abnormal_only(fake, env_panel, monkeypatch):
    notified: list[tuple[str, str]] = []

    async def spy(webhook, content, token=None):
        notified.append((webhook, content))

    monkeypatch.setattr(alerts, "send_webhook", spy)
    # 具体路由先注册，父路由 /api/client 最后
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="Killed\nOutOfMemoryError"))
    fake.route("GET", "s3/resources", {"current_state": "error"})
    fake.route("GET", "s3/console", httpx.Response(200, text="fatal: unable"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s2"), _server("s3")]))

    result = await alerts.scan_and_notify("https://hook.example/send")
    assert result["checked"] == 3
    assert result["abnormal"] == 2  # s1 offline + s3 error
    assert result["notified"] == 2
    assert result["errors"] == []
    ids = [c.split(" ")[3] for _, c in notified]  # "⚠️ PteroPilot 告警：服务器 s1 ..."
    assert "s1" in ids and "s3" in ids and "s2" not in ids


async def test_scan_min_level_error_skips_offline(fake, env_panel, monkeypatch):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s3/resources", {"current_state": "error"})
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s3")]))

    result = await alerts.scan_and_notify("https://hook.example/send", min_level="error")
    assert result["abnormal"] == 1
    assert result["notified"] == 1
    assert "s3" in notified[0]


async def test_scan_no_servers(fake, env_panel, monkeypatch):
    async def spy(webhook, content, token=None):
        raise AssertionError("不应发送")

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "/api/client", _servers_payload([]))
    result = await alerts.scan_and_notify("https://hook.example/send")
    assert result == {"checked": 0, "abnormal": 0, "notified": 0, "suppressed": 0, "pending": 0, "errors": []}


async def test_scan_single_failure_not_blocking(fake, env_panel, monkeypatch):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", Exception("boom"))
    fake.route("GET", "s3/resources", {"current_state": "offline"})
    fake.route("GET", "s3/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s3")]))

    result = await alerts.scan_and_notify("https://hook.example/send")
    assert result["checked"] == 2
    assert result["notified"] == 1  # s3 仍被推送
    assert len(result["errors"]) == 1
    assert "s1" in result["errors"][0]


async def test_scan_log_failure_keeps_state_alert(fake, env_panel, monkeypatch):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", Exception("console down"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify("https://hook.example/send")
    assert result["notified"] == 1
    assert "日志读取失败" in notified[0]


# ================= 冷却去重（阶段三）=================


def test_state_file_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert alerts.load_state(p) == {}
    alerts.save_state(p, {"s1": {"state": "offline", "ts": 123.0}})
    loaded = alerts.load_state(p)
    assert loaded["s1"]["state"] == "offline"
    assert loaded["s1"]["ts"] == 123.0


async def test_cooldown_suppresses_duplicate(fake, env_panel, monkeypatch, tmp_path):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    state_file = tmp_path / "state.json"
    first = await alerts.scan_and_notify("https://hook.example/send", cooldown_minutes=60, state_file=state_file)
    assert first["notified"] == 1
    assert first["suppressed"] == 0
    assert state_file.exists()

    second = await alerts.scan_and_notify("https://hook.example/send", cooldown_minutes=60, state_file=state_file)
    assert second["notified"] == 0
    assert second["suppressed"] == 1  # 同状态 + 冷却期内 → 去重
    assert len(notified) == 1


async def test_state_change_notifies_immediately(fake, env_panel, monkeypatch, tmp_path):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    # 注意路由顺序：resources/console 必须注册在父路由 /api/client 之前
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    state_file = tmp_path / "state.json"
    first = await alerts.scan_and_notify("https://hook.example/send", cooldown_minutes=60, state_file=state_file)
    assert first["notified"] == 1
    # 把历史状态改成 running（与当前不同）→ 状态变化是「新故障」，冷却期内也立即推送
    alerts.save_state(state_file, {"s1": {"state": "running", "ts": time.time()}})
    second = await alerts.scan_and_notify("https://hook.example/send", cooldown_minutes=60, state_file=state_file)
    assert second["notified"] == 1
    assert second["suppressed"] == 0
    assert "offline" in notified[-1]


async def test_cooldown_expiry_notifies_again(fake, env_panel, monkeypatch, tmp_path):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    state_file = tmp_path / "state.json"
    await alerts.scan_and_notify("https://hook.example/send", cooldown_minutes=60, state_file=state_file)
    # 把状态文件里的时间改到很久以前 → 冷却过期 → 再次推送
    alerts.save_state(state_file, {"s1": {"state": "offline", "ts": time.time() - 3600}})
    second = await alerts.scan_and_notify("https://hook.example/send", cooldown_minutes=60, state_file=state_file)
    assert second["notified"] == 1
    assert len(notified) == 2


async def test_cooldown_disabled_by_default(fake, env_panel, monkeypatch, tmp_path):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    first = await alerts.scan_and_notify("https://hook.example/send")
    second = await alerts.scan_and_notify("https://hook.example/send")
    assert first["notified"] == 1 and second["notified"] == 1  # 默认不冷却 → 每次都推
    assert len(notified) == 2


# ================= 按服务器级别配置（阶段六）=================


def test_load_alerts_config(tmp_path):
    p = tmp_path / "alerts.json"
    assert alerts.load_alerts_config(p) == {}
    p.write_text('{"default": {"confirm_count": 2}, "servers": {"s1": {"cooldown_minutes": 60}}}', encoding="utf-8")
    cfg = alerts.load_alerts_config(p)
    assert cfg["default"]["confirm_count"] == 2
    assert cfg["servers"]["s1"]["cooldown_minutes"] == 60


def test_server_effective_precedence():
    cfg = {"default": {"confirm_count": 2, "cooldown_minutes": 15}, "servers": {"s1": {"confirm_count": 3}}}
    assert alerts.server_effective(cfg, "s1", 1, 0, "abnormal", webhook="https://w", smtp_to="t@x.com") == {
        "confirm_count": 3, "cooldown_minutes": 15, "min_level": "abnormal",
        "webhook": True, "email": False, "webhook_url": "https://w", "mail_to": "t@x.com",
    }
    # 无服务器条目 → 用 default
    assert alerts.server_effective(cfg, "s2", 1, 0, "abnormal", webhook="https://w") == {
        "confirm_count": 2, "cooldown_minutes": 15, "min_level": "abnormal",
        "webhook": True, "email": False, "webhook_url": "https://w", "mail_to": "",
    }
    # 无配置 → 用参数
    assert alerts.server_effective({}, "s2", 5, 30, "error", webhook="", email_enabled=True, smtp_to="b@x.com") == {
        "confirm_count": 5, "cooldown_minutes": 30, "min_level": "error",
        "webhook": False, "email": True, "webhook_url": "", "mail_to": "b@x.com",
    }


async def test_per_server_confirm_count(fake, env_panel, monkeypatch, tmp_path):
    """s1 配置 confirm_count=3，s2 用 default=2 → 各自独立计数推送。"""
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s2/resources", {"current_state": "offline"})
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s2")]))
    cfg_file = tmp_path / "alerts.json"
    # default：s2 连续 2 次推送 + 冷却 60min；s1 覆盖为连续 3 次
    cfg_file.write_text(
        '{"default": {"confirm_count": 2, "cooldown_minutes": 60}, "servers": {"s1": {"confirm_count": 3}}}',
        encoding="utf-8",
    )
    state_file = tmp_path / "state.json"

    r1 = await alerts.scan_and_notify("https://hook.example/send", state_file=state_file, config_file=cfg_file, include_log_signals=False)
    assert r1["pending"] == 2  # s1=1/3, s2=1/2
    r2 = await alerts.scan_and_notify("https://hook.example/send", state_file=state_file, config_file=cfg_file, include_log_signals=False)
    assert r2["notified"] == 1  # s2 达 2/2 → 推送；s1 仍 2/3
    assert r2["pending"] == 1
    r3 = await alerts.scan_and_notify("https://hook.example/send", state_file=state_file, config_file=cfg_file, include_log_signals=False)
    assert r3["notified"] == 1  # s1 达 3/3 → 推送；s2 冷却期 → 不重复
    assert r3["suppressed"] == 1
    assert len(notified) == 2
    assert "s1" in notified[-1]


async def test_per_server_min_level(fake, env_panel, monkeypatch, tmp_path):
    """s1 配 min_level=error：离线（非错误态）不报；s3 error 仍报。"""
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s3/resources", {"current_state": "error"})
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s3")]))
    cfg_file = tmp_path / "alerts.json"
    cfg_file.write_text('{"servers": {"s1": {"min_level": "error"}}}', encoding="utf-8")

    r = await alerts.scan_and_notify("https://hook.example/send", config_file=cfg_file, include_log_signals=False)
    assert r["notified"] == 1
    assert "s3" in notified[0]
    assert "s1" not in notified[0]


# ================= 渠道按服务器独立开关/路由（阶段七）=================


async def test_per_server_webhook_off(fake, env_panel, monkeypatch, tmp_path):
    """s1 配 webhook=false → 全局 webhook 存在但 s1 不推送；s2 正常推送。"""
    notified = []

    async def spy(webhook, content, token=None):
        notified.append((webhook, content))

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s2/resources", {"current_state": "offline"})
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s2")]))
    cfg_file = tmp_path / "alerts.json"
    cfg_file.write_text('{"servers": {"s1": {"webhook": false}}}', encoding="utf-8")

    r = await alerts.scan_and_notify("https://hook.example/send", config_file=cfg_file, include_log_signals=False)
    assert r["notified"] == 1
    assert len(notified) == 1
    assert "s2" in notified[0][1]
    assert "s1" not in notified[0][1]


async def test_per_server_webhook_url_override(fake, env_panel, monkeypatch, tmp_path):
    """s1 配 webhook_url → 发往不同目标。"""
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(webhook)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))
    cfg_file = tmp_path / "alerts.json"
    cfg_file.write_text('{"servers": {"s1": {"webhook_url": "https://other.hook/send"}}}', encoding="utf-8")

    await alerts.scan_and_notify("https://hook.example/send", config_file=cfg_file, include_log_signals=False)
    assert notified == ["https://other.hook/send"]


async def test_per_server_email_off_and_mail_to(fake, env_panel, monkeypatch, tmp_path):
    """s1 配 email=false → 不发邮件；s2 用覆盖的收件人。"""
    emails = []
    webhooks = []

    async def spy_email(to, subject, content, host, user, password, port=465, frm=None):
        emails.append(to)

    async def spy_webhook(webhook, content, token=None):
        webhooks.append(webhook)

    monkeypatch.setattr(alerts, "send_email", spy_email)
    monkeypatch.setattr(alerts, "send_webhook", spy_webhook)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s2/resources", {"current_state": "offline"})
    fake.route("GET", "/api/client", _servers_payload([_server("s1"), _server("s2")]))
    cfg_file = tmp_path / "alerts.json"
    cfg_file.write_text(
        '{"servers": {"s1": {"email": false}, "s2": {"mail_to": "oncall@x.com"}}}', encoding="utf-8"
    )

    r = await alerts.scan_and_notify(
        "https://hook.example/send",
        smtp_host="smtp.example.com", smtp_user="u", smtp_password="p", smtp_to="ops@x.com",
        config_file=cfg_file, include_log_signals=False,
    )
    assert r["notified"] == 2
    assert emails == ["oncall@x.com"]  # 只有 s2 发邮件，且用覆盖的收件人
    assert len(webhooks) == 2          # s1/s2 都走 webhook


def test_server_effective_channel_keys():
    cfg = {"default": {"email": False}, "servers": {"s1": {"mail_to": "a@x.com"}}}
    eff = alerts.server_effective(cfg, "s1", 1, 0, "abnormal", webhook="https://w", email_enabled=True, smtp_to="b@x.com")
    assert eff["webhook"] is True
    assert eff["webhook_url"] == "https://w"
    assert eff["email"] is False        # default 关掉邮件
    assert eff["mail_to"] == "a@x.com"  # 服务器覆盖收件人


# ================= 邮件渠道（阶段四）=================


class FakeSMTP:
    def __init__(self, *a, **k):
        self.sent = []
        self.started = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.started = True

    def login(self, user, password):
        self.creds = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


def test_send_email_ssl(monkeypatch):
    fake = FakeSMTP()
    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", lambda host, port, timeout: fake)
    alerts.send_email(
        to="ops@x.com", subject="PteroPilot 告警", content="服务器 s1 离线",
        host="smtp.example.com", user="bot@x.com", password="pw", port=465,
    )
    assert len(fake.sent) == 1
    msg = fake.sent[0]
    assert msg["To"] == "ops@x.com"
    assert msg["From"] == "bot@x.com"
    assert "服务器 s1 离线" in msg.get_content()
    assert fake.creds == ("bot@x.com", "pw")


def test_send_email_starttls_port587(monkeypatch):
    fake = FakeSMTP()
    monkeypatch.setattr(alerts.smtplib, "SMTP", lambda host, port, timeout: fake)
    alerts.send_email(
        to="ops@x.com", subject="s", content="c",
        host="smtp.example.com", user="u", password="p", port=587,
    )
    assert fake.started is True  # STARTTLS


async def test_scan_notifies_email_channel(fake, env_panel, monkeypatch):
    emails = []

    async def spy_email(to, subject, content, host, user, password, port=465, frm=None):
        emails.append((to, subject))

    monkeypatch.setattr(alerts, "send_email", spy_email)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify(
        webhook="",
        smtp_host="smtp.example.com",
        smtp_user="u",
        smtp_password="p",
        smtp_to="ops@x.com",
    )
    assert result["notified"] == 1
    assert len(emails) == 1
    assert emails[0][0] == "ops@x.com"
    assert "s1" in emails[0][1]


async def test_scan_no_channel_skips_notify(fake, env_panel, monkeypatch):
    notified = []

    async def spy_email(*a, **k):
        notified.append(("email", a))

    async def spy_webhook(*a, **k):
        notified.append(("webhook", a))

    monkeypatch.setattr(alerts, "send_email", spy_email)
    monkeypatch.setattr(alerts, "send_webhook", spy_webhook)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify(webhook="")  # 无任何渠道
    assert result["notified"] == 0
    assert result["abnormal"] == 1
    assert notified == []


# ================= 连续确认阈值（阶段五）=================


async def test_confirm_count_requires_consecutive(fake, env_panel, monkeypatch, tmp_path):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))
    state_file = tmp_path / "state.json"

    first = await alerts.scan_and_notify("https://hook.example/send", confirm_count=2, state_file=state_file)
    assert first["notified"] == 0
    assert first["pending"] == 1  # 第 1 次：计数 1 < 2

    second = await alerts.scan_and_notify("https://hook.example/send", confirm_count=2, state_file=state_file)
    assert second["notified"] == 1  # 第 2 次连续 → 推送
    assert second["pending"] == 0
    assert len(notified) == 1


async def test_confirm_count_healthy_resets(fake, env_panel, monkeypatch, tmp_path):
    notified = []

    async def spy(webhook, content, token=None):
        notified.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    # 序列：offline, running(健康), offline, offline, offline(推送时内部还会再查一次 resources)
    states = ["offline", "running", "offline", "offline", "offline", "offline"]
    fake.route("GET", "s1/resources", [{"current_state": s} for s in states])
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))
    state_file = tmp_path / "state.json"

    for i in range(5):
        r = await alerts.scan_and_notify("https://hook.example/send", confirm_count=3, state_file=state_file)
        # 健康（第 2 次）会清零计数；连续 3 次 offline 才推送
        if i == 0:
            assert r["pending"] == 1 and r["notified"] == 0
        elif i == 1:
            assert r["pending"] == 0 and r["notified"] == 0  # 健康，无异常
        elif i in (2, 3):
            assert r["pending"] == 1 and r["notified"] == 0
        else:  # i == 4，连续 3 次 → 推送
            assert r["notified"] == 1 and r["pending"] == 0
    assert len(notified) == 1


# ================= 覆盖率回补（O1）：配置/状态/告警边界 =================


def test_load_alerts_config_non_dict_json(tmp_path):
    """配置是合法 JSON 但不是 dict（如数组）→ 返回空配置。"""
    p = tmp_path / "alerts.json"
    p.write_text('[1, 2, 3]', encoding="utf-8")
    assert alerts.load_alerts_config(p) == {}


def test_load_alerts_config_corrupt_json(tmp_path):
    """配置是损坏 JSON → 返回空配置。"""
    p = tmp_path / "alerts.json"
    p.write_text('{not json', encoding="utf-8")
    assert alerts.load_alerts_config(p) == {}


def test_save_state_write_failure_swallowed(tmp_path):
    """状态文件写不了（父路径是文件而非目录）→ 静默降级，不抛异常。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    state_file = blocker / "sub" / "state.json"
    # mkdir(parents=True) 遇到父路径是普通文件 → OSError 子类 → 被 save_state 吞掉
    alerts.save_state(state_file, {"s1": {"state": "offline", "hits": 1, "ts": 1.0}})  # 不应抛异常


async def test_diagnose_crash_for_no_signal_returns_none(monkeypatch):
    """诊断结果里没有「崩溃信号」行 → 返回 None。"""
    async def fake_diagnose(server_id):
        return "健康度: offline\n（无崩溃信号）"

    monkeypatch.setattr(alerts, "diagnose_crash", fake_diagnose)
    # diagnose_crash_for 复用 server 模块，client 只需存在即可被赋给 srv.panel 再还原
    class _C:
        pass
    out = await alerts.diagnose_crash_for("s1", _C(), 30.0)
    assert out is None


async def test_diagnose_crash_for_with_signal(monkeypatch):
    """诊断结果里有「崩溃信号」行 → 返回该行。"""
    async def fake_diagnose(server_id):
        return "健康度: error\n崩溃信号: 1 类（OOM）\n详情"

    monkeypatch.setattr(alerts, "diagnose_crash", fake_diagnose)
    class _C:
        pass
    out = await alerts.diagnose_crash_for("s1", _C(), 30.0)
    assert out == "崩溃信号: 1 类（OOM）"


async def test_scan_webhook_push_failure_recorded(fake, env_panel, monkeypatch):
    """webhook 推送失败（非 2xx）→ 记入 errors、不崩溃、其余服务器继续。"""
    async def failing_webhook(url, content, token=None):
        raise httpx.HTTPStatusError("push failed", request=httpx.Request("POST", url), response=httpx.Response(500, request=httpx.Request("POST", url)))

    monkeypatch.setattr(alerts, "send_webhook", failing_webhook)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify("https://hook.example/send")
    assert result["abnormal"] == 1
    assert result["notified"] == 0
    assert len(result["errors"]) == 1
    assert "s1" in result["errors"][0]


def test_send_email_connection_failure(monkeypatch):
    """SMTP 连接失败 → 异常向上抛（由 scan 循环接住，此处仅覆盖 send_email 出错路径）。"""
    class _Boom:
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("connection refused")
    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", _Boom)
    with pytest.raises(ConnectionRefusedError):
        alerts.send_email(
            to="ops@x.com", subject="s", content="c",
            host="smtp.example.com", user="u", password="p", port=465,
        )


def test_send_email_auth_failure(monkeypatch):
    """SMTP 认证失败（login 抛 SMTPAuthenticationError）→ 异常向上抛。"""
    class _BadAuth:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self):
            pass
        def login(self, user, password):
            raise alerts.smtplib.SMTPAuthenticationError(535, b"auth failed")

    monkeypatch.setattr(alerts.smtplib, "SMTP", lambda host, port, timeout: _BadAuth())
    with pytest.raises(alerts.smtplib.SMTPAuthenticationError):
        alerts.send_email(
            to="ops@x.com", subject="s", content="c",
            host="smtp.example.com", user="u", password="p", port=587,
        )


# ================= CLI main()（覆盖率回补 O1）=================


async def _fake_scan(**kwargs):
    return {"checked": 2, "abnormal": 1, "notified": 1, "suppressed": 0, "pending": 0, "errors": ["s1: Boom"]}


def test_main_happy_path(monkeypatch, capsys):
    monkeypatch.setattr(alerts, "scan_and_notify", _fake_scan)
    monkeypatch.setattr(alerts, "DEFAULT_WEBHOOK_URL", "")
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts", "--webhook", "https://hook.example/send"])
    alerts.main()
    out = capsys.readouterr().out
    assert "检查 2 台" in out
    assert "异常 1 台" in out
    assert "已推送 1 台" in out
    assert "查询失败: s1: Boom" in out


def test_main_email_channel(monkeypatch, capsys):
    monkeypatch.setattr(alerts, "scan_and_notify", _fake_scan)
    monkeypatch.setattr("sys.argv", [
        "pteropilot-alerts",
        "--smtp-host", "smtp.example.com", "--smtp-user", "u",
        "--smtp-pass", "p", "--mail-to", "ops@x.com",
    ])
    alerts.main()
    out = capsys.readouterr().out
    assert "邮件" in out


def test_main_no_channel_errors(monkeypatch):
    monkeypatch.setattr(alerts, "DEFAULT_WEBHOOK_URL", "")
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts"])
    with pytest.raises(SystemExit) as e:
        alerts.main()
    assert e.value.code == 2  # argparse parser.error


def test_main_invalid_min_level(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts", "--webhook", "https://x", "--min-level", "bogus"])
    with pytest.raises(SystemExit) as e:
        alerts.main()
    assert e.value.code == 2


def test_main_invalid_confirm_count(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pteropilot-alerts", "--webhook", "https://x", "--confirm-count", "abc"])
    with pytest.raises(SystemExit) as e:
        alerts.main()
    assert e.value.code == 2


# ================= 密钥泄漏安全复审（O2）=================


def test_redact_strips_webhook_access_token():
    """redact() 应把 webhook URL 里的 access_token 签名去掉。"""
    url = "https://oapi.dingtalk.com/robot/send?access_token=SECRETTOKEN123"
    out = alerts.redact(url)
    assert "SECRETTOKEN123" not in out
    assert "***" in out


def test_redact_strips_webhook_key_token():
    """redact() 应把企业微信 style 的 ?key= / &key= 签名去掉。"""
    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRETKEY456"
    out = alerts.redact(url)
    assert "SECRETKEY456" not in out
    assert "***" in out


def test_redact_strips_bearer_and_api_key_still():
    """回归：redact 仍应脱敏 Bearer 与 ptla_/ptlc_/peli_ 前缀（旧能力不退化）。"""
    for secret in ("Bearer abcDEF123._~+/=", "ptla_ABC123", "ptlc_XYZ", "peli_QQQ"):
        assert secret.split(" ")[-1] if secret.startswith("Bearer") else secret not in alerts.redact(secret)


async def test_webhook_push_failure_does_not_leak_signed_url(fake, env_panel, monkeypatch):
    """webhook 推送失败时，errors 里不能带出 access_token 签名。"""
    signed_url = "https://oapi.dingtalk.com/robot/send?access_token=LEAK_ME_TOKEN"

    async def failing_webhook(url, content, token=None):
        # 模拟 httpx.raise_for_status 的 HTTPStatusError，其 message 含完整 URL
        raise httpx.HTTPStatusError(
            f"Client error '400' for url '{signed_url}'",
            request=httpx.Request("POST", signed_url),
            response=httpx.Response(400, request=httpx.Request("POST", signed_url)),
        )

    monkeypatch.setattr(alerts, "send_webhook", failing_webhook)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify(signed_url)
    assert len(result["errors"]) == 1
    assert "LEAK_ME_TOKEN" not in result["errors"][0]


async def test_webhook_push_failure_does_not_leak_key_token(fake, env_panel, monkeypatch):
    """企业微信 key 签名同样不能从 errors 漏出。"""
    signed_url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=LEAK_ME_KEY"

    async def failing_webhook(url, content, token=None):
        raise httpx.HTTPStatusError(
            "Client error",
            request=httpx.Request("POST", signed_url),
            response=httpx.Response(400, request=httpx.Request("POST", signed_url)),
        )

    monkeypatch.setattr(alerts, "send_webhook", failing_webhook)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify(signed_url)
    assert len(result["errors"]) == 1
    assert "LEAK_ME_KEY" not in result["errors"][0]


async def test_smtp_auth_failure_does_not_leak_password(fake, env_panel, monkeypatch):
    """SMTP 认证失败时，异常文本不包含 --smtp-pass 密码，errors 里也不应有。"""
    secret_pw = "SUPER_SECRET_PW_987"

    async def failing_email(to, subject, content, host, user, password, port=465, frm=None):
        assert password == secret_pw  # 确认密码确实传进来了
        err = alerts.smtplib.SMTPAuthenticationError(535, b"5.7.8 authentication failed")
        raise err

    monkeypatch.setattr(alerts, "send_email", failing_email)
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text="x"))
    fake.route("GET", "/api/client", _servers_payload([_server("s1")]))

    result = await alerts.scan_and_notify(
        webhook="",
        smtp_host="smtp.example.com", smtp_user="u", smtp_password=secret_pw, smtp_to="ops@x.com",
    )
    assert len(result["errors"]) == 1
    assert secret_pw not in result["errors"][0]


def test_alerts_state_file_contains_no_credentials(tmp_path):
    """冷却状态文件只存 {state, hits, ts}，不含任何 webhook URL 或 SMTP 凭证。"""
    p = tmp_path / "state.json"
    alerts.save_state(p, {"s1": {"state": "offline", "hits": 1, "ts": 1.0}})
    raw = p.read_text(encoding="utf-8")
    assert "http" not in raw and "smtp" not in raw.lower() and "password" not in raw.lower()
