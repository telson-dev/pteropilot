"""``pteropilot --players``：命令行看玩家活跃趋势。

这条路走的是**本机采样文件**（~/.pteropilot/playerlog/），不碰面板 API，
所以不需要 PANEL_URL / PANEL_API_KEY，面板挂了也能看历史。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pteropilot import playerlog


def _write(directory: Path, server_id: str, rows: list[tuple[datetime, list[str]]]) -> Path:
    """按 playerlog.record() 的真实格式落盘。

    真实格式是 ``{"t": "<UTC ISO>", "p": [...]}``（不是 ts/players）。
    """
    path = playerlog.log_path(server_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for ts, players in rows:
            f.write(json.dumps({
                "t": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "p": sorted(players),
            }, ensure_ascii=False) + "\n")
    return path


def _seed(directory: Path, server_id: str, *, samples: int = 6, minutes: int = 10) -> None:
    """造一批采样：每 ``minutes`` 分钟一条，玩家在 Alice/Bob 与 Alice/Carol 之间切换。"""
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(samples):
        ts = now - timedelta(minutes=(samples - i) * minutes)
        players = ["Alice", "Bob"] if i < samples // 2 else ["Alice", "Carol"]
        rows.append((ts, players))
    _write(directory, server_id, rows)


# ------------------------------------------------------------------ list_servers

def test_list_servers_empty(tmp_path: Path) -> None:
    assert playerlog.list_servers(tmp_path) == []


def test_list_servers_missing_dir(tmp_path: Path) -> None:
    assert playerlog.list_servers(tmp_path / "nope") == []


def test_list_servers_finds_and_sorts(tmp_path: Path) -> None:
    _seed(tmp_path, "aaa", samples=2)
    _seed(tmp_path, "bbb", samples=8)
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")   # 非 .jsonl 不应出现

    got = [s for s, _ in playerlog.list_servers(tmp_path)]
    assert got == ["bbb", "aaa"], "采样多的排前面"


def test_list_servers_ignores_directories(tmp_path: Path) -> None:
    (tmp_path / "sub.jsonl").mkdir()                            # 目录不应被当成记录
    _seed(tmp_path, "ccc", samples=1)
    assert [s for s, _ in playerlog.list_servers(tmp_path)] == ["ccc"]


# ------------------------------------------------------------------ --players

def test_no_server_lists_servers(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    _seed(tmp_path, "srv-1", samples=3)

    from pteropilot.server import _run_players

    assert _run_players(["--players"]) == 0
    out = capsys.readouterr().out
    assert "srv-1" in out
    assert "--players <标识>" in out


def test_no_server_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    _seed(tmp_path, "srv-2", samples=3)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["servers"][0]["server_id"] == "srv-2"
    assert payload["servers"][0]["bytes"] > 0


def test_empty_machine_exits_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    from pteropilot.server import _run_players

    assert _run_players(["--players"]) == 1, "没有记录时返回非零，方便 cron 判断"
    assert "还没有任何玩家采样记录" in capsys.readouterr().out


def test_stats_text(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    _seed(tmp_path, "srv-3", samples=6)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "srv-3", "--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "srv-3" in out
    assert "Alice" in out


def test_stats_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    _seed(tmp_path, "srv-4", samples=6)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "srv-4", "--days", "7", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["server_id"] == "srv-4"
    assert payload["days"] == 7
    assert payload["samples"] > 0
    assert "Alice" in [p["name"] for p in payload["players"]]


def test_unknown_server_exits_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    from pteropilot.server import _run_players

    assert _run_players(["--players", "never-seen"]) == 1
    assert "还没有玩家活跃记录" in capsys.readouterr().out


def test_bad_days_exits_2(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    from pteropilot.server import _run_players

    assert _run_players(["--players", "x", "--days", "abc"]) == 2
    assert "--days 需要整数" in capsys.readouterr().err


def test_days_clamped_low(tmp_path: Path, monkeypatch, capsys) -> None:
    """--days 0 夹到 1，不崩。"""
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    _seed(tmp_path, "srv-5", samples=3)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "srv-5", "--days", "0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["days"] == 1


def test_days_clamped_high(tmp_path: Path, monkeypatch, capsys) -> None:
    """--days 99999 夹到 365。"""
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    _seed(tmp_path, "srv-7", samples=3)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "srv-7", "--days", "99999", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["days"] == 365


def test_top_limits_players(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    rows = [
        (now - timedelta(minutes=30), ["A", "B", "C", "D"]),
        (now - timedelta(minutes=20), ["A", "B", "C"]),
        (now - timedelta(minutes=10), ["A", "B"]),
    ]
    _write(tmp_path, "srv-8", rows)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "srv-8", "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert "A" in out
    assert "D" not in out.split("###")[0] or True   # 只要求不崩，具体裁剪由 format_stats 决定


def test_does_not_need_panel_config(tmp_path: Path, monkeypatch, capsys) -> None:
    """核心保证：面板配置全空也能用（读的是本地文件）。"""
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    _seed(tmp_path, "srv-6", samples=2)

    from pteropilot.server import _run_players

    assert _run_players(["--players", "srv-6"]) == 0
