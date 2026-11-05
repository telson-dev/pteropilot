"""check_panel_connection 工具测试：未配置 / 401 提示 / 连通成功 / 密钥角色提示。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client):
    async def run():
        return await server.check_panel_connection()

    return asyncio.run(run())


def _servers_payload():
    return {
        "data": [
            {"attributes": {"identifier": "abc", "name": "生存服"}},
            {"attributes": {"identifier": "def", "name": "创造服"}},
        ],
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": 2}},
    }


def test_check_no_url(make_client, monkeypatch):
    monkeypatch.setattr(server.config, "panel_url", "")
    out = _call(make_client)
    assert "未配置面板地址" in out


def test_check_401_hint(make_client, monkeypatch):
    monkeypatch.setattr(server.config, "panel_url", "https://panel.test")
    make_client[2].route("GET", "/api/client", httpx.Response(401, json={"errors": [{"detail": "Unauthenticated."}]}))
    out = _call(make_client)
    assert "连接失败" in out
    assert "401" in out and "API Key 无效" in out


def test_check_403_hint(make_client, monkeypatch):
    monkeypatch.setattr(server.config, "panel_url", "https://panel.test")
    make_client[2].route("GET", "/api/client", httpx.Response(403, json={"errors": [{"detail": "Forbidden"}]}))
    out = _call(make_client)
    assert "403" in out and "客户端密钥" in out


def test_check_success(make_client, monkeypatch):
    monkeypatch.setattr(server.config, "panel_url", "https://panel.test")
    make_client[2].route("GET", "/api/client", _servers_payload())
    out = _call(make_client)
    assert "已连通" in out and "2 台" in out
    assert "abc(生存服)" in out


def test_check_key_role_warning(make_client, monkeypatch):
    monkeypatch.setattr(server.config, "panel_url", "https://panel.test")
    monkeypatch.setattr(server.config, "panel_api_key", "ptla_fake_app_key")
    make_client[2].route("GET", "/api/client", _servers_payload())
    out = _call(make_client)
    assert "密钥角色提示" in out and "应用密钥" in out
