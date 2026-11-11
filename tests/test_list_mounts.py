"""list_mounts 测试：服务器挂载点（额外存储盘）。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _run(coro):
    return asyncio.run(coro)


def _mount(name="world-store", source="/mnt/world", target="/srv/world", ro=False, user_mountable=False):
    return {
        "object": "server_mount",
        "attributes": {
            "name": name,
            "source": source,
            "target": target,
            "read_only": ro,
            "user_mountable": user_mountable,
        },
    }


def test_list_mounts_basic(make_client):
    make_client[2].route(
        "GET", "/servers/s1/mounts",
        httpx.Response(200, json={"data": [_mount(), _mount("ro-store", "/mnt/ro", "/srv/ro", ro=True)]}),
    )
    out = _run(server.list_mounts("s1"))
    assert "共 2 个挂载点" in out
    assert "world-store" in out
    assert "/mnt/world" in out and "/srv/world" in out
    assert "可写" in out
    assert "只读" in out


def test_list_mounts_empty(make_client):
    make_client[2].route("GET", "/servers/s1/mounts", httpx.Response(200, json={"data": []}))
    out = _run(server.list_mounts("s1"))
    assert "没有挂载点" in out
    assert "mount" in out


def test_list_mounts_user_mountable_flag(make_client):
    make_client[2].route(
        "GET", "/servers/s1/mounts",
        httpx.Response(200, json={"data": [_mount(user_mountable=True)]}),
    )
    out = _run(server.list_mounts("s1"))
    assert "用户可自行挂载" in out


def test_list_mounts_missing_fields_tolerated(make_client):
    """面板返回字段缺失时不能崩。"""
    make_client[2].route("GET", "/servers/s1/mounts", httpx.Response(200, json={"data": [{"attributes": {}}]}))
    out = _run(server.list_mounts("s1"))
    assert "共 1 个挂载点" in out
