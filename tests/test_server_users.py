"""list_server_users 测试：服务器子用户（邮箱 + 权限）。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _run(coro):
    return asyncio.run(coro)


def _user(email="a@x.com", username="alice", perms=None):
    return {
        "object": "server_user",
        "attributes": {
            "email": email,
            "username": username,
            "server_permissions": perms if perms is not None else [
                {"permission": "control.restart"},
                {"permission": "control.start"},
                {"permission": "file.read"},
            ],
        },
    }


def test_list_users_basic(make_client):
    make_client[2].route(
        "GET", "/servers/s1/users",
        httpx.Response(200, json={"data": [_user()]}),
    )
    out = _run(server.list_server_users("s1"))
    assert "共 1 个子用户" in out
    assert "a@x.com" in out
    assert "alice" in out
    assert "3 项" in out
    # 权限按大类聚合
    assert "control" in out
    assert "file" in out


def test_list_users_multiple(make_client):
    make_client[2].route(
        "GET", "/servers/s1/users",
        httpx.Response(200, json={"data": [_user(), _user("b@x.com", "bob", [])]}),
    )
    out = _run(server.list_server_users("s1"))
    assert "共 2 个子用户" in out
    assert "b@x.com" in out
    assert "无权限记录" in out


def test_list_users_empty(make_client):
    make_client[2].route("GET", "/servers/s1/users", httpx.Response(200, json={"data": []}))
    out = _run(server.list_server_users("s1"))
    assert "没有添加子用户" in out
    assert "Users" in out


def test_list_users_username_fallback(make_client):
    """没有 email 时用 username 显示。"""
    u = {"attributes": {"username": "onlyname", "server_permissions": []}}
    make_client[2].route("GET", "/servers/s1/users", httpx.Response(200, json={"data": [u]}))
    out = _run(server.list_server_users("s1"))
    assert "onlyname" in out
