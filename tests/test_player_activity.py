"""analyze_player_activity 测试：从控制台日志统计玩家进出与高峰时段。"""

from __future__ import annotations

import asyncio

from pteropilot import server


def _run(coro):
    return asyncio.run(coro)


class _StubPanel:
    def __init__(self, log: str) -> None:
        self._log = log

    async def console_log(self, server_id: str) -> str:
        return self._log


def _call(monkeypatch, log: str, **kwargs):
    monkeypatch.setattr(server, "panel", _StubPanel(log))
    return _run(server.analyze_player_activity("s1", **kwargs))


LOG = """[12:01:05] [Server thread/INFO]: Alice joined the game
[12:02:10] [Server thread/INFO]: Bob joined the game
[12:30:00] [Server thread/INFO]: Alice left the game
[13:05:00] [Server thread/INFO]: Alice joined the game
[13:06:00] [Server thread/INFO]: Carol joined the game
[14:00:00] [Server thread/INFO]: Carol left the game
"""


def test_counts_and_ranking(monkeypatch):
    out = _call(monkeypatch, LOG)
    assert "共 3 个玩家名" in out
    assert "Alice: 进 2 次 / 退 1 次" in out
    assert "Bob: 进 1 次 / 退 0 次" in out
    assert "Carol: 进 1 次 / 退 1 次" in out


def test_peak_hours(monkeypatch):
    out = _call(monkeypatch, LOG)
    assert "高峰时段" in out
    assert "12 点" in out and "13 点" in out


def test_still_online_players(monkeypatch):
    out = _call(monkeypatch, LOG)
    assert "日志末尾仍在线" in out
    assert "Alice" in out and "Bob" in out
    # Carol 进 1 退 1，不该出现在"仍在线"里
    still_line = [ln for ln in out.splitlines() if "仍在线" in ln][0]
    assert "Carol" not in still_line


def test_top_limit(monkeypatch):
    out = _call(monkeypatch, LOG, top=1)
    assert "只显示前 1 个" in out
    # 只出现第一个玩家
    assert "Alice" in out


def test_top_is_clamped(monkeypatch):
    out = _call(monkeypatch, LOG, top=999)
    assert "只显示前" not in out or "只显示前 20 个" in out


def test_empty_log(monkeypatch):
    out = _call(monkeypatch, "[12:00:00] Server started")
    assert "没有找到玩家加入/退出记录" in out
    assert "不代表完整历史" not in out


def test_disclaimer_always_present(monkeypatch):
    out = _call(monkeypatch, LOG)
    assert "不代表完整历史" in out


def test_chinese_log_format(monkeypatch):
    """兼容中文客户端日志（加入游戏/离开了游戏）。"""
    log = """[09:10:00] [Server thread/INFO]: 小明 joined the game
[09:20:00] [Server thread/INFO]: 小明 left the game
"""
    out = _call(monkeypatch, log)
    assert "小明" in out
    assert "进 1 次 / 退 1 次" in out
