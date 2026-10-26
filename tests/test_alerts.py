"""pteropilot-alerts（崩服告警通知通道）测试。

只读：扫描只用 GET；通知通过 webhook（测试里用 spy / 假 HTTP 捕获）。
"""

from __future__ import annotations

import os
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
