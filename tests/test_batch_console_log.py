"""batch_get_console_log 工具测试：批量读取、截断、单台失败不阻断、空列表。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.batch_get_console_log(*args, **kwargs)

    return asyncio.run(run())


def test_batch_get_console_log_all_ok(make_client):
    fake = make_client[2]
    fake.route("GET", "/servers/srv-a/console", httpx.Response(200, text="[12:00] boot ok"))
    fake.route("GET", "/servers/srv-b/console", httpx.Response(200, text="[12:01] crash!"))
    out = _call(make_client, ["srv-a", "srv-b"])
    assert "### srv-a" in out and "[12:00] boot ok" in out
    assert "### srv-b" in out and "[12:01] crash!" in out
    # 顺序与输入一致
    assert out.index("### srv-a") < out.index("### srv-b")


def test_batch_get_console_log_truncates(make_client):
    fake = make_client[2]
    fake.route("GET", "/servers/srv-a/console", httpx.Response(200, text="x" * 5000))
    out = _call(make_client, ["srv-a"], max_chars=1000)
    assert "已截断，仅显示末尾 1000 字符" in out
    part = out.split("### srv-a")[1].strip()
    assert len(part) < 1050  # 约 1000 字符 + 截断标记（含分隔符余量）


def test_batch_get_console_log_partial_failure(make_client):
    fake = make_client[2]
    fake.route("GET", "/servers/srv-a/console", httpx.Response(200, text="ok"))
    # srv-b 无路由 → 404 → 单台失败不阻断
    out = _call(make_client, ["srv-a", "srv-b"])
    assert "### srv-a" in out and "ok" in out
    assert "### srv-b" in out and "读取失败" in out


def test_batch_get_console_log_empty(make_client):
    out = _call(make_client, [])
    assert "未指定任何服务器" in out


def _servers_payload(data):
    return {
        "data": data,
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(data)}},
    }


def test_batch_get_console_log_star_all_servers(make_client):
    fake = make_client[2]
    # console 路由先注册，避免被 /api/client 前缀路由遮蔽
    fake.route("GET", "/servers/srv-a/console", httpx.Response(200, text="log-a"))
    fake.route("GET", "/servers/srv-b/console", httpx.Response(200, text="log-b"))
    fake.route("GET", "/api/client", _servers_payload([
        {"attributes": {"identifier": "srv-a", "name": "A"}},
        {"attributes": {"identifier": "srv-b", "name": "B"}},
    ]))
    out = _call(make_client, ["*"])
    assert "### srv-a" in out and "log-a" in out
    assert "### srv-b" in out and "log-b" in out


def test_batch_get_console_log_star_no_servers(make_client):
    fake = make_client[2]
    fake.route("GET", "/api/client", _servers_payload([]))
    out = _call(make_client, ["*"])
    assert "没有可访问的服务器" in out
