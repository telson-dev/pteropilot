"""``--status --watch`` 的「异常持续」列。

动机：watch 早就在内存里跟踪上一轮状态，但只用来决定「要不要重画表格」。
服务器真挂了之后，人盯着终端最想知道的是**挂了多久**——
「刚挂 30 秒」和「已经挂 2 小时」完全是两件事。

接口没变：持续时间以 ``state_age_seconds`` 写进每台服务器的 row，
所以 emit / heartbeat 仍只收两个参数，JSON 输出也自动带上这个字段。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pteropilot import server


def _data(*rows: tuple[str, str, str, int]) -> dict:
    """rows: (id, state, health, players)。

    字段要与 ``_status_data`` 的真实产出一致——``_format_status`` 会读
    cpu / memory_bytes / memory_limit / disk_bytes，少一个就 KeyError。
    """
    return {
        "servers": [
            {
                "id": i, "name": i.upper(), "state": s, "health": h,
                "cpu": 1.5, "memory_bytes": 128 * 1024 * 1024,
                "memory_limit": 1024 * 1024 * 1024, "disk_bytes": 0, "players": p,
            }
            for i, s, h, p in rows
        ],
        "errors": [],
    }


@pytest.fixture
def no_sleep(monkeypatch):
    async def fake_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)


class Clock:
    """可手动推进的假时钟，用来确定性地测「持续了多久」。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


# ------------------------------------------------------------------ _fmt_duration

@pytest.mark.parametrize("seconds,expect", [
    (0, "0秒"),
    (9, "9秒"),
    (59, "59秒"),
    (60, "1分00秒"),
    (192, "3分12秒"),
    (3600, "1时00分"),
    (3900, "1时05分"),
    (86400, "1天00时"),
    (86400 + 3 * 3600, "1天03时"),
    (2.7, "2秒"),
])
def test_fmt_duration(seconds, expect) -> None:
    assert server._fmt_duration(seconds) == expect


@pytest.mark.parametrize("bad", [None, -1, "x"])
def test_fmt_duration_bad(bad) -> None:
    assert server._fmt_duration(bad) == "未知"


# ------------------------------------------------------------------ 表格列

def _table(**row_kw) -> str:
    row = {
        "id": "smp", "name": "SMP", "state": "stopped", "health": "abnormal",
        "cpu": 1.5, "memory_bytes": 128 * 1024 * 1024,
        "memory_limit": 1024 * 1024 * 1024, "disk_bytes": 0, "players": None,
    }
    row.update(row_kw)
    return server._format_status({"servers": [row], "errors": []})


def test_no_age_column_without_field() -> None:
    """单次查询没有上一轮数据，不该凭空多一列。"""
    assert "异常持续" not in _table()


def test_age_column_when_field_present() -> None:
    out = _table(state_age_seconds=192)
    assert "异常持续" in out
    assert "3分12秒" in out


def test_healthy_row_shows_dash() -> None:
    """健康的服务器不写「已运行 3 天」——这一列只回答「出问题多久了」。"""
    out = _table(health="healthy", state="running", state_age_seconds=5)
    assert "5秒" not in out


def test_unhealthy_row_shows_duration() -> None:
    out = _table(health="abnormal", state_age_seconds=3600)
    assert "1时00分" in out


def test_age_column_coexists_with_backup_and_players() -> None:
    row = {
        "id": "smp", "name": "SMP", "state": "stopped", "health": "abnormal",
        "cpu": 1.5, "memory_bytes": 128 * 1024 * 1024,
        "memory_limit": 1024 * 1024 * 1024, "disk_bytes": 0,
        "players": None, "state_age_seconds": 90,
        "backup_age_days": 3.0, "backup_ok_count": 1,
    }
    out = server._format_status({"servers": [row], "errors": []})
    header = out.splitlines()[0]
    assert "磁盘" in header and "异常持续" in header and "最新备份" in header
    assert "1分30秒" in out and "3 天前" in out


def test_table_stays_aligned_with_age_column() -> None:
    row = {
        "id": "smp", "name": "SMP 生存服", "state": "stopped", "health": "abnormal",
        "cpu": 1.5, "memory_bytes": 128 * 1024 * 1024,
        "memory_limit": 1024 * 1024 * 1024, "disk_bytes": 0,
        "players": None, "state_age_seconds": 192,
    }
    lines = server._format_status({"servers": [row], "errors": []}).splitlines()
    assert server._display_width(lines[0]) == len(lines[1]), "表头与横线要等宽"


# ------------------------------------------------------------------ 计时逻辑

def test_first_round_has_no_age(no_sleep) -> None:
    """首轮硬算会显示成 0 秒——那是假信息，所以首轮不带这个字段。"""
    clock = Clock()
    seq = [_data(("s1", "running", "healthy", 2))]
    seen: list[dict] = []

    async def fetch():
        return seq.pop(0)

    asyncio.run(server._watch_loop(
        fetch, interval=5, emit=lambda d, first: seen.append(d),
        heartbeat=lambda d: None, max_rounds=1, clock=clock,
    ))
    assert "state_age_seconds" not in seen[0]["servers"][0]


def test_age_accumulates_while_state_unchanged(no_sleep) -> None:
    clock = Clock()
    seq = [_data(("s1", "stopped", "abnormal", 0))] * 3
    seen: list[dict] = []

    async def fetch():
        return seq.pop(0)

    async def run() -> None:
        await server._watch_loop(
            fetch, interval=60, emit=lambda d, first: seen.append(d),
            heartbeat=lambda d: seen.append(d), max_rounds=3, clock=clock,
        )

    # 每轮之间手动推进 60 秒（fake_sleep 不推进，所以这里靠自己）
    orig_sleep = server.asyncio.sleep

    async def tick_sleep(_d: float) -> None:
        clock.tick(60)

    server.asyncio.sleep = tick_sleep
    try:
        asyncio.run(run())
    finally:
        server.asyncio.sleep = orig_sleep

    # 首轮不带该字段（见 test_first_round_has_no_age），从第二轮起才有
    ages = [d["servers"][0]["state_age_seconds"] for d in seen[1:]]
    assert ages == [60, 120], f"第 2、3 轮应累积到 60/120 秒，实际 {ages}"


def test_state_change_resets_age(no_sleep) -> None:
    clock = Clock()
    seq = [
        _data(("s1", "stopped", "abnormal", 0)),
        _data(("s1", "stopped", "abnormal", 0)),
        _data(("s1", "running", "healthy", 0)),   # 恢复了 → 重置
    ]
    seen: list[dict] = []

    async def fetch():
        return seq.pop(0)

    orig_sleep = server.asyncio.sleep

    async def tick_sleep(_d: float) -> None:
        clock.tick(60)

    server.asyncio.sleep = tick_sleep
    try:
        asyncio.run(server._watch_loop(
            fetch, interval=60, emit=lambda d, f: seen.append(d),
            heartbeat=lambda d: seen.append(d), max_rounds=3, clock=clock,
        ))
    finally:
        server.asyncio.sleep = orig_sleep

    assert seen[1]["servers"][0]["state_age_seconds"] == 60
    assert seen[2]["servers"][0]["state_age_seconds"] == 0, "状态变了要重新计时"


def test_players_change_does_not_reset_age(no_sleep) -> None:
    """在线人数上下浮动不是「状态变了」，不能让「已异常 2 小时」反复归零。"""
    clock = Clock()
    seq = [
        _data(("s1", "running", "healthy", 2)),
        _data(("s1", "running", "healthy", 5)),   # 只有人数变了
    ]
    seen: list[dict] = []

    async def fetch():
        return seq.pop(0)

    orig_sleep = server.asyncio.sleep

    async def tick_sleep(_d: float) -> None:
        clock.tick(60)

    server.asyncio.sleep = tick_sleep
    try:
        asyncio.run(server._watch_loop(
            fetch, interval=60, emit=lambda d, f: seen.append(d),
            heartbeat=lambda d: seen.append(d), max_rounds=2, clock=clock,
        ))
    finally:
        server.asyncio.sleep = orig_sleep

    assert seen[1]["servers"][0]["state_age_seconds"] == 60


def test_removed_server_drops_its_timer(no_sleep) -> None:
    """服务器被删掉后不该留着旧计时——否则同名再出现会从很久以前算起。"""
    clock = Clock()
    seq = [
        _data(("s1", "running", "healthy", 1), ("s2", "stopped", "abnormal", 0)),
        _data(("s1", "running", "healthy", 1)),          # s2 消失
        _data(("s1", "running", "healthy", 1), ("s2", "stopped", "abnormal", 0)),
    ]
    seen: list[dict] = []

    async def fetch():
        return seq.pop(0)

    orig_sleep = server.asyncio.sleep

    async def tick_sleep(_d: float) -> None:
        clock.tick(60)

    server.asyncio.sleep = tick_sleep
    try:
        asyncio.run(server._watch_loop(
            fetch, interval=60, emit=lambda d, f: seen.append(d),
            heartbeat=lambda d: seen.append(d), max_rounds=3, clock=clock,
        ))
    finally:
        server.asyncio.sleep = orig_sleep

    age = seen[2]["servers"][1]["state_age_seconds"]
    assert age == 0, f"s2 重新出现应从 0 开始计时（连续存在应是 120），实际 {age}"


def test_loop_still_returns_last_data(no_sleep) -> None:
    seq = [_data(("s1", "running", "healthy", 1))] * 2
    clock = Clock()

    async def fetch():
        return seq.pop(0)

    out = asyncio.run(server._watch_loop(
        fetch, interval=5, emit=lambda d, f: None, heartbeat=lambda d: None,
        max_rounds=2, clock=clock,
    ))
    assert out["servers"][0]["id"] == "s1"


# ------------------------------------------------------------------ 输出（走真实 _run_status 路径）

@pytest.fixture
def panel_env(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


def _drive_watch(monkeypatch, seq, rounds, clock, tick, *, as_json: bool = False):
    """跑真实 ``_run_status`` 的 watch 分支，时钟按 tick 秒推进。"""

    async def fake_status(sid, with_backups=False):
        return seq.pop(0)

    orig = server.asyncio.sleep
    counter = {"n": 0}

    async def tick_sleep(_d: float) -> None:
        clock.tick(tick)
        counter["n"] += 1

    async def fake_sleep_with_clock(d: float) -> None:
        await tick_sleep(d)

    monkeypatch.setattr(server, "_status_data", fake_status)
    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep_with_clock)
    # clock 参数是 _watch_loop 才有的，_run_status 内部不传 clock，
    # 所以这里直接替换 time.monotonic 让计时走假时钟。
    monkeypatch.setattr(server.time, "monotonic", lambda: clock.now)
    try:
        args = ["--status", "--watch", "--interval", "60", "--rounds", str(rounds)]
        if as_json:
            args.append("--json")
        server._run_status(args)
    finally:
        server.asyncio.sleep = orig


def test_watch_text_shows_age_column(panel_env, monkeypatch, capsys) -> None:
    """表格只在「首次」和「状态变化」时重画，所以要有台服务器发生变化。

    场景：s1 一直异常（已 60 秒），s2 这轮恢复了 → 触发重画；
    重画出来的表里 s1 应显示「1分00秒」，s2 恢复健康所以是「-」。
    """
    clock = Clock()
    seq = [
        _data(("s1", "stopped", "abnormal", 0), ("s2", "stopped", "abnormal", 0)),
        _data(("s1", "stopped", "abnormal", 0), ("s2", "running", "healthy", 0)),
    ]
    _drive_watch(monkeypatch, seq, 2, clock, tick=60)
    out = capsys.readouterr().out
    assert "首次" in out and "状态变化" in out
    assert "异常持续" not in out.split("状态变化")[0], "首轮还不知道持续多久，不该有这一列"
    assert "异常持续" in out.split("状态变化")[1]
    assert "1分00秒" in out


def test_watch_heartbeat_reports_longest_abnormal(panel_env, monkeypatch, capsys) -> None:
    clock = Clock()
    seq = [_data(("s1", "stopped", "abnormal", 0))] * 3
    _drive_watch(monkeypatch, seq, 3, clock, tick=60)
    out = capsys.readouterr().out
    beats = [ln for ln in out.splitlines() if "无变化" in ln]
    assert len(beats) == 2
    assert "最长异常 s1 已 1分00秒" in beats[-1] or "最长异常 s1 已 1分00秒" in beats[0]


def test_watch_heartbeat_quiet_when_all_healthy(panel_env, monkeypatch, capsys) -> None:
    clock = Clock()
    seq = [_data(("s1", "running", "healthy", 2))] * 2
    _drive_watch(monkeypatch, seq, 2, clock, tick=60)
    out = capsys.readouterr().out
    assert "最长异常" not in out


def test_watch_json_carries_state_age(panel_env, monkeypatch, capsys) -> None:
    """--watch --json 只在变化时输出一行；那一行里要带持续时间。"""
    clock = Clock()
    seq = [
        _data(("s1", "stopped", "abnormal", 0), ("s2", "stopped", "abnormal", 0)),
        _data(("s1", "stopped", "abnormal", 0), ("s2", "running", "healthy", 0)),
    ]
    _drive_watch(monkeypatch, seq, 2, clock, tick=60, as_json=True)
    objs = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    assert len(objs) == 2, "首轮 + 变化各一行"
    s1 = [r for r in objs[1]["servers"] if r["id"] == "s1"][0]
    assert s1["state_age_seconds"] == 60
    s2 = [r for r in objs[1]["servers"] if r["id"] == "s2"][0]
    assert s2["state_age_seconds"] == 0, "刚恢复的那台才刚开始计时"
