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
