"""``pteropilot --log``：命令行看控制台日志。

服务器崩了、指令没反应时，最想看的就是控制台最后几十行——原来只能开 AI
客户端问一句，现在直接打到终端，还能按关键字过滤。
"""

from __future__ import annotations

import json

import pytest

from pteropilot import server


@pytest.fixture
def fake_panel(monkeypatch):
    """把面板调用替换成固定日志，并给一份可通过校验的配置。"""
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()  # 重新读取环境变量

    state = {"log": "", "calls": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            state["calls"] += 1
            return state["log"]

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    return state


def test_no_server_prints_usage(capsys) -> None:
    assert server._run_log(["--log"]) == 2
    assert "用法" in capsys.readouterr().err


def test_prints_tail(fake_panel, capsys) -> None:
    fake_panel["log"] = "\n".join(f"line {i}" for i in range(200))
    assert server._run_log(["--log", "smp", "--lines", "5"]) == 0
    out = capsys.readouterr().out
    assert "line 199" in out
    assert "line 195" in out
    assert "line 194" not in out, "只该显示末尾 5 行"


def test_grep_filters(fake_panel, capsys) -> None:
    fake_panel["log"] = "Started\nERROR: boom\ndone\nERROR: again"
    assert server._run_log(["--log", "smp", "--grep", "error"]) == 0
    out = capsys.readouterr().out
    assert "boom" in out and "again" in out
    assert "Started" not in out, "grep 应过滤掉不匹配行"


def test_grep_is_case_insensitive(fake_panel, capsys) -> None:
    fake_panel["log"] = "Exception in thread\nok"
    assert server._run_log(["--log", "smp", "--grep", "EXCEPTION"]) == 0
    assert "Exception" in capsys.readouterr().out


def test_grep_no_match_exits_nonzero(fake_panel, capsys) -> None:
    fake_panel["log"] = "all fine"
    assert server._run_log(["--log", "smp", "--grep", "nothere"]) == 1
    assert "没有匹配" in capsys.readouterr().out


def test_empty_console_exits_nonzero(fake_panel, capsys) -> None:
    fake_panel["log"] = ""
    assert server._run_log(["--log", "smp"]) == 1
    assert "没有输出" in capsys.readouterr().out


def test_strips_ansi_and_cr(fake_panel, capsys) -> None:
    fake_panel["log"] = "\x1b[32mINFO\x1b[0m done\r\nnext"
    assert server._run_log(["--log", "smp"]) == 0
    out = capsys.readouterr().out
    assert "\x1b[" not in out, "ANSI 颜色要清掉，否则终端里是乱码"
    assert "INFO done" in out
    assert "next" in out


def test_json_output(fake_panel, capsys) -> None:
    fake_panel["log"] = "a\nb\nc"
    assert server._run_log(["--log", "smp", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["server_id"] == "smp"
    assert payload["total_lines"] == 3
    assert payload["lines"] == ["a", "b", "c"]
    assert payload["grep"] is None


def test_json_with_grep(fake_panel, capsys) -> None:
    fake_panel["log"] = "ok\nFAIL\nok"
    assert server._run_log(["--log", "smp", "--grep", "fail", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["matched_lines"] == 1
    assert payload["shown_lines"] == 1
    assert payload["grep"] == "fail"


def test_lines_clamped(fake_panel, capsys) -> None:
    """--lines 0 夹到 1，不崩。"""
    fake_panel["log"] = "x\ny\nz"
    assert server._run_log(["--log", "smp", "--lines", "0"]) == 0
    capsys.readouterr()          # 丢掉第一次的纯文本输出，避免和 JSON 拼在一起
    assert server._run_log(["--log", "smp", "--lines", "0", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["shown_lines"] == 1


def test_bad_lines_exits_2(fake_panel, capsys) -> None:
    assert server._run_log(["--log", "smp", "--lines", "abc"]) == 2
    assert "--lines 需要整数" in capsys.readouterr().err


def test_missing_config_exits_1(monkeypatch, capsys) -> None:
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    assert server._run_log(["--log", "smp"]) == 1
    assert "需要面板配置" in capsys.readouterr().err


def test_panel_failure_exits_1(fake_panel, monkeypatch, capsys) -> None:
    class Boom:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            raise RuntimeError("panel down")

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", Boom)
    assert server._run_log(["--log", "smp"]) == 1
    assert "读取失败" in capsys.readouterr().err
