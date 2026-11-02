"""broadcast_message（批量广播）工具测试。

高危写操作：只读拒绝 + 必须 confirm=True；"*" 表示全部服务器；
单台失败不阻断其余；路由按 PanelClient.send_command 的实际请求注册。
"""

from __future__ import annotations

import json

import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


def _posted(fake, path_contains: str) -> list[dict]:
    """按路径关键字取该端点的全部 POST 请求体。"""
    hits = [r for r in fake.requests if r.method == "POST" and path_contains in str(r.url.path)]
    return [json.loads(r.content) for r in hits]


def _no_post(fake, path_contains: str) -> bool:
    return not any(r.method == "POST" and path_contains in str(r.url.path) for r in fake.requests)


def _servers_payload(data: list[dict]) -> dict:
    return {
        "data": data,
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(data)}},
    }


def _server(identifier: str) -> dict:
    return {"attributes": {"identifier": identifier, "name": identifier}}


async def test_broadcast_readonly_denied(fake):
    _bind(fake, readonly=True)
    out = await srv.broadcast_message(["*"], "维护", confirm=True)
    assert "只读模式" in out and "已拒绝" in out


async def test_broadcast_requires_confirm(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([_server("abc")]))
    out = await srv.broadcast_message(["*"], "维护")
    assert "高危操作" in out and "confirm=True" in out
    assert _no_post(fake, "command")  # 未发送


async def test_broadcast_empty_message(fake):
    _bind(fake)
    out = await srv.broadcast_message(["*"], "   ", confirm=True)
    assert "消息不能为空" in out


async def test_broadcast_no_servers(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([]))
    out = await srv.broadcast_message(["*"], "维护", confirm=True)
    assert "没有可访问的服务器" in out


async def test_broadcast_all_servers(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([_server("abc"), _server("def")]))
    fake.route("POST", "abc/command", {})
    fake.route("POST", "def/command", {})
    out = await srv.broadcast_message(["*"], "维护", confirm=True)
    assert "已广播到 2 台" in out
    assert "abc" in out and "def" in out
    assert _posted(fake, "abc/command") == [{"command": "say 维护"}]
    assert _posted(fake, "def/command") == [{"command": "say 维护"}]


async def test_broadcast_specific_ids(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([_server("abc"), _server("def")]))
    fake.route("POST", "abc/command", {})
    out = await srv.broadcast_message(["abc"], "hi", confirm=True)
    assert "已广播到 1 台" in out and "abc" in out and "def" not in out
    assert _no_post(fake, "def/command")


async def test_broadcast_no_matching_target(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([_server("abc")]))
    out = await srv.broadcast_message(["nope"], "hi", confirm=True)
    assert "指定的服务器都不存在或无权限" in out


async def test_broadcast_partial_failure(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([_server("abc"), _server("def")]))
    fake.route("POST", "abc/command", {})
    # def 不发路由 → 请求 404 → 单台失败不阻断
    out = await srv.broadcast_message(["*"], "hi", confirm=True)
    assert "已广播到 1 台" in out and "abc" in out
    assert "失败 1 台" in out and "def" in out


async def test_broadcast_skips_server_without_id(fake):
    _bind(fake)
    # 一台缺 identifier/id（无 sid）→ 跳过，不参与目标列表
    fake.route(
        "GET", "/api/client",
        _servers_payload([
            _server("abc"),
            {"attributes": {"name": "no-id"}},
        ]),
    )
    fake.route("POST", "abc/command", {})
    out = await srv.broadcast_message(["*"], "hi", confirm=True)
    assert "已广播到 1 台" in out and "abc" in out
    assert "no-id" not in out
