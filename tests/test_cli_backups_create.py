"""``--backups --create``：命令行主动触发一次备份。

补完备份链路的最后一环。此前「看清单 / 取下载链接」都能在命令行做，
唯独「主动创建」只能靠 AI 客户端调 MCP 工具。

设计判断（测试就是把这些判断钉住）：
- 单台 --create **不需要** --confirm：与 MCP 工具 `create_backup` 口径一致，
  建备份是常规且可恢复的操作
- 多台（"*" / a,b）--create **必须** --confirm：一次给所有服务器建备份可能
  撑爆存储，而磁盘写满会让存档写不进去
- PANEL_READONLY=1 一律拒绝
"""

from __future__ import annotations

import json

import pytest

from pteropilot import server


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    monkeypatch.delenv("PANEL_READONLY", raising=False)
    server.config.__init__()
    return monkeypatch


def _fake(monkeypatch, *, created=None, fail_on=(), ids=("smp",)):
    """假面板：create_backup 返回带 uuid 的响应，fail_on 里的标识会抛错。"""
    state = {"calls": []}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def create_backup(self, sid):
            state["calls"].append(sid)
            if sid in fail_on:
                raise RuntimeError("panel said no")
            return {"attributes": {"uuid": created or f"uuid-{sid}-0000-1111",
                                  "name": f"backup-{sid}"}}

        async def list_backups(self, sid):
            return []

        async def aclose(self):
            pass

    async def fake_resolve(wanted):
        return list(ids) if wanted == ["*"] else list(wanted)

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    monkeypatch.setattr(server, "_resolve_target_ids", fake_resolve)
    return state


# ------------------------------------------------------------------ 单台：不需要确认

def test_single_create_without_confirm(panel, monkeypatch, capsys) -> None:
    state = _fake(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create"])
    out = capsys.readouterr().out
    assert rc == 0
    assert state["calls"] == ["smp"]
    assert "已开始创建备份" in out
    assert "uuid-smp" in out


def test_single_create_shows_next_steps(panel, monkeypatch, capsys) -> None:
    """创建完要告诉用户下一步看进度、取链接——否则他不知道备份在哪儿。"""
    _fake(monkeypatch)
    server._run_backups(["--backups", "smp", "--create"])
    out = capsys.readouterr().out
    assert "pteropilot --backups smp" in out
    assert "--backup-url smp" in out


def test_single_create_json(panel, monkeypatch, capsys) -> None:
    _fake(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create", "--json"])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert obj["created"][0]["server_id"] == "smp"
    assert obj["created"][0]["uuid"].startswith("uuid-smp")


def test_single_create_failure_exits_4(panel, monkeypatch, capsys) -> None:
    _fake(monkeypatch, fail_on=("smp",))
    rc = server._run_backups(["--backups", "smp", "--create"])
    err = capsys.readouterr().err
    assert rc == 4, "面板拒绝要如实非零"
    assert "smp" in err and "panel said no" in err


# ------------------------------------------------------------------ 多台：必须确认

def test_multi_create_requires_confirm(panel, monkeypatch, capsys) -> None:
    state = _fake(monkeypatch, ids=("smp", "lobby"))
    rc = server._run_backups(["--backups", "*", "--create"])
    err = capsys.readouterr().err
    assert rc == 2
    assert state["calls"] == [], "没确认就不该真的建备份"
    assert "--confirm" in err
    assert "撑爆存储" in err, "要说清为什么需要确认"


def test_multi_create_comma_also_requires_confirm(panel, monkeypatch) -> None:
    _fake(monkeypatch, ids=("smp", "lobby"))
    assert server._run_backups(["--backups", "smp,lobby", "--create"]) == 2


def test_multi_create_with_confirm_runs(panel, monkeypatch, capsys) -> None:
    state = _fake(monkeypatch, ids=("smp", "lobby"))
    rc = server._run_backups(["--backups", "*", "--create", "--confirm"])
    out = capsys.readouterr().out
    assert rc == 0
    assert state["calls"] == ["smp", "lobby"]
    assert "已开始创建备份" in out


def test_multi_create_partial_failure_exits_4(panel, monkeypatch, capsys) -> None:
    _fake(monkeypatch, ids=("smp", "lobby"), fail_on=("lobby",))
    rc = server._run_backups(["--backups", "*", "--create", "--confirm"])
    captured = capsys.readouterr()
    assert rc == 4, "部分失败要如实非零"
    assert "已开始创建备份" in captured.out, "成功那台照常输出"
    assert "lobby" in captured.err


# ------------------------------------------------------------------ 只读模式

def test_readonly_refuses_create(panel, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PANEL_READONLY", "1")
    server.config.__init__()
    state = _fake(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create"])
    err = capsys.readouterr().err
    assert rc == 2
    assert state["calls"] == []
    assert "PANEL_READONLY" in err


def test_readonly_does_not_block_listing(panel, monkeypatch, capsys) -> None:
    """只读只拦写操作，列清单照常能看。"""
    monkeypatch.setenv("PANEL_READONLY", "1")
    server.config.__init__()
    _fake(monkeypatch)
    assert server._run_backups(["--backups", "smp"]) == 0


# ------------------------------------------------------------------ 配置缺失

def test_create_without_config_exits_3(panel, monkeypatch, capsys) -> None:
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    rc = server._run_backups(["--backups", "smp", "--create"])
    assert rc == 3
    assert "需要面板配置" in capsys.readouterr().err


def test_create_panel_unreachable_exits_4(panel, monkeypatch, capsys) -> None:
    class Dead:
        def __init__(self, *a, **k):
            pass

        async def create_backup(self, sid):
            raise RuntimeError("connection refused")

        async def aclose(self):
            pass

    async def fake_resolve(wanted):
        return ["smp"]

    monkeypatch.setattr(server, "PanelClient", Dead)
    monkeypatch.setattr(server, "_resolve_target_ids", fake_resolve)
    rc = server._run_backups(["--backups", "smp", "--create"])
    assert rc == 4
    assert "connection refused" in capsys.readouterr().err


def test_create_usage_hint_mentions_create() -> None:
    """--backups 缺标识时的用法提示要把 --create 带上。"""
    import inspect

    src = inspect.getsource(server._run_backups)
    assert "--create" in src
