"""面板地址规范化测试。

用户最常犯的配置错误之一：把面板 API 文档里的完整路径（…/api）粘进 PANEL_URL，
结果客户端拼成 /api/api/client → 404，看起来像"密钥错了"，实际是地址多了后缀。
"""

from __future__ import annotations

import pytest

from pteropilot.config import Config, normalize_panel_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://panel.example.com", "https://panel.example.com"),
        ("https://panel.example.com/", "https://panel.example.com"),
        ("https://panel.example.com/api", "https://panel.example.com"),
        ("https://panel.example.com/api/", "https://panel.example.com"),
        ("https://panel.example.com/api/client", "https://panel.example.com"),
        ("https://panel.example.com/api/client/", "https://panel.example.com"),
        ("https://panel.example.com/api/application", "https://panel.example.com"),
        ("  https://panel.example.com/api  ", "https://panel.example.com"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_strips_api_suffixes(raw, expected):
    assert normalize_panel_url(raw) == expected


def test_normalize_keeps_real_subpath():
    """真实部署在子路径（如反向代理 /pterodactyl）时必须保留。"""
    assert normalize_panel_url("https://example.com/pterodactyl") == "https://example.com/pterodactyl"
    assert normalize_panel_url("https://example.com/pterodactyl/") == "https://example.com/pterodactyl"


def test_normalize_case_insensitive_suffix():
    assert normalize_panel_url("https://panel.example.com/API") == "https://panel.example.com"
    assert normalize_panel_url("https://panel.example.com/Api/Client") == "https://panel.example.com"


def test_config_field_is_normalized(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com/api/")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_test")
    assert Config().panel_url == "https://panel.example.com"


def test_config_keeps_plain_url(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_test")
    assert Config().panel_url == "https://panel.example.com"
