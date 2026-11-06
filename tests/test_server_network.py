"""get_server_network 工具测试：主地址/附加端口展示、空分配、字段兜底。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.get_server_network(*args, **kwargs)

    return asyncio.run(run())


def test_network_allocations_primary_and_extra(make_client):
    make_client[2].route(
        "GET",
        "/servers/s1/network/allocations",
        httpx.Response(
            200,
            json={
                "data": [
                    {"attributes": {"ip": "1.2.3.4", "port": 25565, "is_primary": True, "notes": "主服"}},
                    {"attributes": {"ip": "1.2.3.5", "port": 25566, "is_primary": False}},
                ]
            },
        ),
    )
    out = _call(make_client, "s1")
    assert "共 2 个网络分配" in out
    assert "主地址: 1.2.3.4:25565" in out
    assert "- 1.2.3.4:25565 ⭐主地址（主服）" in out
    assert "- 1.2.3.5:25566" in out


def test_network_allocations_ip_alias_priority(make_client):
    # ip_alias 优先于 ip（面板里可设别名域名）
    make_client[2].route(
        "GET",
        "/servers/s1/network/allocations",
        httpx.Response(
            200,
            json={"data": [{"attributes": {"ip": "1.2.3.4", "ip_alias": "mc.example.com", "port": 25565, "is_primary": True}}]},
        ),
    )
    out = _call(make_client, "s1")
    assert "mc.example.com:25565" in out


def test_network_allocations_empty(make_client):
    make_client[2].route(
        "GET", "/servers/s1/network/allocations", httpx.Response(200, json={"data": []})
    )
    out = _call(make_client, "s1")
    assert "没有网络分配记录" in out
