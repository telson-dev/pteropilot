"""玩家活跃历史（playerlog）与 player_stats 工具的测试。

覆盖点：
  * 去重：玩家集合没变化时不重复写
  * 时长估算：按相邻采样间隔累加，且有上限
  * 时段分布：按本地时区落桶
  * 健壮性：损坏行、缺失文件、非法 server_id、超大文件都不应炸
  * 工具契约：player_stats 拒绝 days < 1；list_players 记录失败不影响返回
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pteropilot import playerlog


BASE = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def tmpdir(tmp_path: Path) -> Path:
    return tmp_path


# ---------- record ----------

def test_record_writes_file(tmpdir):
    assert playerlog.record("srv-a", ["alice"], now=BASE, directory=tmpdir) is True
    path = playerlog.log_path("srv-a", tmpdir)
    assert path.exists()
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["p"] == ["alice"]
    assert rows[0]["t"] == "2026-11-14T10:00:00Z"


def test_record_dedupes_same_player_set(tmpdir):
    assert playerlog.record("srv-a", ["alice", "bob"], now=BASE, directory=tmpdir) is True
    # 顺序不同但集合相同 → 跳过
    assert playerlog.record("srv-a", ["bob", "alice"], now=BASE + timedelta(minutes=1), directory=tmpdir) is False
    path = playerlog.log_path("srv-a", tmpdir)
    assert len([l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]) == 1


def test_record_writes_when_set_changes(tmpdir):
    playerlog.record("srv-a", ["alice"], now=BASE, directory=tmpdir)
    assert playerlog.record("srv-a", ["alice", "bob"], now=BASE + timedelta(minutes=1), directory=tmpdir) is True


def test_record_handles_empty_players(tmpdir):
    assert playerlog.record("srv-a", [], now=BASE, directory=tmpdir) is True
    assert playerlog.record("srv-a", [], now=BASE + timedelta(minutes=1), directory=tmpdir) is False


def test_record_ignores_blank_names(tmpdir):
    playerlog.record("srv-a", ["alice", "  ", "bob"], now=BASE, directory=tmpdir)
    path = playerlog.log_path("srv-a", tmpdir)
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["p"] == ["alice", "bob"]


def test_path_is_sanitized(tmpdir):
    # 路径穿越与非法字符不能逃出目录
    playerlog.record("../../evil", ["x"], now=BASE, directory=tmpdir)
    path = playerlog.log_path("../../evil", tmpdir)
    assert path.parent == tmpdir
    assert ".." not in path.name


# ---------- stats ----------

def test_stats_empty_when_no_file(tmpdir):
    data = playerlog.stats("nope", directory=tmpdir)
    assert data["samples"] == 0
    assert data["unique"] == 0
    assert data["players"] == []


def test_stats_accumulates_minutes(tmpdir):
    playerlog.record("s", ["a"], now=BASE, directory=tmpdir)
    playerlog.record("s", ["a", "b"], now=BASE + timedelta(minutes=10), directory=tmpdir)
    playerlog.record("s", ["a"], now=BASE + timedelta(minutes=20), directory=tmpdir)
    data = playerlog.stats("s", directory=tmpdir, now=BASE + timedelta(hours=1))
    names = {p["name"]: p for p in data["players"]}
    # a 出现 3 次，覆盖 10+10=20 分钟；b 出现 1 次（最后一条没有下一条，时长 0）
    assert names["a"]["samples"] == 3
    assert names["a"]["minutes"] == pytest.approx(20.0)
    assert names["b"]["samples"] == 1


def test_stats_caps_slice_minutes(tmpdir):
    """两次采样间隔一小时，只按 MAX_SLICE_MINUTES 折算，避免异常大的时长。"""
    playerlog.record("s", ["a"], now=BASE, directory=tmpdir)
    playerlog.record("s", ["a", "b"], now=BASE + timedelta(hours=1), directory=tmpdir)
    data = playerlog.stats("s", directory=tmpdir, now=BASE + timedelta(hours=2))
    names = {p["name"]: p for p in data["players"]}
    assert names["a"]["minutes"] <= playerlog.MAX_SLICE_MINUTES


def test_stats_respects_days_window(tmpdir):
    old = BASE - timedelta(days=30)
    playerlog.record("s", ["old"], now=old, directory=tmpdir)
    playerlog.record("s", ["new"], now=BASE, directory=tmpdir)
    data = playerlog.stats("s", days=7, directory=tmpdir, now=BASE + timedelta(minutes=5))
    assert [p["name"] for p in data["players"]] == ["new"]


def test_stats_skips_corrupt_lines(tmpdir):
    path = playerlog.log_path("s", tmpdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"t": "2026-11-14T10:00:00Z", "p": ["a"]})
    path.write_text("not json\n\n" + good + "\n" + json.dumps({"t": "bogus", "p": ["x"]}) + "\n", encoding="utf-8")
    data = playerlog.stats("s", directory=tmpdir, now=BASE + timedelta(hours=1))
    assert data["samples"] == 1
    assert data["players"][0]["name"] == "a"


def test_stats_hours_use_local_timezone(tmpdir):
    playerlog.record("s", ["a"], now=BASE, directory=tmpdir)
    data = playerlog.stats("s", directory=tmpdir, now=BASE + timedelta(minutes=5))
    local_hour = BASE.astimezone(datetime.now().astimezone().tzinfo).hour
    assert data["hours"][local_hour] == 1
    assert sum(data["hours"]) == 1


def test_stats_trim_keeps_newest(tmp_path, monkeypatch):
    """超过 MAX_LINES 时保留较新的行。"""
    monkeypatch.setattr(playerlog, "MAX_LINES", 10)
    for i in range(14):
        playerlog.record("s", [f"p{i}"], now=BASE + timedelta(minutes=i), directory=tmp_path)
    path = playerlog.log_path("s", tmp_path)
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) <= 8 + 1  # 裁剪到一半（5）+ 后续追加
    last = json.loads(lines[-1])
    assert last["p"] == ["p13"]


# ---------- format ----------

def test_format_empty_hint():
    text = playerlog.format_stats("srv-1", {"samples": 0, "players": []})
    assert "还没有玩家活跃记录" in text


def test_format_contains_ranking_and_caveat(tmpdir):
    playerlog.record("s", ["alice"], now=BASE, directory=tmpdir)
    playerlog.record("s", ["alice", "bob"], now=BASE + timedelta(minutes=5), directory=tmpdir)
    data = playerlog.stats("s", directory=tmpdir, now=BASE + timedelta(minutes=10))
    text = playerlog.format_stats("s", data, days=7)
    assert "在线时长排行" in text
    assert "alice" in text
    assert "不是权威" in text


def test_format_shows_truncated_notice():
    data = {"samples": 3, "unique": 1, "players": [{"name": "a", "samples": 3, "minutes": 5.0}],
            "hours": [0] * 24, "first": "2026-11-14T10:00:00Z", "last": "2026-11-14T10:05:00Z",
            "truncated": True}
    assert "已被裁剪" in playerlog.format_stats("s", data)


# ---------- 工具层 ----------

def test_player_stats_tool_rejects_bad_days():
    import asyncio
    from pteropilot.server import player_stats

    out = asyncio.run(player_stats("srv-x", days=0))
    assert "至少为 1" in out


def test_player_stats_tool_returns_hint_when_no_data(monkeypatch, tmp_path):
    import asyncio
    from pteropilot import server

    monkeypatch.setattr(server.playerlog, "DEFAULT_DIR", tmp_path)
    out = asyncio.run(server.player_stats("never-seen", days=7))
    assert "还没有玩家活跃记录" in out
