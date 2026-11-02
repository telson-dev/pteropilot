"""list_my_servers 增强测试：默认列表 / with_status 并发状态 / status 过滤 / 状态拉取失败兜底。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.list_my_servers(*args, **kwargs)

    return asyncio.run(run())


def _servers():
    return {
        "data": [
            {"attributes": {"identifier": "abc", "name": "生存服", "uuid": "u1"}},
            {"attributes": {"identifier": "def", "name": "创造服", "uuid": "u2"}},
        ]
    }


def test_list_my_servers_default(make_client):
    make_client[2].route("GET", "/api/client", _servers())
    out = _call(make_client)
    assert "- abc | 生存服 | u1" in out
    assert "- def | 创造服 | u2" in out
    assert "状态" not in out  # 默认不带状态列


def test_list_my_servers_with_status(make_client):
    fake = make_client[2]
    # resources 路由先注册（否则会被 /api/client 前缀路由遮蔽）
    fake.route("GET", "/servers/abc/resources", httpx.Response(200, json={"current_state": "running"}))
    fake.route("GET", "/servers/def/resources", httpx.Response(200, json={"current_state": "offline"}))
    fake.route("GET", "/api/client", _servers())
    out = _call(make_client, with_status=True)
    assert "- abc | 生存服 | u1 | running" in out
    assert "- def | 创造服 | u2 | offline" in out


def test_list_my_servers_status_filter(make_client):
    fake = make_client[2]
    fake.route("GET", "/servers/abc/resources", httpx.Response(200, json={"current_state": "running"}))
    fake.route("GET", "/servers/def/resources", httpx.Response(200, json={"current_state": "offline"}))
    fake.route("GET", "/api/client", _servers())
    out = _call(make_client, status="running", with_status=True)
    assert "状态过滤: running" in out
    assert "abc" in out and "def" not in out  # 只保留 running


def test_list_my_servers_status_filter_no_match(make_client):
    fake = make_client[2]
    fake.route("GET", "/servers/abc/resources", httpx.Response(200, json={"current_state": "offline"}))
    fake.route("GET", "/servers/def/resources", httpx.Response(200, json={"current_state": "offline"}))
    fake.route("GET", "/api/client", _servers())
    out = _call(make_client, status="running", with_status=True)
    assert "没有状态为 running 的服务器" in out


def test_list_my_servers_state_failure_tolerated(make_client):
    fake = make_client[2]
    fake.route("GET", "/servers/abc/resources", httpx.Response(200, json={"current_state": "running"}))
    fake.route("GET", "/api/client", _servers())
    # def 无 resources 路由 → 404 → 状态显示 "?"，不阻断
    out = _call(make_client, with_status=True)
    assert "abc | 生存服 | u1 | running" in out
    assert "def | 创造服 | u2 | ?" in out
