"""双面板（Pterodactyl/Pelican）兼容相关测试。

覆盖：应用密钥回退（PANEL_APP_KEY 优先，否则用 PANEL_API_KEY）、
密钥前缀诊断（客户端/应用密钥分开，单 key 场景提示）。"""

from __future__ import annotations

import os
import sys

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


# ================= main() 启动路径覆盖 =================


def test_attrs_fallback_non_dict():
    import pteropilot.server as srv

    assert srv._attrs("not-a-dict") == {}


def test_server_main_valid_config_prints_warnings(monkeypatch, capsys):
    """有效配置下 main()：打印密钥角色提示（若有）并进入 mcp.run。"""
    import pteropilot.server as srv

    # main() 用的是模块级 config（导入时构建），直接替换为带客户端密钥的配置
    monkeypatch.setattr(srv, "config", Config(panel_url="https://panel", panel_api_key="ptlc_k"))
    monkeypatch.setattr(srv.mcp, "run", lambda: None)  # 不真正启动 MCP 服务
    srv.main()
    err = capsys.readouterr().err
    assert "PANEL_APP_KEY" in err  # 密钥角色提示打印


def test_alerts_main_runs_scan(monkeypatch, capsys):
    """alerts main()：解析参数并执行扫描（无 webhook 时提示需要渠道）。"""
    import pteropilot.alerts as alerts

    monkeypatch.setattr(sys, "argv", ["pteropilot-alerts"])
    with pytest.raises(SystemExit) as exc:
        alerts.main()
    assert exc.value.code == 2  # argparse parser.error 退出码
    err = capsys.readouterr().err
    assert "webhook" in err or "SMTP" in err
