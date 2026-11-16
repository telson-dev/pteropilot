"""计划执行（--in / --at）的测试。

这块的风险不在「能不能等」，而在时间换算算错：``2h`` 被当成 2 秒、
``03:00`` 算成今天而不是明天——用户半夜爬起来才发现没重启。
所以把解析函数本身测密一点，等待逻辑用 monkeypatch 掉 time.sleep。
"""

from __future__ import annotations

from datetime import datetime

import pytest

import pteropilot.server as srv
from pteropilot.config import Config


# ---------------- _parse_duration ----------------

@pytest.mark.parametrize(
    "raw,expect",
    [
        ("5m", 300),
        ("300s", 300),
        ("300", 300),      # 省略单位 = 秒
        ("2h", 7200),
        ("1d", 86400),
        ("0", 0),
        ("0.5h", 1800),    # 小数也认
        (" 5M ", 300),     # 大小写与空白
    ],
)
def test_parse_duration_ok(raw, expect):
    assert srv._parse_duration(raw) == expect


@pytest.mark.parametrize("raw", ["", "   ", "abc", "5x", "-5m", "m"])
def test_parse_duration_bad(raw):
    with pytest.raises(ValueError):
        srv._parse_duration(raw)


# ---------------- _seconds_until_clock ----------------

def test_clock_later_today():
    now = datetime(2026, 11, 15, 1, 0, 0)
    # 03:00 还没到 → 2 小时
    assert srv._seconds_until_clock("03:00", now=now) == 7200


def test_clock_passed_rolls_to_tomorrow():
    now = datetime(2026, 11, 15, 4, 0, 0)
    # 03:00 已经过了 → 顺延到明天 03:00 = 23 小时
    assert srv._seconds_until_clock("03:00", now=now) == 23 * 3600


def test_clock_exact_now_rolls_forward():
    """正好等于当前时刻时也要顺延，避免 sleep(0) 之后又跑一遍。"""
    now = datetime(2026, 11, 15, 3, 0, 0)
    assert srv._seconds_until_clock("03:00", now=now) == 24 * 3600


def test_clock_with_seconds():
    now = datetime(2026, 11, 15, 1, 0, 0)
    assert srv._seconds_until_clock("03:00:30", now=now) == 7230


@pytest.mark.parametrize("raw", ["", "3", "3:60", "25:00", "aa:bb", "1:2:3:4"])
def test_clock_bad(raw):
    with pytest.raises(ValueError):
        srv._seconds_until_clock(raw, now=datetime(2026, 11, 15, 1, 0, 0))


# ---------------- _scheduled_delay ----------------

def test_scheduled_none_when_absent():
    assert srv._scheduled_delay(["--power", "start"]) == (None, None)


def test_scheduled_in():
    d, err = srv._scheduled_delay(["--in", "5m"])
    assert err is None and d == 300


def test_scheduled_at():
    d, err = srv._scheduled_delay(["--at", "03:00"])
    assert err is None and d is not None and 0 < d <= 86400


def test_scheduled_both_is_error():
    d, err = srv._scheduled_delay(["--in", "5m", "--at", "03:00"])
    assert d is None and "--in 和 --at 只能给一个" in err


def test_scheduled_missing_value():
    d, err = srv._scheduled_delay(["--in"])
    assert d is None and "时间间隔" in err


def test_scheduled_bad_value():
    d, err = srv._scheduled_delay(["--in", "abc"])
    assert d is None and "看不懂" in err


# ---------------- _wait_until ----------------

def test_wait_zero_returns_true():
    assert srv._wait_until(0, "x") is True


def test_wait_over_limit_refused(capsys):
    assert srv._wait_until(25 * 3600, "x") is False
    assert "上限 24 小时" in capsys.readouterr().err


def test_wait_sleeps(monkeypatch):
    slept = {}
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.setdefault("s", s))
    assert srv._wait_until(120, "x") is True
    assert slept["s"] == 120


def test_wait_ctrl_c_cancels(monkeypatch, capsys):
    def boom(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(srv.time, "sleep", boom)
    assert srv._wait_until(60, "x") is False
    assert "已取消" in capsys.readouterr().err


# ---------------- CLI 接线 ----------------

class FakePanel:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def client_servers(self):
        return [{"attributes": {"identifier": "s1", "name": "srv"}}]

    async def resources(self, sid):
        return {"current_state": "running"}

    async def send_command(self, sid, command):
        self.sent.append((sid, command))

    async def power(self, sid, signal):
        self.sent.append((sid, signal))


@pytest.fixture
def fake():
    f = FakePanel()
    srv.panel = f
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=False)
    yield f
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=False)


def test_broadcast_with_in_waits_then_sends(fake, monkeypatch, capsys):
    slept = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.append(s))
    rc = srv._run_broadcast(["--broadcast", "say hi", "--in", "2s"])
    assert rc == 0
    assert slept == [2]
    assert fake.sent == [("s1", "say hi")]


def test_broadcast_ctrl_c_exit_4(fake, monkeypatch, capsys):
    def boom(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(srv.time, "sleep", boom)
    rc = srv._run_broadcast(["--broadcast", "say hi", "--in", "1h"])
    assert rc == 4
    assert fake.sent == []          # 取消后一个字都没发


def test_power_with_in_waits_then_sends(fake, monkeypatch):
    slept = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: slept.append(s))
    rc = srv._run_power(["--power", "start", "--servers", "s1", "--in", "90"])
    assert rc == 0
    assert slept == [90]
    assert fake.sent == [("s1", "start")]


def test_bad_schedule_does_not_touch_panel(fake, capsys):
    rc = srv._run_power(["--power", "start", "--servers", "s1", "--in", "abc"])
    assert rc == 2
    assert fake.sent == []
    assert "看不懂" in capsys.readouterr().err
