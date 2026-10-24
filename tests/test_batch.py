"""batch_get_status（批量开服骨架）工具测试。

只读工具，遍历我可访问服务器返回逐台「标识 | 名称 | 状态 | 健康等级」摘要。
覆盖：空列表、全成功、部分失败（单台查询抛异常不阻断整体）、identifier 缺失回退 uuid。

注意路由注册顺序：client_servers 端点路径是裸 `/api/client`，而其 `contains` 关键字
`/api/client` 也会命中每台服务器的 resources 路径 `/api/client/servers/<id>/resources`。
因此 resources 路由必须注册在 `/api/client` 之前，否则会被父路由吞掉。
"""

from __future__ import annotations

import httpx
import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=False)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


def _servers_payload(data: list[dict]) -> dict:
    """构造 client_servers 分页端点返回体。"""
    return {
        "data": data,
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(data)}},
    }


async def test_batch_empty(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([]))
    out = await srv.batch_get_status()
    assert "没有可访问的服务器" in out


async def test_batch_all_success(fake):
    _bind(fake)
    fake.route("GET", "abc/resources", {"attributes": {"current_state": "running"}})
    fake.route("GET", "def/resources", {"attributes": {"current_state": "offline"}})
    fake.route(
        "GET",
        "/api/client",
        _servers_payload(
            [
                {"attributes": {"identifier": "abc", "name": "生存服", "uuid": "u1"}},
                {"attributes": {"identifier": "def", "name": "创造服", "uuid": "u2"}},
            ]
        ),
    )

    out = await srv.batch_get_status()

    assert "共 2 台服务器" in out
    assert "成功 2 台、失败 0 台" in out
    assert "abc | 生存服 | running | 🟢" in out
    assert "def | 创造服 | offline | 🔴" in out


async def test_batch_partial_failure(fake):
    _bind(fake)
    fake.route("GET", "ok1/resources", {"attributes": {"current_state": "running"}})
    # bad 这台 resources 抛异常（面板 500），不阻断整体
    fake.route("GET", "bad/resources", httpx.Response(500, json={"errors": []}))
    fake.route("GET", "ok2/resources", {"attributes": {"current_state": "starting"}})
    fake.route(
        "GET",
        "/api/client",
        _servers_payload(
            [
                {"attributes": {"identifier": "ok1", "name": "正常服", "uuid": "u1"}},
                {"attributes": {"identifier": "bad", "name": "故障服", "uuid": "u2"}},
                {"attributes": {"identifier": "ok2", "name": "正常服2", "uuid": "u3"}},
            ]
        ),
    )

    out = await srv.batch_get_status()

    assert "共 3 台服务器" in out
    assert "成功 2 台、失败 1 台" in out
    assert "ok1 | 正常服 | running | 🟢" in out
    assert "bad | 故障服 | 查询失败" in out
    assert "ok2 | 正常服2 | starting | 🟡" in out


async def test_batch_identifer_fallback_uuid(fake):
    # 无 identifier 的服务器回退用 uuid 定位/展示
    _bind(fake)
    fake.route("GET", "uuid-only/resources", {"attributes": {"current_state": "running"}})
    fake.route(
        "GET",
        "/api/client",
        _servers_payload([{"attributes": {"name": "无名服", "uuid": "uuid-only"}}]),
    )
    out = await srv.batch_get_status()
    assert "uuid-only | 无名服 | running | 🟢" in out