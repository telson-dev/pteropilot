"""``--log`` 的多台模式：``"*"`` 与 ``a,b``。

单台输出格式必须与之前完全一致（test_cli_log.py 锁住了它），所以多台走的是
另一条分支；这里专门测那一条。
"""

from __future__ import annotations

import json

import pytest

from pteropilot import server


@pytest.fixture
def panel(monkeypatch):
    """搭一个假面板：按 sid 返回不同日志，并记录被查了哪些。"""
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()

    logs = {}
    asked: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            asked.append(sid)
            if sid not in logs:
                raise RuntimeError("boom")
            return logs[sid]

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)

    async def fake_resolve(ids):
        if ids == ["*"]:
            return ["smp", "creative", "skyblock"]
        return ids

    monkeypatch.setattr(server, "_resolve_target_ids", fake_resolve)
    return logs, asked


# ------------------------------------------------------------------ 文本模式

def test_star_reads_every_server(panel, capsys) -> None:
    logs, asked = panel
    logs["smp"] = "Started\ndone"
    logs["creative"] = "creative ready"
    logs["skyblock"] = "skyblock ready"

    assert server._run_log(["--log", "*"]) == 0
    out = capsys.readouterr().out
    assert sorted(asked) == ["creative", "skyblock", "smp"]
    assert "### smp 控制台" in out
    assert "### creative 控制台" in out


def test_comma_list_only_those(panel, capsys) -> None:
    logs, asked = panel
    logs["smp"] = "a"
    logs["creative"] = "b"

    assert server._run_log(["--log", "smp,creative"]) == 0
    assert sorted(asked) == ["creative", "smp"]
    assert "skyblock" not in asked


def test_comma_list_tolerates_spaces(panel, capsys) -> None:
    logs, asked = panel
    logs["smp"] = "a"
    logs["creative"] = "b"

    server._run_log(["--log", "smp, creative"])
    assert sorted(asked) == ["creative", "smp"]


def test_multi_grep_applies_to_each(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = "Started\nERROR x\ndone"
    logs["creative"] = "ERROR y\nfine"
    logs["skyblock"] = "nothing here"

    assert server._run_log(["--log", "*", "--grep", "error"]) == 0
    out = capsys.readouterr().out
    assert "ERROR x" in out and "ERROR y" in out
    assert "Started" not in out
    assert "没有匹配" in out, "没命中的那台要明确说明，而不是静默跳过"


def test_multi_single_failure_does_not_hide_others(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = "ok"
    # creative 不给 → FakeClient 抛异常
    logs["skyblock"] = "also ok"

    assert server._run_log(["--log", "*"]) == 0
    out = capsys.readouterr().out
    assert "### smp 控制台" in out
    assert "❌ 读取失败" in out, "单台失败要标注，不能吞掉"
    assert "### skyblock 控制台" in out


def test_multi_all_empty_exits_one(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = ""
    logs["creative"] = ""
    logs["skyblock"] = ""

    assert server._run_log(["--log", "*"]) == 1, "全空时退出码 1，方便脚本判断"


def test_multi_lines_limits_each(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = "\n".join(f"s{i}" for i in range(50))
    logs["creative"] = "\n".join(f"c{i}" for i in range(50))
    logs["skyblock"] = "\n".join(f"k{i}" for i in range(50))

    server._run_log(["--log", "*", "--lines", "3"])
    out = capsys.readouterr().out
    for prefix in ("s", "c", "k"):
        assert f"{prefix}49" in out
        assert f"{prefix}46" not in out, "--lines 应对每台都生效"


# ------------------------------------------------------------------ JSON 模式

def test_multi_json_shape(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = "a\nb"
    logs["creative"] = "c"
    logs["skyblock"] = ""

    assert server._run_log(["--log", "*", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"servers", "grep"}, "多台用 servers 数组，不是单台的 server_id"
    ids = [s["server_id"] for s in payload["servers"]]
    assert ids == ["smp", "creative", "skyblock"], "顺序跟解析结果一致"
    assert payload["servers"][0]["lines"] == ["a", "b"]


def test_multi_json_records_error(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = "ok"
    logs["skyblock"] = "ok too"
    # creative 故意不给 → FakeClient 抛异常

    server._run_log(["--log", "*", "--json"])
    payload = json.loads(capsys.readouterr().out)
    bad = [s for s in payload["servers"] if "error" in s]
    assert len(bad) == 1
    assert bad[0]["server_id"] == "creative"
    assert bad[0]["error"] == "RuntimeError"


def test_multi_json_with_grep(panel, capsys) -> None:
    logs, _ = panel
    logs["smp"] = "ok\nFAIL"
    logs["creative"] = "ok"
    logs["skyblock"] = "FAIL too"

    server._run_log(["--log", "*", "--grep", "fail", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["grep"] == "fail"
    by_id = {s["server_id"]: s for s in payload["servers"]}
    assert by_id["smp"]["matched_lines"] == 1
    assert by_id["creative"]["matched_lines"] == 0


# ------------------------------------------------------------------ 边界

def test_no_servers_from_resolve(panel, monkeypatch, capsys) -> None:
    async def empty(ids):
        return []

    monkeypatch.setattr(server, "_resolve_target_ids", empty)
    assert server._run_log(["--log", "*"]) == 1
    assert "没有可访问的服务器" in capsys.readouterr().out


def test_empty_comma_list_is_usage_error(panel, capsys) -> None:
    assert server._run_log(["--log", ","]) == 2
    assert "空的" in capsys.readouterr().err


def test_single_server_still_uses_old_shape(panel, capsys) -> None:
    """单台不能因为加了多台模式就变形。"""
    logs, _ = panel
    logs["smp"] = "a\nb"

    server._run_log(["--log", "smp", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["server_id"] == "smp"
    assert "servers" not in payload
