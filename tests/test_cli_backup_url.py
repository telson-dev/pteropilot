"""``pteropilot --backup-url <标识> <备份名或 UUID>``。

补完恢复流程最后一环：--status --backups 说「备份过期了」→ --backups 列「有
哪些」→ 真要恢复时还缺一个一次性下载链接，而那个能力只在 MCP 工具里。

允许按备份名指（UUID 36 位没法手打，--backups 表格里也没地方放）；名字可以只写
前几位，唯一才用；重名时列候选让人自己选，不猜。
"""

from __future__ import annotations

import json

import pytest

from pteropilot import server

_GB = 1024 ** 3
URL = "https://panel.example.com/backups/download?token=abc123"


def _b(uuid="aaaaaaaa-1111-2222-3333-444444444444", name="weekly-1",
       created="2026-11-16T10:00:00Z", completed=True) -> dict:
    a = {"uuid": uuid, "name": name, "bytes": _GB, "created_at": created,
         "is_locked": False}
    if completed:
        a["completed_at"] = created
    return {"attributes": a}


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


def _bind(backups=None, *, url=URL, raises: Exception | None = None,
          url_raises: Exception | None = None) -> list:
    calls: list[tuple[str, str]] = []

    class Client:
        def __init__(self, *a, **k):
            pass

        async def list_backups(self, sid):
            if raises:
                raise raises
            return list(backups or [])

        async def backup_download_url(self, sid, bid):
            calls.append((sid, bid))
            if url_raises:
                raise url_raises
            return url

        async def aclose(self):
            pass

    server.PanelClient = Client
    return calls


# ------------------------------------------------------------------ 用法 / 退出码

def test_missing_args_exits_2(env_panel, capsys) -> None:
    assert server._run_backup_url(["--backup-url"]) == 2
    assert "用法" in capsys.readouterr().err


def test_missing_key_exits_2(env_panel, capsys) -> None:
    assert server._run_backup_url(["--backup-url", "smp"]) == 2


def test_missing_config_exits_3(monkeypatch, capsys) -> None:
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    assert server._run_backup_url(["--backup-url", "smp", "weekly-1"]) == 3
    assert "需要面板配置" in capsys.readouterr().err


def test_panel_error_exits_4(env_panel, capsys) -> None:
    _bind(raises=RuntimeError("connection refused"))
    assert server._run_backup_url(["--backup-url", "smp", "weekly-1"]) == 4
    assert "查询失败" in capsys.readouterr().err


def test_no_backup_at_all_exits_4(env_panel, capsys) -> None:
    _bind([])
    assert server._run_backup_url(["--backup-url", "smp", "weekly-1"]) == 4
    assert "没有任何备份" in capsys.readouterr().err


def test_no_url_from_panel_exits_4(env_panel, capsys) -> None:
    _bind([_b()], url="")
    assert server._run_backup_url(["--backup-url", "smp", "weekly-1"]) == 4
    assert "没返回下载链接" in capsys.readouterr().err


# ------------------------------------------------------------------ 按名字定位

def test_exact_name(env_panel, capsys) -> None:
    calls = _bind([_b(name="weekly-1"), _b(uuid="bbbb", name="nightly")])
    assert server._run_backup_url(["--backup-url", "smp", "weekly-1"]) == 0
    out = capsys.readouterr().out
    assert URL in out
    assert calls == [("smp", "aaaaaaaa-1111-2222-3333-444444444444")]


def test_prefix_match_when_unique(env_panel) -> None:
    calls = _bind([_b(name="weekly-2026-11-16"), _b(uuid="bbbb", name="nightly-3")])
    assert server._run_backup_url(["--backup-url", "smp", "weekly"]) == 0
    assert calls[0][1].startswith("aaaaaaaa")


def test_by_uuid(env_panel) -> None:
    _bind([_b(uuid="cccccccc-0000", name="x")])
    assert server._run_backup_url(["--backup-url", "smp", "cccccccc"]) == 0


def test_ambiguous_lists_candidates_and_exits_2(env_panel, capsys) -> None:
    """重名时不能瞎选一个——恢复错备份是不可逆的。"""
    _bind([_b(uuid="a1", name="weekly-1"), _b(uuid="a2", name="weekly-2")])
    rc = server._run_backup_url(["--backup-url", "smp", "weekly"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "没找到唯一匹配" in err
    assert "weekly-1" in err and "weekly-2" in err


def test_not_found(env_panel, capsys) -> None:
    _bind([_b(name="weekly-1")])
    rc = server._run_backup_url(["--backup-url", "smp", "不存在的东西"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "没找到唯一匹配" in err


def test_empty_key_does_not_match_everything(env_panel, capsys) -> None:
    """空串前缀会匹配所有备份——必须当成用法错误而不是随便挑一个。"""
    _bind([_b(name="a"), _b(uuid="b", name="b")])
    rc = server._run_backup_url(["--backup-url", "smp", "   "])
    assert rc in (2, 4)


# ------------------------------------------------------------------ 输出

def test_text_output_warns_one_time(env_panel, capsys) -> None:
    _bind([_b()])
    server._run_backup_url(["--backup-url", "smp", "weekly-1"])
    out = capsys.readouterr().out
    assert URL in out
    assert "一次性" in out, "必须提醒链接一次性且不要外传"


def test_json_output(env_panel, capsys) -> None:
    _bind([_b()])
    assert server._run_backup_url(["--backup-url", "smp", "weekly-1", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["url"] == URL
    assert d["server_id"] == "smp"
    assert d["backup"]["name"] == "weekly-1"


def test_json_has_no_one_time_warning_noise(env_panel, capsys) -> None:
    """JSON 是给脚本吃的，不能混进人看的提示语。"""
    _bind([_b()])
    server._run_backup_url(["--backup-url", "smp", "weekly-1", "--json"])
    out = capsys.readouterr().out
    json.loads(out)          # 必须整体可解析
    assert "一次性" not in out
