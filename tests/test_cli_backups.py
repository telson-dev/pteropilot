"""``pteropilot --backups <标识>``：命令行看备份清单。

动机：``--status --backups`` 只告诉你「最新备份多旧」，但看到这个警告之后，
人真正想干的是「都有哪些备份、哪个能拿来恢复」。这个能力一直只在 MCP 工具
（list_backups）里，纯命令行用户摸不到。
"""

from __future__ import annotations

import json

import pytest

from pteropilot import server

_GB = 1024 ** 3


def _b(uuid="u1", name="weekly", size=1 * _GB, created="2026-11-16T10:00:00Z",
       completed=True, locked=False) -> dict:
    a = {"uuid": uuid, "name": name, "bytes": size, "created_at": created,
         "is_locked": locked}
    if completed:
        a["completed_at"] = created
    return {"attributes": a}


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


def _bind(backups=None, *, raises: Exception | None = None):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def list_backups(self, sid):
            if raises:
                raise raises
            return list(backups or [])

        async def aclose(self):
            pass

    server.PanelClient = Client


# ------------------------------------------------------------------ 用法 / 退出码

def test_missing_server_id_exits_2(env_panel, capsys) -> None:
    assert server._run_backups(["--backups"]) == 2
    assert "用法" in capsys.readouterr().err


def test_missing_config_exits_3(monkeypatch, capsys) -> None:
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    assert server._run_backups(["--backups", "smp"]) == 3
    assert "需要面板配置" in capsys.readouterr().err


def test_panel_error_exits_4(env_panel, capsys) -> None:
    _bind(raises=RuntimeError("connection refused"))
    assert server._run_backups(["--backups", "smp"]) == 4
    assert "查询失败" in capsys.readouterr().err


def test_bad_limit_exits_2(env_panel, capsys) -> None:
    assert server._run_backups(["--backups", "smp", "--limit", "abc"]) == 2
    assert "整数" in capsys.readouterr().err


def test_limit_without_value_exits_2(env_panel, capsys) -> None:
    assert server._run_backups(["--backups", "smp", "--limit"]) == 2


# ------------------------------------------------------------------ 表格

def test_lists_newest_first(env_panel, capsys) -> None:
    _bind([
        _b(uuid="old", name="old-one", created="2026-10-01T00:00:00Z"),
        _b(uuid="new", name="new-one", created="2026-11-16T00:00:00Z"),
        _b(uuid="mid", name="mid-one", created="2026-11-10T00:00:00Z"),
    ])
    assert server._run_backups(["--backups", "smp"]) == 0
    out = capsys.readouterr().out
    i_new = out.index("new-one")
    i_mid = out.index("mid-one")
    i_old = out.index("old-one")
    assert i_new < i_mid < i_old, "应该从新到旧"


def test_empty_backups(env_panel, capsys) -> None:
    _bind([])
    assert server._run_backups(["--backups", "smp"]) == 0
    assert "还没有任何备份" in capsys.readouterr().out


def test_shows_state_and_lock(env_panel, capsys) -> None:
    _bind([
        _b(uuid="a", name="done-locked", created="2026-11-16T00:00:00Z", completed=True, locked=True),
        _b(uuid="b", name="running", created="2026-11-15T00:00:00Z", completed=False),
    ])
    server._run_backups(["--backups", "smp"])
    out = capsys.readouterr().out
    assert "完成" in out and "进行中" in out
    assert "已锁定" in out


def test_summary_counts_successful_and_total_size(env_panel, capsys) -> None:
    _bind([
        _b(uuid="a", size=1 * _GB, created="2026-11-16T00:00:00Z", completed=True),
        _b(uuid="b", size=1 * _GB, created="2026-11-15T00:00:00Z", completed=False),
    ])
    server._run_backups(["--backups", "smp"])
    out = capsys.readouterr().out
    assert "成功 1 / 共 2" in out
    assert "合计 2.0 GB" in out


def test_limit_truncates_and_says_so(env_panel, capsys) -> None:
    _bind([_b(uuid=f"u{i}", name=f"b{i}", created=f"2026-11-{10 + i:02d}T00:00:00Z")
           for i in range(1, 6)])
    server._run_backups(["--backups", "smp", "--limit", "2"])
    out = capsys.readouterr().out
    assert "显示最近 2 个，共 5 个" in out
    assert out.count("b") >= 2


def test_limit_not_exceeded_does_not_claim_truncation(env_panel, capsys) -> None:
    _bind([_b(uuid="a", name="only")])
    server._run_backups(["--backups", "smp", "--limit", "10"])
    out = capsys.readouterr().out
    assert "共 1 个备份" in out
    assert "显示最近" not in out


def test_limit_clamped(env_panel, capsys) -> None:
    """--limit 0 会被夹到 1，不会输出空表。"""
    _bind([_b(uuid="a", name="n1"), _b(uuid="b", name="n2")])
    assert server._run_backups(["--backups", "smp", "--limit", "0"]) == 0
    assert "显示最近 1 个" in capsys.readouterr().out


# ------------------------------------------------------------------ JSON

def test_json_shape(env_panel, capsys) -> None:
    _bind([_b(uuid="a", name="n1", size=_GB)])
    assert server._run_backups(["--backups", "smp", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["server_id"] == "smp"
    assert d["total"] == 1
    assert d["shown"] == 1
    card = d["backups"][0]
    assert card["name"] == "n1" and card["bytes"] == _GB
    assert card["completed"] is True and card["locked"] is False


def test_json_truncated_fields(env_panel, capsys) -> None:
    _bind([_b(uuid=f"u{i}", created=f"2026-11-{10 + i:02d}T00:00:00Z") for i in range(1, 5)])
    server._run_backups(["--backups", "smp", "--limit", "2", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["total"] == 4 and d["shown"] == 2 and len(d["backups"]) == 2
