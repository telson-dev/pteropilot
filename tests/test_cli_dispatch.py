"""``main()`` 的 flag 分发矩阵。

命令行面已经涨到 7 个 flag（--help / --version / --doctor / --status / --log /
--players / --broadcast / --power）。这些 flag 全靠 `main()` 里一串 if 判断
分发——改个名字、挪个顺序，就可能悄悄掉进「启动 server」分支。

这个文件对每个 flag 断言三件事：
  1. 路由到了对应的处理函数
  2. 没有掉进 ``mcp.run()``
  3. 退出码/返回值符合约定
"""

from __future__ import annotations

import pytest

from pteropilot import server


@pytest.fixture
def wired(monkeypatch):
    """把每个处理函数换成记录器，避免真的连面板/起 server。"""
    calls: dict[str, list[list[str]]] = {}

    def record(name: str, rc: int = 0):
        def fn(argv=None, *args, **kwargs):
            # _run_doctor 是 _run_doctor(skip_network=...) 关键字调用，
            # 其余是 _run_xxx(argv) 位置调用，两种都要接得住
            calls.setdefault(name, []).append(list(argv) if argv is not None else [])
            return rc
        return fn

    def boom():
        raise AssertionError("不该启动 MCP server")

    monkeypatch.setattr(server.mcp, "run", boom)
    monkeypatch.setattr(server, "_run_status", record("status"))
    monkeypatch.setattr(server, "_run_log", record("log"))
    monkeypatch.setattr(server, "_run_players", record("players"))
    monkeypatch.setattr(server, "_run_doctor", record("doctor"))
    monkeypatch.setattr(server, "_run_broadcast", record("broadcast"))
    monkeypatch.setattr(server, "_run_power", record("power"))
    return calls


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


@pytest.fixture
def unconfigured(monkeypatch):
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()


# ------------------------------------------------------------------ 每个 flag 都有去处

@pytest.mark.parametrize("flag,handler", [
    (["--status"], "status"),
    (["--status", "smp", "--json"], "status"),
    (["--status", "--watch", "--interval", "10"], "status"),
    (["--status", "--backups", "--backup-days", "30"], "status"),
    (["--log", "smp"], "log"),
    (["--log", "smp", "--grep", "err"], "log"),
    (["--players"], "players"),
    (["--players", "smp", "--days", "30"], "players"),
])
def test_read_flags_route(wired, configured, flag, handler) -> None:
    server.sys.argv = ["pteropilot", *flag]
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert handler in wired, f"{flag} 没有路由到 {handler}"
    assert wired[handler][-1] == flag, "参数要原样传下去"


@pytest.mark.parametrize("flag,handler", [
    (['--broadcast', 'say hi'], "broadcast"),
    (["--power", "restart", "--servers", "smp", "--confirm"], "power"),
])
def test_write_flags_route(wired, configured, flag, handler) -> None:
    server.sys.argv = ["pteropilot", *flag]
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert handler in wired


def test_doctor_routes(wired, configured) -> None:
    server.sys.argv = ["pteropilot", "--doctor"]
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert "doctor" in wired


def test_version_does_not_start_server(wired) -> None:
    server.sys.argv = ["pteropilot", "--version"]
    server.main()                     # 只是 return
    assert wired == {}


def test_help_does_not_start_server(wired) -> None:
    server.sys.argv = ["pteropilot", "--help"]
    server.main()
    assert wired == {}


# ------------------------------------------------------------------ 缺配置时的退出码

# 只覆盖 main() 自己校验配置的 flag。
# --status / --log / --players 的校验在各自函数内部，已在各自测试文件里覆盖。
@pytest.mark.parametrize("flag", [
    ["--broadcast", "say hi"],
    ["--power", "restart", "--servers", "smp", "--confirm"],
])
def test_missing_config_exits_one(wired, unconfigured, capsys, flag) -> None:
    server.sys.argv = ["pteropilot", *flag]
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1
    assert "需要面板配置" in capsys.readouterr().err


def test_no_flag_without_config_does_not_start_server(wired, unconfigured, capsys) -> None:
    server.sys.argv = ["pteropilot"]
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1, "无参数 + 无配置 应当启动失败"
    assert "启动失败" in capsys.readouterr().err


# ------------------------------------------------------------------ 工具数兜底

def test_tool_count_fallback_when_introspection_fails(monkeypatch) -> None:
    """MCP 内部结构变了也要能数出工具数（从源码里数 @mcp.tool()）。"""
    monkeypatch.setattr(server.mcp, "_tool_manager", None, raising=False)
    monkeypatch.setattr(server.mcp, "_tool_manager", object(), raising=False)
    n = server._tool_count()
    assert n > 0, "兜底路径应当从源码数出工具"
