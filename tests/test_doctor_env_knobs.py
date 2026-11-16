"""--doctor 的环境变量自检。

同一类「静默失效」的第三处：
  - PTEROPILOT_CONCURRENCY / RETRIES / RETRY_BASE 非法取值**静默**回退默认
  - PANEL_READONLY 只认 1/true/yes/on，写成 enabled / y / readonly 会被当成
    **关闭** —— 用户以为锁上了写操作，其实没有。这条是安全相关的，必须报出来。
"""

from __future__ import annotations

import pytest

from pteropilot import server


def _chk(**env) -> dict:
    return server.check_env_knobs(env)


# ------------------------------------------------------------------ 未设置

def test_nothing_set_is_quiet() -> None:
    r = _chk()
    assert r["lines"] == [] and r["problems"] == [] and r["hints"] == []


# ------------------------------------------------------------------ 合法值

def test_valid_values_echoed() -> None:
    r = _chk(PTEROPILOT_CONCURRENCY="4", PTEROPILOT_RETRIES="0",
             PTEROPILOT_RETRY_BASE="1.5")
    text = " ".join(r["lines"])
    assert "PTEROPILOT_CONCURRENCY: 4 ✅" in text
    assert "PTEROPILOT_RETRIES: 0 ✅" in text
    assert "PTEROPILOT_RETRY_BASE: 1.5 秒 ✅" in text
    assert r["problems"] == []


def test_whitespace_is_trimmed() -> None:
    r = _chk(PTEROPILOT_CONCURRENCY="  6  ")
    assert "6 ✅" in " ".join(r["lines"])


# ------------------------------------------------------------------ 非法 / 越界

@pytest.mark.parametrize("raw", ["abc", "4x", ""])
def test_concurrency_not_a_number(raw) -> None:
    r = _chk(PTEROPILOT_CONCURRENCY=raw)
    if raw == "":
        assert r["lines"] == []      # 空串等于没设
        return
    assert "不是合法数字" in " ".join(r["lines"])
    assert any("PTEROPILOT_CONCURRENCY" in h for h in r["hints"])


def test_concurrency_clamped_high() -> None:
    r = _chk(PTEROPILOT_CONCURRENCY="999")
    assert "超过上限" in " ".join(r["lines"])
    assert "32" in " ".join(r["lines"])


def test_concurrency_clamped_low() -> None:
    r = _chk(PTEROPILOT_CONCURRENCY="0")
    assert "小于下限" in " ".join(r["lines"])


def test_retries_clamped() -> None:
    r = _chk(PTEROPILOT_RETRIES="9")
    assert "超过上限" in " ".join(r["lines"])


def test_retry_base_float_parse() -> None:
    r = _chk(PTEROPILOT_RETRY_BASE="0.25")
    assert "0.25 秒 ✅" in " ".join(r["lines"])


def test_retry_base_not_number() -> None:
    r = _chk(PTEROPILOT_RETRY_BASE="fast")
    assert "不是合法数字" in " ".join(r["lines"])


# ------------------------------------------------------------------ PANEL_READONLY（安全相关）

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_readonly_recognized_truthy(raw) -> None:
    r = _chk(PANEL_READONLY=raw)
    assert "开启" in " ".join(r["lines"])
    assert r["problems"] == []


@pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
def test_readonly_recognized_falsy(raw) -> None:
    r = _chk(PANEL_READONLY=raw)
    assert "关闭" in " ".join(r["lines"])
    assert r["problems"] == []


@pytest.mark.parametrize("raw", ["enabled", "y", "readonly", "sure", "2"])
def test_readonly_unrecognized_is_a_problem(raw) -> None:
    """用户很可能以为这样写就锁上了，必须明确告诉他没锁。"""
    r = _chk(PANEL_READONLY=raw)
    assert r["lines"] == []
    assert len(r["problems"]) == 1
    msg = r["problems"][0]
    assert "无法识别" in msg
    assert "写操作其实没有被拦" in msg
    assert any("PANEL_READONLY=1" in h for h in r["hints"])


def test_readonly_and_knobs_together() -> None:
    r = _chk(PANEL_READONLY="enabled", PTEROPILOT_CONCURRENCY="2")
    assert "PTEROPILOT_CONCURRENCY: 2 ✅" in " ".join(r["lines"])
    assert len(r["problems"]) == 1
