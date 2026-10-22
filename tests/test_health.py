"""check_server_health（崩服告警检测点）工具测试。

只读工具，对比 current_state 翻译健康等级 + 中文提示 + 建议动作。
覆盖：healthy / transitional / abnormal / unknown 各等级，以及工具层输出。
"""

from __future__ import annotations

import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


# ================= classify_state 纯函数 =================


def test_classify_running():
    assert srv.classify_state("running") == ("healthy", "服务器运行正常。")


def test_classify_starting():
    assert srv.classify_state("starting") == ("transitional", "服务器正在启动，稍候即就绪。")


def test_classify_offline():
    level, hint = srv.classify_state("offline")
    assert level == "abnormal"
    assert "离线" in hint


def test_classify_error():
    level, hint = srv.classify_state("error")
    assert level == "abnormal"
    assert "错误状态" in hint


def test_classify_suspended():
    level, hint = srv.classify_state("suspended")
    assert level == "abnormal"
    assert "挂起" in hint


def test_classify_unknown():
    level, hint = srv.classify_state("weird-state")
    assert level == "unknown"
    assert "weird-state" in hint


# ================= 工具层 check_server_health =================


async def test_health_running(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/resources",
        {"attributes": {"current_state": "running", "resources": {}}},
    )
    out = await srv.check_server_health("s1")
    assert "running" in out and "🟢 正常" in out and "无需干预" in out


async def test_health_offline(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/resources",
        {"attributes": {"current_state": "offline", "resources": {}}},
    )
    out = await srv.check_server_health("s1")
    assert "🔴 异常" in out and "离线" in out and "get_console_log" in out


async def test_health_transitional(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/resources",
        {"attributes": {"current_state": "stopping", "resources": {}}},
    )
    out = await srv.check_server_health("s1")
    assert "🟡 中间态" in out and "停机" in out


async def test_health_unknown(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/resources",
        {"attributes": {"current_state": "totally-new", "resources": {}}},
    )
    out = await srv.check_server_health("s1")
    assert "⚪ 未知" in out and "totally-new" in out


async def test_health_missing_state(fake):
    # 面板不返回 current_state 时回退为「?」→ unknown
    _bind(fake)
    fake.route("GET", "/resources", {"attributes": {"resources": {}}})
    out = await srv.check_server_health("s1")
    assert "⚪ 未知" in out and "?" in out