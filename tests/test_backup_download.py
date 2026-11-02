"""get_backup_download 工具测试：正常返回链接 / 空链接提示 / 端点路径正确。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.get_backup_download(*args, **kwargs)

    return asyncio.run(run())


def test_get_backup_download_returns_url(make_client):
    make_client[2].route(
        "GET", "/backups/bkp-1/download",
        httpx.Response(200, json={"attributes": {"url": "https://dl.example/bkp-1.tar.gz"}}),
    )
    out = _call(make_client, "srv1", "bkp-1")
    assert "https://dl.example/bkp-1.tar.gz" in out
    assert "一次性" in out


def test_get_backup_download_no_url(make_client):
    # 端点返回但无 url 字段 → 明确提示，而不是报错
    make_client[2].route("GET", "/backups/bkp-2/download", httpx.Response(200, json={"attributes": {}}))
    out = _call(make_client, "srv1", "bkp-2")
    assert "未获取到下载链接" in out


def test_get_backup_download_path_shape(make_client):
    # 验证请求路径包含 server 与 backup id
    make_client[2].route(
        "GET", "/backups/bkp-3/download",
        httpx.Response(200, json={"attributes": {"url": "https://dl.example/x"}}),
    )
    _call(make_client, "srv-abc", "bkp-3")
    req = make_client[2].requests[-1]
    assert "/servers/srv-abc/backups/bkp-3/download" in req.url.path
