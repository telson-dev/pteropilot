"""双面板（Pterodactyl/Pelican）兼容相关测试。

覆盖：应用密钥回退（PANEL_APP_KEY 优先，否则用 PANEL_API_KEY）、
密钥前缀诊断（客户端/应用密钥分开，单 key 场景提示）。"""

from __future__ import annotations

import os

import httpx
import pytest

from pteropilot.client import PanelClient
from pteropilot.config import Config, key_role_warnings


def test_app_key_fallback_to_api_key():
    cfg = Config(panel_url="https://panel", panel_api_key="ptlc_k", panel_app_key="")
    pc = PanelClient(cfg)
    try:
        assert pc.app._inner.headers["Authorization"] == "Bearer ptlc_k"
        assert pc.client._inner.headers["Authorization"] == "Bearer ptlc_k"
    finally:
        import asyncio

        asyncio.run(pc.aclose())


def test_app_key_used_for_application_api():
    cfg = Config(panel_url="https://panel", panel_api_key="ptlc_k", panel_app_key="ptla_a")
    pc = PanelClient(cfg)
    try:
        assert pc.app._inner.headers["Authorization"] == "Bearer ptla_a"
        assert pc.client._inner.headers["Authorization"] == "Bearer ptlc_k"
    finally:
        import asyncio

        asyncio.run(pc.aclose())


def test_key_role_warnings_none_when_both_set():
    cfg = Config(panel_url="p", panel_api_key="ptlc_k", panel_app_key="ptla_a")
    assert key_role_warnings(cfg) == []


def test_key_role_warnings_client_key_without_app_key():
    cfg = Config(panel_url="p", panel_api_key="ptlc_k", panel_app_key="")
    ws = key_role_warnings(cfg)
    assert any("PANEL_APP_KEY" in w for w in ws)


def test_key_role_warnings_app_key_as_api_key():
    cfg = Config(panel_url="p", panel_api_key="ptla_a", panel_app_key="")
    ws = key_role_warnings(cfg)
    assert any("客户端 API" in w for w in ws)


def test_key_role_warnings_app_key_has_client_prefix():
    cfg = Config(panel_url="p", panel_api_key="ptlc_k", panel_app_key="pacc_b")
    ws = key_role_warnings(cfg)
    assert any("PANEL_APP_KEY" in w for w in ws)


def test_key_role_warnings_unknown_prefix():
    cfg = Config(panel_url="p", panel_api_key="nonsense")
    ws = key_role_warnings(cfg)
    assert any("前缀无法识别" in w for w in ws)


def test_config_env_reads_panel_app_key(monkeypatch):
    monkeypatch.setenv("PANEL_APP_KEY", "ptla_from_env")
    cfg = Config()
    assert cfg.panel_app_key == "ptla_from_env"
