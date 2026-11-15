"""``pteropilot --help``：命令行速查。

以前的痛点：用户拿到命令后第一个敲 ``pteropilot --help``，程序会走进启动
流程、报「需要设置 PANEL_URL」并退出 1——最糟糕的第一印象。
"""

from __future__ import annotations

import pytest

from pteropilot import server


@pytest.fixture
def no_config(monkeypatch):
    """刻意不给任何面板配置：help 必须在这种状态下也能正常工作。"""
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    return monkeypatch


@pytest.mark.parametrize("flag", ["--help", "-h", "help"])
def test_help_works_without_config(no_config, capsys, flag: str) -> None:
    no_config.setattr(server.sys, "argv", ["pteropilot", flag])
    server.main()          # 不该抛 SystemExit(1)，也不该真的启动 server
    out = capsys.readouterr().out
    assert "pteropilot v" in out
    assert "--doctor" in out


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_exits_zero(no_config, flag: str) -> None:
    """main() 里 help 分支是 return，不是 exit(1)。"""
    no_config.setattr(server.sys, "argv", ["pteropilot", flag])
    server.main()          # 只是 return，不抛异常


def test_help_lists_every_command(capsys) -> None:
    server._print_help()
    out = capsys.readouterr().out
    for flag in ("--help", "--version", "--doctor", "--status", "--watch",
                 "--log", "--players", "--broadcast", "--power"):
        assert flag in out, f"帮助里漏了 {flag}"


def test_help_documents_env_vars(capsys) -> None:
    server._print_help()
    out = capsys.readouterr().out
    for var in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY", "PANEL_READONLY",
                "PTEROPILOT_CONCURRENCY", "PTEROPILOT_RETRIES"):
        assert var in out, f"帮助里漏了环境变量 {var}"


def test_help_warns_about_confirm(capsys) -> None:
    """写操作要二次确认这件事必须在帮助里说清。"""
    server._print_help()
    assert "--confirm" in capsys.readouterr().out


def test_help_mentions_docs(capsys) -> None:
    server._print_help()
    out = capsys.readouterr().out
    assert "telson.dev/pteropilot.html" in out
    assert "telson.dev/tutorial.html" in out


def test_help_is_short_enough(capsys) -> None:
    """速查表不该长到一屏放不下。"""
    server._print_help()
    lines = capsys.readouterr().out.rstrip().splitlines()
    assert len(lines) <= 45, f"帮助有 {len(lines)} 行，太长了"


def test_version_still_works(no_config, capsys) -> None:
    no_config.setattr(server.sys, "argv", ["pteropilot", "--version"])
    server.main()
    out = capsys.readouterr().out
    assert "个 MCP 工具" in out


def test_no_args_without_config_still_fails(no_config) -> None:
    """无参数且没配置时，仍然应该启动失败（不是悄悄当 help）。"""
    no_config.setattr(server.sys, "argv", ["pteropilot"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1
