"""list_databases 工具测试：数据库列表展示、空列表、字段兜底。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.list_databases(*args, **kwargs)

    return asyncio.run(run())


def _db(name="smp_db", user="smp_user", host="db.example.com", port=3306, conn="%"):
    return {
        "attributes": {
            "id": 1,
            "name": name,
            "username": user,
            "connections_from": conn,
            "host": {"host": host, "port": port},
        }
    }


def test_list_databases_single(make_client):
    make_client[2].route(
        "GET", "/servers/s1/databases", httpx.Response(200, json={"data": [_db()]})
    )
    out = _call(make_client, "s1")
    assert "共 1 个数据库" in out
    assert "smp_db" in out
    assert "db.example.com:3306" in out
    assert "smp_user" in out
    assert "来源限制 %" in out
    # 明确提示密码不在 API 范围
    assert "密码需到面板网页端查看" in out


def test_list_databases_multiple_and_custom_conn(make_client):
    make_client[2].route(
        "GET",
        "/servers/s1/databases",
        httpx.Response(
            200,
            json={"data": [_db(name="a_db", conn="10.0.0.1"), _db(name="b_db", user="b_user")]},
        ),
    )
    out = _call(make_client, "s1")
    assert "共 2 个数据库" in out
    assert "a_db" in out and "b_db" in out
    assert "来源限制 10.0.0.1" in out


def test_list_databases_empty(make_client):
    make_client[2].route("GET", "/servers/s1/databases", httpx.Response(200, json={"data": []}))
    out = _call(make_client, "s1")
    assert "没有分配数据库" in out


def test_list_databases_host_address_fallback(make_client):
    # 某些面板用 address 而不是 host
    make_client[2].route(
        "GET",
        "/servers/s1/databases",
        httpx.Response(
            200,
            json={"data": [{"attributes": {"name": "x", "host": {"address": "db2.example.com", "port": 3307}}}]},
        ),
    )
    out = _call(make_client, "s1")
    assert "db2.example.com:3307" in out
