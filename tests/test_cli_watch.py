"""``pteropilot --status --watch``：轮询状态，只在真的变化时刷屏。

``_watch_loop`` 把 I/O 全拿出去（emit / heartbeat 由调用方给），所以这里
可以直接跑循环本身，不用连面板也不用真的等。
"""

from __future__ import annotations

import asyncio

import pytest

from pteropilot import server


def _data(*rows: tuple[str, str, str, int]) -> dict:
    """rows: (id, state, health, players)

    字段要与 ``_status_data`` 的真实产出一致——``_format_status`` 会读
    memory_bytes / memory_limit / cpu / disk_bytes，少一个就 KeyError。
    """
    return {
        "servers": [
            {
                "id": i,
                "name": i.upper(),
                "state": s,
                "health": h,
                "cpu": 1.5,
                "memory_bytes": 128 * 1024 * 1024,
                "memory_limit": 1024 * 1024 * 1024,
                "disk_bytes": 0,
                "players": p,
            }
            for i, s, h, p in rows
        ],
        "errors": [],
    }


@pytest.fixture
def no_sleep(monkeypatch):
    """把轮询间隔压成 0，并记录每次请求的间隔。"""
    calls: list[float] = []

    async def fake_sleep(d: float) -> None:
        calls.append(d)

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    return calls


# ------------------------------------------------------------------ 签名

def test_signature_ignores_cpu_and_memory() -> None:
    """CPU/内存每轮都在抖，不能因为它们触发刷屏。"""
    a = {"servers": [{"id": "s1", "state": "running", "health": "healthy", "players": 3, "cpu": 10.0}]}
    b = {"servers": [{"id": "s1", "state": "running", "health": "healthy", "players": 3, "cpu": 87.5}]}
    assert server._status_signature(a) == server._status_signature(b)


@pytest.mark.parametrize("field,value", [
    ("state", "stopped"),
    ("health", "dead"),
    ("players", 0),
])
def test_signature_reacts_to_real_changes(field: str, value) -> None:
    base = {"id": "s1", "state": "running", "health": "healthy", "players": 3}
    assert (server._status_signature({"servers": [dict(base)]})
            != server._status_signature({"servers": [{**base, field: value}]}))


def test_signature_is_order_insensitive() -> None:
    a = _data(("a", "running", "healthy", 1), ("b", "stopped", "dead", 0))
    b = _data(("b", "stopped", "dead", 0), ("a", "running", "healthy", 1))
    assert server._status_signature(a) == server._status_signature(b)


def test_signature_handles_empty() -> None:
    assert server._status_signature({}) == ""
    assert server._status_signature({"servers": []}) == ""


# ------------------------------------------------------------------ 循环

def test_emits_first_then_heartbeats(no_sleep) -> None:
    seq = [_data(("s1", "running", "healthy", 2))] * 3
    emitted: list[bool] = []
    beats: list[int] = []

    async def fetch():
        return seq.pop(0)

    asyncio.run(server._watch_loop(
        fetch, interval=5,
        emit=lambda d, first: emitted.append(first),
        heartbeat=lambda d: beats.append(1),
        max_rounds=3,
    ))
    assert emitted == [True], "只有第一轮该刷完整表格"
    assert len(beats) == 2, "后两轮都是心跳"


def test_emits_again_when_state_changes(no_sleep) -> None:
    seq = [
        _data(("s1", "running", "healthy", 2)),
        _data(("s1", "stopped", "dead", 0)),
        _data(("s1", "stopped", "dead", 0)),
    ]
    emitted: list[bool] = []

    async def fetch():
        return seq.pop(0)

    asyncio.run(server._watch_loop(
        fetch, interval=5,
        emit=lambda d, first: emitted.append(first),
        heartbeat=lambda d: None,
        max_rounds=3,
    ))
    assert emitted == [True, False], "状态变了要再刷一次，且 first=False"


def test_uses_interval(no_sleep) -> None:
    seq = [_data(("s1", "running", "healthy", 1))] * 3

    async def fetch():
        return seq.pop(0)

    asyncio.run(server._watch_loop(
        fetch, interval=7, emit=lambda d, f: None, heartbeat=lambda d: None, max_rounds=3,
    ))
    assert no_sleep == [7, 7], "两轮之间各睡一次，共 max_rounds-1 次"


def test_returns_last_data(no_sleep) -> None:
    seq = [_data(("s1", "running", "healthy", 1)), _data(("s1", "stopped", "dead", 0))]

    async def fetch():
        return seq.pop(0)

    out = asyncio.run(server._watch_loop(
        fetch, interval=1, emit=lambda d, f: None, heartbeat=lambda d: None, max_rounds=2,
    ))
    assert out["servers"][0]["state"] == "stopped"


# ------------------------------------------------------------------ 命令层

@pytest.fixture
def panel_env(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


def test_watch_text_output(panel_env, no_sleep, monkeypatch, capsys) -> None:
    seq = [_data(("s1", "running", "healthy", 2)), _data(("s1", "running", "healthy", 2))]

    async def fake_status(sid):
        return seq.pop(0)

    monkeypatch.setattr(server, "_status_data", fake_status)

    rc = server._run_status(["--status", "--watch", "--interval", "10", "--rounds", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "每 10 秒检查一次" in out
    assert "首次" in out
    assert "无变化" in out


def test_watch_exits_nonzero_when_unhealthy(panel_env, no_sleep, monkeypatch, capsys) -> None:
    seq = [_data(("s1", "stopped", "dead", 0))] * 2

    async def fake_status(sid):
        return seq.pop(0)

    monkeypatch.setattr(server, "_status_data", fake_status)
    rc = server._run_status(["--status", "--watch", "--rounds", "2"])
    assert rc == 1, "最后一轮有异常服务器就该是非零"


def test_watch_json_is_one_object_per_line(panel_env, no_sleep, monkeypatch, capsys) -> None:
    import json as _json

    seq = [_data(("s1", "running", "healthy", 1)), _data(("s1", "stopped", "dead", 0))]

    async def fake_status(sid):
        return seq.pop(0)

    monkeypatch.setattr(server, "_status_data", fake_status)
    server._run_status(["--status", "--watch", "--json", "--rounds", "2"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("{")]
    assert len(lines) == 2, "两轮都变了，应有两行 NDJSON"
    for ln in lines:
        assert _json.loads(ln)["servers"][0]["id"] == "s1"


def test_watch_json_suppresses_heartbeat(panel_env, no_sleep, monkeypatch, capsys) -> None:
    seq = [_data(("s1", "running", "healthy", 1))] * 3

    async def fake_status(sid):
        return seq.pop(0)

    monkeypatch.setattr(server, "_status_data", fake_status)
    server._run_status(["--status", "--watch", "--json", "--rounds", "3"])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("{")]
    assert len(lines) == 1, "没变化时 JSON 模式不该输出心跳"


def test_watch_bad_interval(panel_env, capsys) -> None:
    rc = server._run_status(["--status", "--watch", "--interval", "abc"])
    assert rc == 2
    assert "--interval 需要整数" in capsys.readouterr().err


def test_watch_interval_clamped(panel_env, no_sleep, monkeypatch, capsys) -> None:
    seq = [_data(("s1", "running", "healthy", 1))] * 2

    async def fake_status(sid):
        return seq.pop(0)

    monkeypatch.setattr(server, "_status_data", fake_status)
    server._run_status(["--status", "--watch", "--interval", "0", "--rounds", "2"])
    assert "每 5 秒检查一次" in capsys.readouterr().out, "0 应被夹到下限 5"


def test_watch_survives_keyboard_interrupt(panel_env, monkeypatch, capsys) -> None:
    async def fake_status(sid):
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_status_data", fake_status)
    rc = server._run_status(["--status", "--watch", "--rounds", "2"])
    assert rc == 0, "Ctrl+C 应当干净退出（0），而不是当错误"
    assert "已停止" in capsys.readouterr().out


def test_watch_reports_panel_error(panel_env, monkeypatch, capsys) -> None:
    async def fake_status(sid):
        raise RuntimeError("panel down")

    monkeypatch.setattr(server, "_status_data", fake_status)
    rc = server._run_status(["--status", "--watch"])
    assert rc == 1
    assert "查询失败" in capsys.readouterr().err


def test_single_shot_still_works(panel_env, monkeypatch, capsys) -> None:
    async def fake_status(sid):
        return _data(("s1", "running", "healthy", 1))

    monkeypatch.setattr(server, "_status_data", fake_status)
    rc = server._run_status(["--status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "每" not in out, "非 watch 模式不该打印轮询提示"
