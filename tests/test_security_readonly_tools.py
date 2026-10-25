"""三个新只读工具（list_players / check_server_health / batch_get_status）的安全复审测试。

K3 安全复审：确认它们①不触发任何写/危险端点（无读改旁路）、②错误路径不泄漏
API Key / 令牌、③返回结构不透出内部密钥或签名 URL。

注意：这三个工具都是纯只读（走 resources / console / client_servers 三个 GET 端点），
因此不需要 `_denied_if_readonly()` 门禁——只读模式下写操作才被拦。这里的关注点是
「确认它们永远不会落到写端点」以及「报错时密钥被 redact」。
"""

from __future__ import annotations

import httpx
import pytest

import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="ptlc_supersecret", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


# ================= ① 只读工具不触发任何写方法 =================


async def test_list_players_only_issues_get(fake):
    _bind(fake)
    fake.route("GET", "/console", httpx.Response(200, text="Steve joined the game\n"))
    await srv.list_players("s1")
    # 只应有一个 GET 请求，且路径是 console，不是任何写端点
    methods = {r.method for r in fake.requests}
    assert methods == {"GET"}
    assert all("console" in str(r.url.path) for r in fake.requests)


async def test_check_server_health_only_issues_get(fake):
    _bind(fake)
    fake.route("GET", "/resources", {"attributes": {"current_state": "running"}})
    await srv.check_server_health("s1")
    methods = {r.method for r in fake.requests}
    assert methods == {"GET"}


async def test_batch_get_status_only_issues_get(fake):
    _bind(fake)
    # resources 路由必须先于裸 /api/client 注册，否则被父路由吞掉（见 test_batch.py 顶部说明）
    fake.route("GET", "abc/resources", {"attributes": {"current_state": "running"}})
    fake.route("GET", "/api/client", {
        "data": [{"attributes": {"identifier": "abc", "name": "s", "uuid": "u1"}}],
        "meta": {"pagination": {"total_pages": 1}},
    })
    await srv.batch_get_status()
    methods = {r.method for r in fake.requests}
    assert methods == {"GET"}


# ================= ② 错误路径不泄漏密钥 =================


async def test_check_server_health_error_does_not_leak_key(fake):
    # 面板 500 时 _Guarded 会把错误 redact 后抛 PanelError，密钥不应出现在异常文本里
    _bind(fake)
    fake.route("GET", "/resources", httpx.Response(500, json={"errors": []}))
    with pytest.raises(Exception) as ei:
        await srv.check_server_health("s1")
    assert "supersecret" not in str(ei.value)


async def test_batch_get_status_failure_drops_error_detail(fake):
    # 单台失败时 batch 只标注「查询失败」，不把错误原文（可能含敏感信息）透给 AI
    _bind(fake)
    fake.route("GET", "bad/resources", httpx.Response(500, json={"errors": []}))
    fake.route("GET", "good/resources", {"attributes": {"current_state": "running"}})
    fake.route("GET", "/api/client", {
        "data": [
            {"attributes": {"identifier": "bad", "name": "故障服", "uuid": "u1"}},
            {"attributes": {"identifier": "good", "name": "正常服", "uuid": "u2"}},
        ],
        "meta": {"pagination": {"total_pages": 1}},
    })
    out = await srv.batch_get_status()
    assert "查询失败" in out
    # 不出现面板返回的错误正文或任何密钥
    assert "errors" not in out
    assert "supersecret" not in out


# ================= ③ 返回结构不透出内部令牌 / 签名 =================


async def test_list_players_no_token_in_output(fake):
    _bind(fake)
    fake.route("GET", "/console", httpx.Response(200, text="Steve joined the game\n"))
    out = await srv.list_players("s1")
    assert "ptlc_" not in out
    assert "Bearer" not in out
    assert "supersecret" not in out


async def test_batch_get_status_no_token_in_output(fake):
    _bind(fake)
    fake.route("GET", "abc/resources", {"attributes": {"current_state": "running"}})
    fake.route("GET", "/api/client", {
        "data": [{"attributes": {"identifier": "abc", "name": "s", "uuid": "u1"}}],
        "meta": {"pagination": {"total_pages": 1}},
    })
    out = await srv.batch_get_status()
    assert "ptlc_" not in out
    assert "Bearer" not in out
    assert "supersecret" not in out