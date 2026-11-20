"""``--doctor`` 检查本机玩家采样是否还在更新。

同样的静默失效主题：用户按文档挂了 cron 定时 ``--players --record``，
某天 cron 停了（或机器重启后忘了恢复），采样就悄悄停在几天前，
而没有任何地方会告诉他——看到的历史趋势会一直失真。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pteropilot import playerlog, server

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _write(dirpath: Path, server_id: str, ages_days: list[float]) -> None:
    p = playerlog.log_path(server_id, dirpath)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"t": (NOW - timedelta(days=a)).strftime("%Y-%m-%dT%H:%M:%SZ"), "p": ["p1"]})
        for a in ages_days
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ latest_sample

def test_latest_sample_empty(tmp_path: Path) -> None:
    assert playerlog.latest_sample(tmp_path) is None


def test_latest_sample_picks_newest_across_servers(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [5, 1])
    _write(tmp_path, "lobby", [3])
    newest = playerlog.latest_sample(tmp_path)
    assert newest is not None
    assert newest[0] == "smp", "近 1 天那条来自 smp"
    assert newest[1] == NOW - timedelta(days=1)


def test_latest_sample_skips_broken_file(tmp_path: Path) -> None:
    """某台文件尾部是坏行时跳过它，不影响其余。"""
    _write(tmp_path, "good", [1])
    p = playerlog.log_path("broken", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json}\n", encoding="utf-8")
    newest = playerlog.latest_sample(tmp_path)
    assert newest is not None and newest[0] == "good"


def test_latest_sample_ignores_unparsable_time(tmp_path: Path) -> None:
    p = playerlog.log_path("weird", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"t": "not-a-date", "p": []}) + "\n", encoding="utf-8")
    assert playerlog.latest_sample(tmp_path) is None


# ------------------------------------------------------------------ check_playerlog

def test_no_samples_is_off_not_problem(tmp_path: Path) -> None:
    r = server.check_playerlog(directory=tmp_path)
    assert r["problems"] == []
    assert "还没有记录" in r["lines"][0]


def test_fresh_sample_is_quiet(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [0.2])
    r = server.check_playerlog(directory=tmp_path, now=NOW)
    assert r["problems"] == []
    assert "最近一条" in r["lines"][0]
    assert r["hints"] == [], "新鲜时不该有提示"


def test_stale_sample_hints_cron_may_be_dead(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [10])
    r = server.check_playerlog(directory=tmp_path, now=NOW)
    assert r["problems"] == [], "用户可能只是手动采过，不该判成配置错"
    assert len(r["hints"]) == 1
    h = r["hints"][0]
    assert "已经 10 天没更新了" in h, "文案要通顺"
    assert "cron" in h, "要指向最可能的原因"


def test_exactly_at_threshold_is_quiet(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [3])
    r = server.check_playerlog(directory=tmp_path, now=NOW, max_age_days=3)
    assert r["hints"] == []


def test_minutes_and_hours_wording(tmp_path: Path) -> None:
    _write(tmp_path, "smp", [30 / 1440])       # 30 分钟前
    r = server.check_playerlog(directory=tmp_path, now=NOW)
    assert "分钟前" in r["lines"][0]

    _write(tmp_path, "smp", [0.5])             # 12 小时前
    r = server.check_playerlog(directory=tmp_path, now=NOW)
    assert "小时前" in r["lines"][0]


def test_line_names_the_server(tmp_path: Path) -> None:
    _write(tmp_path, "lobby", [1])
    r = server.check_playerlog(directory=tmp_path, now=NOW)
    assert "lobby" in r["lines"][0]


# ------------------------------------------------------------------ doctor 集成

@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()


def test_doctor_prints_playerlog_line(env_panel, monkeypatch, tmp_path, capsys) -> None:
    _write(tmp_path, "smp", [0.2])
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    server._run_doctor(skip_network=True)
    out = capsys.readouterr().out
    assert "玩家采样：最近一条在" in out


def test_doctor_hints_when_stale(env_panel, monkeypatch, tmp_path, capsys) -> None:
    _write(tmp_path, "smp", [10])
    monkeypatch.setattr(playerlog, "DEFAULT_DIR", tmp_path)
    server._run_doctor(skip_network=True)
    out = capsys.readouterr().out
    assert "先确认它还在跑" in out
