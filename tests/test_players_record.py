"""``pteropilot --players --record``：现在采一条样写进本地历史。

背景：``--players`` 读的是本机采样文件，而采样只在 AI 客户端调用 ``list_players``
时才会写。只用命令行的用户（或想用 cron 积累历史的人）永远看不到数据——
这个功能对他们等于不存在。
"""

from __future__ import annotations

import json

import pytest

from pteropilot import playerlog, server


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把 playerlog 指到临时目录，别污染真实 ~/.pteropilot。"""
    recorded: list[tuple[str, list[str]]] = []

    def fake_record(server_id, players, *, now=None, directory=None):
        recorded.append((server_id, list(players)))
        return True

    monkeypatch.setattr(playerlog, "record", fake_record)
    monkeypatch.setattr(playerlog, "list_servers", lambda directory=None: [])
    return recorded


@pytest.fixture
def panel(monkeypatch):
    """假面板：按 sid 返回玩家名单。"""
    rosters: dict[str, list[str]] = {}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def online_players(self, sid):
            if sid not in rosters:
                raise RuntimeError("boom")
            return rosters[sid]

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", Client)

    async def fake_resolve(ids):
        return ["a", "b"] if ids == ["*"] else ids

    monkeypatch.setattr(server, "_resolve_target_ids", fake_resolve)
    return rosters


# ------------------------------------------------------------------ 用法 / 退出码

def test_missing_config_exits_3(monkeypatch, capsys) -> None:
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    assert server._run_players(["--players", "--record"]) == 3
    assert "需要面板配置" in capsys.readouterr().err


def test_all_failures_exit_4(env_panel, store, panel, capsys) -> None:
    """全部服务器都采不到样 → 4（面板/网络问题，不是「没服务器」）。"""

    class Dead:
        def __init__(self, *a, **k):
            pass

        async def online_players(self, sid):
            raise RuntimeError("connection refused")

        async def aclose(self):
            pass

    import pteropilot.server as srv
    orig = srv.PanelClient
    srv.PanelClient = Dead
    try:
        rc = server._run_players(["--players", "--record"])
    finally:
        srv.PanelClient = orig
    assert rc == 4
    assert store == [], "采不到样就不该写任何东西"
    assert "全部服务器都采样失败" in capsys.readouterr().out


# ------------------------------------------------------------------ 采样

def test_records_single_server(env_panel, store, panel, capsys) -> None:
    panel["smp"] = ["Alice", "Bob"]
    rc = server._run_players(["--players", "smp", "--record"])
    out = capsys.readouterr().out
    assert rc == 0
    assert store == [("smp", ["Alice", "Bob"])]
    assert "2 人在线" in out


def test_records_all_servers_when_no_id(env_panel, store, panel, capsys) -> None:
    """不给标识 = 全部可访问服务器，方便挂 cron 一次采完。"""
    panel["a"] = ["p1"]
    panel["b"] = []
    rc = server._run_players(["--players", "--record"])
    assert rc == 0
    assert sorted(sid for sid, _ in store) == ["a", "b"]
    assert ("b", []) in store, "0 人在线也是一条有效采样"


def test_records_zero_players(env_panel, store, panel, capsys) -> None:
    panel["smp"] = []
    rc = server._run_players(["--players", "smp", "--record"])
    assert rc == 0
    assert store == [("smp", [])]
    assert "0 人在线" in capsys.readouterr().out


def test_single_failure_does_not_hide_others(env_panel, store, panel, capsys) -> None:
    panel["a"] = ["p1"]
    # b 不在 rosters 里 → 抛异常
    rc = server._run_players(["--players", "--record"])
    out = capsys.readouterr()
    assert rc == 4, "有失败就该非零"
    assert ("a", ["p1"]) in store
    assert "b: RuntimeError" in out.err


# ------------------------------------------------------------------ JSON

def test_json_shape(env_panel, store, panel, capsys) -> None:
    panel["smp"] = ["Alice"]
    rc = server._run_players(["--players", "smp", "--record", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["errors"] == []
    assert payload["recorded"][0]["server_id"] == "smp"
    assert payload["recorded"][0]["players"] == ["Alice"]
    assert payload["recorded"][0]["written"] is True


def test_json_reports_errors(env_panel, store, panel, capsys) -> None:
    panel["a"] = ["p1"]
    server._run_players(["--players", "--record", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert any("b" in e for e in payload["errors"])


# ------------------------------------------------------------------ 与只读用法互不干扰

def test_without_record_still_reads_local(env_panel, store, capsys) -> None:
    """不加 --record 时仍是纯本地读取，不碰面板。"""
    rc = server._run_players(["--players"])
    out = capsys.readouterr().out
    assert rc == 1, "本地没记录时返回 1（与之前一致）"
    assert "还没有任何玩家采样记录" in out
    assert store == [], "只读模式不该写任何东西"
