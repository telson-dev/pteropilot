"""live-smoke 集成测试：用真实 HTTP 面板模拟器（uvicorn 起本地服务）跑一遍。

这是套件里第一次走「真 HTTP 全栈」（PanelClient → httpx → 真实服务端），
验证 live-smoke.py 的探测逻辑与端点路径，不只是 MockTransport。
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根，导入 scripts

from pteropilot.config import Config
from scripts import live_smoke


def _free_port() -> int:
    """找一个当前空闲的本地端口，避免 CI/并行测试里硬编码端口被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_panel_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/client")
    async def client_servers():
        return {
            "data": [{"attributes": {"identifier": "s1", "name": "S1"}}],
            "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": 1}},
        }

    @app.get("/api/client/")  # httpx 空路径合并会带尾斜杠；真实面板（Laravel）两种都接受
    async def client_servers_slash():
        return await client_servers()

    @app.get("/api/client/servers/s1/resources")
    async def resources():
        return {"attributes": {"current_state": "running", "cpu": 1.0, "memory": 100}}

    @app.get("/api/client/servers/s1/console")
    async def console():
        return Response("line1\nline2\n")

    @app.get("/api/client/servers/s1/files/list")
    async def files():
        return {"attributes": {"files": [{"name": "a.txt", "size": 10}]}}

    @app.get("/api/client/servers/s1/backups")
    async def backups():
        return {"data": [{"attributes": {"uuid": "b1", "created_at": "2026-01-01T00:00:00Z"}}]}

    @app.get("/api/application/nodes")
    async def nodes():
        return {"data": [{"attributes": {"id": 1, "name": "node1"}}]}

    @app.get("/api/application/servers")
    async def servers():
        return {"data": [{"attributes": {"id": 1, "identifier": "s1"}}]}

    @app.get("/api/application/users")
    async def users():
        return {"data": [{"attributes": {"id": 1, "username": "u1"}}]}

    return app


@pytest.fixture(scope="module")
def base_url():
    """起一个本地 uvicorn 面板模拟器，返回其实际绑定的 base URL。"""
    port = _free_port()
    app = build_panel_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    # 等服务起来
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/api/client", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.2)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def test_live_smoke_all_endpoints_pass(base_url):
    cfg = Config(panel_url=base_url, panel_api_key="ptlc_k", panel_app_key="ptla_a")
    report = asyncio.run(live_smoke.run(cfg))
    assert "Pterodactyl" in report["panel_hint"]
    results = report["results"]
    assert len(results) >= 6
    for name, (ok, summary) in results.items():
        assert ok, f"{name} 应通过: {summary}"
    # 关键端点确实被探测到
    names = " ".join(results.keys())
    assert "resources" in names
    assert "console_log" in names
    assert "list_backups" in names
    assert "list_nodes" in names


def test_live_smoke_pelican_hint():
    cfg = Config(panel_url="http://127.0.0.1:0", panel_api_key="pacc_k", panel_app_key="papp_a")
    assert "Pelican" in live_smoke.panel_type_hint(cfg)


def test_live_smoke_app_key_as_client_warns():
    cfg = Config(panel_url="http://127.0.0.1:0", panel_api_key="ptla_a")
    assert "403" in live_smoke.panel_type_hint(cfg)
