"""player_history_stats 工具测试：日志解析（join/leave/lost connection/IP 清理）与统计输出。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.player_history_stats(*args, **kwargs)

    return asyncio.run(run())


def test_parse_events_basic():
    log = (
        "[12:00:01] [Server thread/INFO]: Steve joined the game\n"
        "[12:05:30] [Server thread/INFO]: Alex[/127.0.0.1:5555] joined the game\n"
        "[13:00:00] [Server thread/INFO]: Steve left the game\n"
        "[13:10:00] [Server thread/INFO]: Alex lost connection: Disconnected\n"
        "[13:11:00] [Server thread/INFO]: Server pinged\n"
    )
    ev = server._parse_player_events(log, 100)
    assert [(t, n, k) for t, n, k in ev] == [
        ("12:00:01", "Steve", "joined"),
        ("12:05:30", "Alex", "joined"),
        ("13:00:00", "Steve", "left"),
        ("13:10:00", "Alex", "left"),
    ]


def test_parse_events_max_lines():
    log = "\n".join(f"[12:0{i}:00] [Server thread/INFO]: P{i} joined the game" for i in range(10))
    ev = server._parse_player_events(log, 5)
    assert len(ev) == 5
    assert ev[0][1] == "P5"  # 只保留最近 5 行


def test_player_history_stats_online_and_top(make_client):
    log = (
        "[12:00:00] [Server thread/INFO]: Steve joined the game\n"
        "[12:00:10] [Server thread/INFO]: Steve joined the game\n"
        "[12:01:00] [Server thread/INFO]: Alex joined the game\n"
        "[12:02:00] [Server thread/INFO]: Steve left the game\n"
        "[12:03:00] [Server thread/INFO]: Alex joined the game\n"
    )
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1")
    assert "当前在线（推断）" in out
    assert "Alex（净+2）" in out  # 在线（join2-left0）
    assert "上线次数 Top" in out
    assert "Steve ×2" in out and "Alex ×2" in out
    assert "最近活动" in out


def test_player_history_stats_no_events(make_client):
    log = "[12:00:00] [Server thread/INFO]: Some random line\nAnother line\n"
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1")
    assert "没有发现加入/离开记录" in out


def test_player_history_stats_empty(make_client):
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=""))
    out = _call(make_client, "srv1")
    assert "没有发现加入/离开记录" in out


def test_player_history_stats_ip_cleaned(make_client):
    log = "[12:00:00] [Server thread/INFO]: Steve[/127.0.0.1:5555] joined the game\n"
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1")
    assert "Steve" in out
    assert "127.0.0.1" not in out


def test_player_history_stats_lines_clamped(make_client):
    # lines 低于 100 会拉到 100；不影响无事件时的提示
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text="x\n"))
    out = _call(make_client, "srv1", lines=10)
    assert "没有发现加入/离开记录" in out
