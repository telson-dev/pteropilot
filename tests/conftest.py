"""测试夹具：用 httpx MockTransport 构造面板客户端，不触网。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pteropilot.client import PanelClient
from pteropilot.config import Config


class FakePanel:
    """记录请求并按 (method, path 关键字) 返回预置响应。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: list[tuple[str, str, Any]] = []

    def route(self, method: str, contains: str, response: Any) -> None:
        self.routes.append((method.upper(), contains, response))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, contains, response in self.routes:
            if request.method == method and contains in str(request.url.path):
                # 列表 = 按调用顺序依次返回
                if isinstance(response, list):
                    if not response:
                        continue
                    response = response.pop(0)
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, httpx.Response):
                    return response
                return httpx.Response(200, json=response)
        return httpx.Response(404, json={"errors": [{"detail": f"no route for {request.method} {request.url}"}]})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePanel:
    fake_panel = FakePanel()
    transport = httpx.MockTransport(fake_panel.handler)

    def fake_init(self: PanelClient, config: Config, timeout: float = 30.0) -> None:
        headers = {"Authorization": "Bearer test", "Accept": "application/json"}
        self.app = httpx.AsyncClient(transport=transport, base_url="https://panel/api/application", headers=headers)
        self.client = httpx.AsyncClient(transport=transport, base_url="https://panel/api/client", headers=headers)

    monkeypatch.setattr(PanelClient, "__init__", fake_init)

    async def fake_aclose(self: PanelClient) -> None:
        await self.app.aclose()
        await self.client.aclose()

    monkeypatch.setattr(PanelClient, "aclose", fake_aclose)
    return fake_panel


@pytest.fixture
def make_client(fake: FakePanel):
    import pteropilot.server as server_module

    cfg = Config(panel_url="https://panel", panel_api_key="test", readonly=False)
    client = PanelClient(cfg)
    # 让 server 模块使用这个客户端实例
    server_module.panel = client
    server_module.config = cfg
    yield client, cfg, fake
