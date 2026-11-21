"""``pteropilot --log --watch``：像 tail -f 一样跟读控制台。

动机：`--log` 只能取一次「末 N 行」。而最需要看日志的时刻，往往是服务器
**正在**崩、你在等下一行输出——这时只能反复手动重跑命令。

难点：面板返回的是「最后 N 行」而不是增量，所以两次轮询之间是同一段文本被
窗口滑过；要用 overlap 对齐算出真正的增量（``_log_delta``）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pteropilot import server


@pytest.fixture
def fake_panel(monkeypatch):
    """假面板：日志内容可逐轮变更，并记录被调次数。"""
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()

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


@pytest.fixture
def no_sleep(monkeypatch):
    async def fake_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)


def _lines(*items: str) -> str:
    return "\r\n".join(items) + "\r\n"


# ------------------------------------------------------------------ _log_delta

def test_delta_identical_is_empty() -> None:
    old = ["a", "b", "c"]
    assert server._log_delta(old, list(old)) == ([], False)


def test_delta_pure_append() -> None:
    old = ["a", "b"]
    new = ["a", "b", "c", "d"]
    assert server._log_delta(old, new) == (["c", "d"], False)


def test_delta_shifted_window() -> None:
    """面板只保留末 N 行：内容没变，窗口滑动过。"""
    old = ["a", "b", "c", "d"]
    new = ["c", "d", "e"]           # a,b 掉出去了，e 是新增
    assert server._log_delta(old, new) == (["e"], False)


def test_delta_capped_and_full() -> None:
    old = ["b", "c", "d"]
    new = ["c", "d", "e"]
    assert server._log_delta(old, new) == (["e"], False)


def test_delta_no_overlap_flags_resync() -> None:
    """面板重启 / 日志被清过：不能假装整段都是新增。"""
    delta, resync = server._log_delta(["a", "b"], ["x", "y"])
    assert resync is True
    assert delta == ["x", "y"]


def test_delta_empty_old_returns_all() -> None:
    assert server._log_delta([], ["a", "b"]) == (["a", "b"], False)


def test_delta_duplicate_lines() -> None:
    """日志里常有重复行，overlap 要按最长匹配算，不能少算。"""
    old = ["log", "log", "log"]
    new = ["log", "log", "log", "log"]
    assert server._log_delta(old, new) == (["log"], False)


# ------------------------------------------------------------------ 跟读循环

def test_loop_first_round_yields_tail(no_sleep) -> None:
    seq = [["1", "2", "3"], ["1", "2", "3"]]
    seen: list = []

    async def fetch():
        return [("smp", seq.pop(0))]

    asyncio.run(server._log_watch_loop(
        fetch, interval=5, tail=2, emit=lambda items: seen.append(items), max_rounds=2,
    ))
    assert seen[0][0][1] == ["2", "3"], "首轮给末 tail 行当起点"
    assert seen[1][0][1] == [], "第二轮没有新增"


def test_loop_only_new_lines_later(no_sleep) -> None:
    seq = [["a"], ["a", "b"], ["a", "b", "c"]]
    seen: list = []

    async def fetch():
        return [("smp", seq.pop(0))]

    asyncio.run(server._log_watch_loop(
        fetch, interval=5, tail=10, emit=lambda items: seen.append(items), max_rounds=3,
    ))
    assert seen[0][0][1] == ["a"]
    assert seen[1][0][1] == ["b"]
    assert seen[2][0][1] == ["c"]


def test_loop_reports_error_and_keeps_going(no_sleep) -> None:
    """单台失败要如实标注，不能让整轮炸掉。"""
    seq = [("smp", RuntimeError("boom")), ("smp", ["ok"])]
    seen: list = []

    async def fetch():
        item = seq.pop(0)
        return [item]

    asyncio.run(server._log_watch_loop(
        fetch, interval=5, tail=10, emit=lambda items: seen.append(items), max_rounds=2,
    ))
    assert isinstance(seen[0][0][3], BaseException)
    assert seen[1][0][1] == ["ok"]


def test_loop_multi_server_independent(no_sleep) -> None:
    seq = [
        [("a", ["1"]), ("b", ["x"])],
        [("a", ["1", "2"]), ("b", ["x"])],
    ]
    seen: list = []

    async def fetch():
        return seq.pop(0)

    asyncio.run(server._log_watch_loop(
        fetch, interval=5, tail=10, emit=lambda items: seen.append(items), max_rounds=2,
    ))
    second = {i: delta for i, delta, _r, _e in seen[1]}
    assert second["a"] == ["2"] and second["b"] == []


# ------------------------------------------------------------------ CLI 输出

def test_watch_prints_tail_then_only_new(fake_panel, no_sleep, capsys) -> None:
    fake_panel["log"] = _lines("line1", "line2")
    seq = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            seq["n"] += 1
            return _lines("line1", "line2") if seq["n"] == 1 else _lines("line1", "line2", "line3")

        async def aclose(self):
            pass

    server.PanelClient = FakeClient
    rc = server._run_log(["--log", "smp", "--watch", "--interval", "2", "--rounds", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "开始跟读" in out
    assert "line1" in out and "line2" in out
    assert "line3" in out
    assert out.count("line1") == 1, "第二轮不该重复打已有行"


def test_watch_json_one_object_per_round(fake_panel, no_sleep, capsys, monkeypatch) -> None:
    # 每轮都会新建一个 client 实例，计数必须放在共享的闭包里
    counter = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            counter["n"] += 1
            lines = ["a"] if counter["n"] == 1 else ["a", "b"]
            return _lines(*lines)

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    rc = server._run_log(["--log", "smp", "--watch", "--interval", "2", "--rounds", "2", "--json"])
    out = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    assert rc == 0
    objs = [json.loads(ln) for ln in out]
    assert objs[0]["lines"] == ["a"]
    assert objs[1]["lines"] == ["b"]


def test_watch_grep_only_shows_matches(fake_panel, no_sleep, capsys, monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            return _lines("INFO ok", "ERROR boom")

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    rc = server._run_log(["--log", "smp", "--watch", "--interval", "2", "--rounds", "1",
                          "--grep", "error"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ERROR boom" in out
    assert "INFO ok" not in out


def test_watch_interval_clamped(fake_panel, no_sleep, capsys, monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            return _lines("x")

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    # --interval 1 会被夹到 2（下限），不该报错
    assert server._run_log(["--log", "smp", "--watch", "--interval", "1", "--rounds", "1"]) == 0
    assert "每 2 秒跟一次" in capsys.readouterr().out


def test_watch_bad_interval_exits_2(fake_panel, capsys) -> None:
    assert server._run_log(["--log", "smp", "--watch", "--interval", "abc"]) == 2
    assert "整数" in capsys.readouterr().err


def test_watch_multi_server_prefixes_lines(fake_panel, no_sleep, capsys, monkeypatch) -> None:
    """``--log "*" --watch``：每行带服务器前缀，否则多台混在一起没法分辨。"""
    monkeypatch.setattr(server, "_resolve_target_ids", lambda ids: _ids(ids))

    logs = {"smp": ["A1"], "lobby": ["B1"]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            return _lines(*logs[sid])

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    rc = server._run_log(["--log", "*", "--watch", "--interval", "2", "--rounds", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "smp| A1" in out
    assert "lobby| B1" in out


async def _ids(ids):
    return ["smp", "lobby"] if ids == ["*"] else ids


def test_watch_multi_single_failure_flagged(fake_panel, no_sleep, capsys, monkeypatch) -> None:
    monkeypatch.setattr(server, "_resolve_target_ids", lambda ids: _ids(ids))

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            if sid == "lobby":
                raise RuntimeError("boom")
            return _lines("ok-line")

        async def aclose(self):
            pass

    monkeypatch.setattr(server, "PanelClient", FakeClient)
    rc = server._run_log(["--log", "*", "--watch", "--interval", "2", "--rounds", "1"])
    captured = capsys.readouterr()
    assert rc == 0, "单台失败不该让整轮非零"
    assert "ok-line" in captured.out, "成功那台的内容要照常输出"
    assert "lobby" in captured.err, "失败那台要在 stderr 点名"


def test_watch_missing_config_exits_3(monkeypatch, capsys) -> None:
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    server.config.__init__()
    assert server._run_log(["--log", "smp", "--watch"]) == 3
    assert "需要面板配置" in capsys.readouterr().err


def test_watch_panel_failure_exits_4(fake_panel, capsys) -> None:
    class Dead:
        def __init__(self, *a, **k):
            pass

        async def console_log(self, sid):
            raise RuntimeError("connection refused")

        async def aclose(self):
            pass

    server.PanelClient = Dead
    assert server._run_log(["--log", "smp", "--watch", "--rounds", "1"]) == 4
    assert "读取失败" in capsys.readouterr().err
