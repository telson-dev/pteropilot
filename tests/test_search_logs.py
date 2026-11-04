"""search_server_logs 工具测试：命中（不区分大小写）、无命中、空关键字、limit 截断、行号正确。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.search_server_logs(*args, **kwargs)

    return asyncio.run(run())


def test_search_hit_case_insensitive(make_client):
    log = "[12:00:00] [Server thread/ERROR]: something failed\n[12:01:00] info\n[12:02:00] [Server thread/WARN]: another Error here\n"
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1", "error")
    assert "共 2 行" in out
    assert "1: [12:00:00]" in out and "3: [12:02:00]" in out
    assert "2: [12:01:00]" not in out


def test_search_no_match(make_client):
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text="all good\n"))
    out = _call(make_client, "srv1", "OutOfMemoryError")
    assert "没有匹配" in out


def test_search_empty_pattern(make_client):
    out = _call(make_client, "srv1", "   ")
    assert "不能为空" in out


def test_search_limit_truncates(make_client):
    log = "\n".join(f"line {i} error" for i in range(10))
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1", "error", limit=3)
    assert "仅显示前 3 行" in out
    body = out.splitlines()[1:]  # 跳过标题行
    assert len(body) == 3
    assert all("error" in line for line in body)
