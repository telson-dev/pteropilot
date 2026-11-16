"""``pteropilot-alerts --disk``：磁盘使用率告警。

背景：``check_resource_thresholds`` 的 docstring 里早就写着「内存/磁盘用使用率」，
但磁盘那一项从来没实现过——文档承诺了、代码没给。磁盘写满会让存档写不进去，
是游戏服务器最常见的事故之一，所以这一项该补。

并且这一轮刚给 `--status` 加了磁盘列，告警这边跟上才闭环。
"""

from __future__ import annotations

import httpx
import pytest

import pteropilot.alerts as alerts

_GB = 1024 ** 3


def _res(disk=0, lim=None, cpu=1.0, mem=1, mem_lim=2) -> dict:
    r = {"cpu_absolute": cpu, "memory_bytes": mem, "memory_limit": mem_lim, "disk_bytes": disk}
    if lim is not None:
        r["disk_limit"] = lim
    return {"resources": r}


# ------------------------------------------------------------------ 纯函数

def test_disk_disabled_is_quiet() -> None:
    assert alerts.check_resource_thresholds(_res(disk=9 * _GB, lim=10 * _GB), 0, 0, 0) == []


def test_disk_under_threshold() -> None:
    assert alerts.check_resource_thresholds(_res(disk=5 * _GB, lim=10 * _GB), 0, 0, 85) == []


def test_disk_at_threshold_alerts() -> None:
    """等于阈值就告警（与 CPU/内存口径一致）。"""
    out = alerts.check_resource_thresholds(_res(disk=9 * _GB, lim=10 * _GB), 0, 0, 90)
    assert len(out) == 1
    assert "磁盘" in out[0] and "90%" in out[0]


def test_disk_over_threshold_reports_ratio() -> None:
    out = alerts.check_resource_thresholds(_res(disk=95 * _GB // 10, lim=10 * _GB), 0, 0, 85)
    assert "9.5G" in out[0] or "9.5G /" in out[0]
    assert "95%" in out[0]


def test_disk_without_limit_skipped() -> None:
    """面板没给配额（字段缺失或 0）时跳过，不能拿 0 当分母。"""
    assert alerts.check_resource_thresholds(_res(disk=9 * _GB, lim=None), 0, 0, 85) == []
    assert alerts.check_resource_thresholds(_res(disk=9 * _GB, lim=0), 0, 0, 85) == []


def test_disk_missing_bytes_skipped() -> None:
    assert alerts.check_resource_thresholds({"resources": {"disk_limit": 10 * _GB}}, 0, 0, 85) == []


def test_disk_and_memory_both_reported() -> None:
    """磁盘与内存可以同时超限，两条都要报。"""
    out = alerts.check_resource_thresholds(
        _res(disk=9 * _GB, lim=10 * _GB, mem=95, mem_lim=100), 0, 90, 85
    )
    assert len(out) == 2
    assert any("内存" in x for x in out)
    assert any("磁盘" in x for x in out)


# ------------------------------------------------------------------ 整条链路

def _servers_payload(ids: list[str]) -> dict:
    return {
        "data": [{"attributes": {"identifier": i, "name": i}} for i in ids],
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(ids)}},
    }


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel")
    monkeypatch.setenv("PANEL_API_KEY", "test")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


@pytest.fixture
def sent(monkeypatch):
    out: list[str] = []

    async def spy(webhook, content, token=None):
        out.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    return out


async def test_disk_over_threshold_notifies(fake, env_panel, sent):
    fake.route("GET", "s1/resources", {
        "current_state": "running",
        "resources": {"cpu_absolute": 1.0, "memory_bytes": 1, "memory_limit": 2,
                      "disk_bytes": 95 * _GB // 100, "disk_limit": _GB},
    })
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", disk_threshold=85, include_log_signals=False,
    )
    assert result["abnormal"] == 1
    assert "high_load" in sent[0]
    assert "磁盘" in sent[0]


async def test_disk_disabled_does_not_alert(fake, env_panel, sent):
    fake.route("GET", "s1/resources", {
        "current_state": "running",
        "resources": {"cpu_absolute": 1.0, "memory_bytes": 1, "memory_limit": 2,
                      "disk_bytes": 99 * _GB // 100, "disk_limit": _GB},
    })
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", include_log_signals=False,
    )
    assert result["abnormal"] == 0
    assert sent == []


def test_disk_threshold_can_be_set_per_server() -> None:
    """alerts.json 里按服务器覆盖磁盘阈值。"""
    cfg = {
        "default": {"disk_threshold": 80},
        "servers": {"big-disk": {"disk_threshold": 95}},
    }
    eff = alerts.server_effective(cfg, "big-disk", 1, 0, "abnormal", disk_threshold=50)
    assert eff["disk_threshold"] == 95.0

    other = alerts.server_effective(cfg, "small", 1, 0, "abnormal", disk_threshold=50)
    assert other["disk_threshold"] == 80.0, "服务器条目没有 → 用 default"
