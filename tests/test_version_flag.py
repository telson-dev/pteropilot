"""`pteropilot --version` 输出测试：让用户能确认装的是哪一版、工具数对不对。

背景：PyPI 上的 pteropilot 曾长期停在 0.2.0（22 个工具），而仓库已到 0.2.22（40 个）。
用户 `uvx pteropilot` 装到旧版时，`--version` 里的工具数是唯一能立刻看出差异的信号。
"""

from __future__ import annotations

import pytest

from pteropilot import __version__
from pteropilot import server as server_mod


def _run_version(monkeypatch, capsys, argv):
    monkeypatch.setattr("sys.argv", ["pteropilot"] + argv)
    monkeypatch.delenv("PANEL_URL", raising=False)
    monkeypatch.delenv("PANEL_API_KEY", raising=False)
    server_mod.main()  # 不应抛异常，也不应要求面板配置
    return capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--version", "-V", "-v"])
def test_version_flags_print_version_and_tool_count(monkeypatch, capsys, flag):
    out = _run_version(monkeypatch, capsys, [flag])
    assert f"v{__version__}" in out
    assert "个 MCP 工具" in out


def test_version_does_not_require_panel_config(monkeypatch, capsys):
    """--version 不该因为没配 PANEL_URL 而报错退出。"""
    out = _run_version(monkeypatch, capsys, ["--version"])
    assert "启动失败" not in out
    assert "PANEL_URL" not in out


def test_version_reports_expected_tool_count(monkeypatch, capsys):
    out = _run_version(monkeypatch, capsys, ["--version"])
    count = server_mod._tool_count()
    assert count >= 30, f"工具数异常：{count}"
    assert str(count) in out


def test_tool_count_matches_registered_tools():
    """--version 显示的工具数必须与真实注册数一致（不能是写死的常量）。"""
    assert server_mod._tool_count() == len(server_mod.mcp._tool_manager._tools)


def test_version_warns_when_tool_count_looks_old(monkeypatch, capsys):
    """工具数明显偏少时应提示可能装了旧版（PyPI 可能滞后）。"""
    monkeypatch.setattr(server_mod, "_tool_count", lambda: 5)
    monkeypatch.setattr("sys.argv", ["pteropilot", "--version"])
    monkeypatch.delenv("PANEL_URL", raising=False)
    monkeypatch.delenv("PANEL_API_KEY", raising=False)
    server_mod.main()
    out, err = capsys.readouterr()
    assert "pteropilot v" in out
    assert "5 个 MCP 工具" in out
    assert "旧版本" in err


def test_version_no_warning_when_count_normal(monkeypatch, capsys):
    monkeypatch.setattr(server_mod, "_tool_count", lambda: 40)
    monkeypatch.setattr("sys.argv", ["pteropilot", "--version"])
    monkeypatch.delenv("PANEL_URL", raising=False)
    monkeypatch.delenv("PANEL_API_KEY", raising=False)
    server_mod.main()
    out, err = capsys.readouterr()
    assert "旧版本" not in err
