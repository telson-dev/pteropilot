"""计划执行：``--in`` / ``--at`` 的解析与等待。

这块逻辑一直没测试覆盖（在 CI 覆盖率报告里连续几轮都是未覆盖行）。
写测试时逮到一个真 bug：``--in nan`` 会让 ``time.sleep(nan)`` 抛
ValueError，而 ``_wait_until`` 只捕 KeyboardInterrupt，于是用户看到一屏堆栈。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pteropilot import server


# ------------------------------------------------------------------ 解析时长

@pytest.mark.parametrize("raw,expect", [
    ("90", 90.0),
    ("5s", 5.0),
    ("5m", 300.0),
    ("2h", 7200.0),
    ("1d", 86400.0),
    ("0", 0.0),
    ("1.5h", 5400.0),
    (" 5M ", 300.0),          # 空格与大小写都该容忍
])
def test_parse_duration_ok(raw: str, expect: float) -> None:
    assert server._parse_duration(raw) == pytest.approx(expect)


@pytest.mark.parametrize("raw", ["", "  ", "abc", "5x", "s", None])
def test_parse_duration_rejects_garbage(raw) -> None:
    with pytest.raises(ValueError):
        server._parse_duration(raw)


@pytest.mark.parametrize("raw", ["-5", "-5m", "-1h"])
def test_parse_duration_rejects_negative(raw: str) -> None:
    with pytest.raises(ValueError, match="不能为负"):
        server._parse_duration(raw)


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "infinity"])
def test_parse_duration_rejects_non_finite(raw: str) -> None:
    """float() 认得这些字面量。

    nan 更糟：``time.sleep(nan)`` 会抛 ValueError，而 ``_wait_until`` 只捕
    KeyboardInterrupt，于是用户看到一屏堆栈而不是一句人话。
    """
    with pytest.raises(ValueError, match="看不懂"):
        server._parse_duration(raw)


# ------------------------------------------------------------------ 解析时刻

def test_clock_later_today() -> None:
    now = datetime(2026, 11, 16, 10, 0, 0)
    assert server._seconds_until_clock("12:00", now) == pytest.approx(2 * 3600)


def test_clock_already_passed_rolls_to_tomorrow() -> None:
    now = datetime(2026, 11, 16, 10, 0, 0)
    assert server._seconds_until_clock("09:00", now) == pytest.approx(23 * 3600)


def test_clock_exactly_now_rolls_to_tomorrow() -> None:
    """正好到点时顺延一天，而不是返回 0 立刻执行。"""
    now = datetime(2026, 11, 16, 10, 0, 0)
    assert server._seconds_until_clock("10:00", now) == pytest.approx(24 * 3600)


def test_clock_with_seconds() -> None:
    now = datetime(2026, 11, 16, 10, 0, 0)
    assert server._seconds_until_clock("10:00:30", now) == pytest.approx(30)


def test_clock_single_digit_hour() -> None:
    now = datetime(2026, 11, 16, 3, 0, 0)
    assert server._seconds_until_clock("9:30", now) == pytest.approx(6.5 * 3600)


@pytest.mark.parametrize("raw", ["", "abc", "25:00", "10:60", "10:00:60", "10", "1:2:3:4"])
def test_clock_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        server._seconds_until_clock(raw, datetime(2026, 11, 16, 10, 0, 0))


# ------------------------------------------------------------------ argv 抽取

def test_no_schedule_flags() -> None:
    assert server._scheduled_delay(["--broadcast", "say hi"]) == (None, None)


def test_in_flag() -> None:
    delay, err = server._scheduled_delay(["--broadcast", "say hi", "--in", "5m"])
    assert err is None
    assert delay == pytest.approx(300)


def test_at_flag() -> None:
    delay, err = server._scheduled_delay(["--broadcast", "say hi", "--at", "23:59"])
    assert err is None
    assert 0 < delay <= 86400


def test_both_flags_conflict() -> None:
    delay, err = server._scheduled_delay(["--in", "5m", "--at", "03:00"])
    assert delay is None
    assert "--in 和 --at 只能给一个" in err


@pytest.mark.parametrize("argv,msg", [
    (["--in"], "--in 后面要跟时间间隔"),
    (["--at"], "--at 后面要跟时刻"),
])
def test_missing_value(argv: list[str], msg: str) -> None:
    delay, err = server._scheduled_delay(argv)
    assert delay is None
    assert msg in err


def test_both_flags_wins_over_missing_value() -> None:
    """``--in --at`` 两个 flag 都在，先报冲突。

    顺序上冲突判断在取值之前，所以 ``--in --at 03:00`` 报的是「只能给一个」
    而不是「--in 后面要跟间隔」—— 两者都算说对了，不必为此把逻辑绕复杂。
    """
    delay, err = server._scheduled_delay(["--in", "--at"])
    assert delay is None
    assert "只能给一个" in err


def test_bad_value_reports_parse_error() -> None:
    delay, err = server._scheduled_delay(["--in", "banana"])
    assert delay is None
    assert "看不懂的时间间隔" in err


# ------------------------------------------------------------------ 等待

def test_wait_zero_returns_immediately(monkeypatch, capsys) -> None:
    monkeypatch.setattr(server.time, "sleep", lambda s: pytest.fail("不该真的等"))
    assert server._wait_until(0, "x") is True
    assert capsys.readouterr().err == ""


def test_wait_negative_returns_immediately() -> None:
    assert server._wait_until(-5, "x") is True


def test_wait_over_limit_is_refused(monkeypatch, capsys) -> None:
    monkeypatch.setattr(server.time, "sleep", lambda s: pytest.fail("不该真的等"))
    assert server._wait_until(server._MAX_WAIT_SECONDS + 1, "x") is False
    err = capsys.readouterr().err
    assert "上限 24 小时" in err


def test_wait_prints_target_time(monkeypatch, capsys) -> None:
    slept: list[float] = []
    monkeypatch.setattr(server.time, "sleep", lambda s: slept.append(s))
    assert server._wait_until(30, "广播") is True
    assert slept == [30]
    err = capsys.readouterr().err
    assert "将在" in err and "30 秒后" in err and "广播" in err


def test_wait_cancelled_by_ctrl_c(monkeypatch, capsys) -> None:
    def boom(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(server.time, "sleep", boom)
    assert server._wait_until(10, "x") is False
    assert "已取消" in capsys.readouterr().err


def test_max_wait_is_one_day() -> None:
    assert server._MAX_WAIT_SECONDS == 24 * 3600
    assert timedelta(seconds=server._MAX_WAIT_SECONDS).days == 1
