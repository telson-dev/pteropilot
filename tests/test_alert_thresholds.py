"""资源阈值告警（--cpu / --memory）的测试。

背景：告警原来只看「服务器还在不在线」——running 但 CPU 打满、
内存快爆的服务器不会叫。这几项补上，并锁住几个容易写错的边界：
不限额的服务器不能拿 0 去算百分比、阈值 0 表示关闭、合成状态是 high_load。
"""

from __future__ import annotations

import pytest

from pteropilot.alerts import check_resource_thresholds


def _res(cpu=None, mem=None, lim=None):
    r = {}
    if cpu is not None:
        r["cpu_absolute"] = cpu
    if mem is not None:
        r["memory_bytes"] = mem
    if lim is not None:
        r["memory_limit"] = lim
    return {"current_state": "running", "resources": r}


# ---------------- 关闭阈值 ----------------

def test_no_threshold_never_alerts():
    assert check_resource_thresholds(_res(cpu=99, mem=10**12, lim=10**12), 0, 0) == []


# ---------------- CPU ----------------

def test_cpu_below_threshold():
    assert check_resource_thresholds(_res(cpu=50), 85, 0) == []


def test_cpu_at_threshold_alerts():
    """等于阈值也算超——运维习惯是「到 85 就叫」，不是「超过 85」。"""
    out = check_resource_thresholds(_res(cpu=85), 85, 0)
    assert len(out) == 1 and "85.0%" in out[0]


def test_cpu_above_threshold():
    out = check_resource_thresholds(_res(cpu=92.5), 85, 0)
    assert "92.5%" in out[0] and "85" in out[0]


def test_cpu_missing_field_is_silent():
    assert check_resource_thresholds(_res(), 85, 0) == []


def test_cpu_non_numeric_is_silent():
    assert check_resource_thresholds({"resources": {"cpu_absolute": "?"}}, 85, 0) == []


# ---------------- 内存 ----------------

def test_memory_percent_alerts():
    res = _res(mem=5.8 * 1024**3, lim=6 * 1024**3)
    out = check_resource_thresholds(res, 0, 90)
    assert len(out) == 1
    assert "97%" in out[0] and "90" in out[0]
    assert "5939" in out[0] and "6144" in out[0]


def test_memory_under_threshold():
    res = _res(mem=1 * 1024**3, lim=6 * 1024**3)
    assert check_resource_thresholds(res, 0, 90) == []


def test_memory_unlimited_skipped():
    """memory_limit=0 表示不限额：不能拿 0 做分母算出假百分比。"""
    assert check_resource_thresholds(_res(mem=10**12, lim=0), 0, 50) == []


def test_memory_limit_missing_skipped():
    assert check_resource_thresholds(_res(mem=10**12), 0, 50) == []


# ---------------- 同时超 ----------------

def test_both_over():
    res = _res(cpu=95, mem=5.9 * 1024**3, lim=6 * 1024**3)
    out = check_resource_thresholds(res, 85, 90)
    assert len(out) == 2
    assert any("CPU" in x for x in out) and any("内存" in x for x in out)


def test_flat_dict_without_resources_key():
    """面板偶尔直接给扁平结构（没有外层 resources），也要能读。"""
    flat = {"cpu_absolute": 99, "memory_bytes": 100, "memory_limit": 100}
    out = check_resource_thresholds(flat, 85, 90)
    assert len(out) == 2


def test_empty_dict_is_safe():
    assert check_resource_thresholds({}, 85, 90) == []
    assert check_resource_thresholds({"resources": None}, 85, 90) == []


# ---------------- 合成状态 ----------------

def test_high_load_message_shape():
    """告警文案里要能看出「哪项超了多少」，不然收到通知也不知道该干嘛。"""
    out = check_resource_thresholds(_res(cpu=91.23), 85, 0)
    assert "≥ 阈值" in out[0]
