"""负载告警的整条链路（scan_and_notify 里的接线）。

``check_resource_thresholds()`` 本身在 test_alert_thresholds.py 里有 15 个单元
测试，但**它怎么被 scan_and_notify 用起来**一直是零覆盖：

  - 状态健康时才会去查资源（``if not state_bad and ...``）
  - 命中后把状态合成为 ``high_load``
  - res_bad 里的说明要进入告警正文
  - 同样受「连续确认」与「冷却去重」约束
  - 恢复后要清零确认计数

这些是 README 里宣传的 "Alert on load, not just on death"，一条没测就等于
没人保证它真的会叫。
"""

from __future__ import annotations

import httpx
import pytest

import pteropilot.alerts as alerts


def _servers_payload(ids: list[str]) -> dict:
    return {
        "data": [{"attributes": {"identifier": i, "name": i}} for i in ids],
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(ids)}},
    }


def _resources(state: str = "running", cpu: float = 1.0,
               mem: int = 100, lim: int = 1000) -> dict:
    return {
        "current_state": state,
        "resources": {"cpu_absolute": cpu, "memory_bytes": mem, "memory_limit": lim},
    }


@pytest.fixture
def env_panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel")
    monkeypatch.setenv("PANEL_API_KEY", "test")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


@pytest.fixture
def sent(monkeypatch):
    """捕获真正发出去的 webhook 内容。"""
    out: list[str] = []

    async def spy(webhook, content, token=None):
        out.append(content)

    monkeypatch.setattr(alerts, "send_webhook", spy)
    return out


# ------------------------------------------------------------------ 触发

async def test_cpu_over_threshold_notifies(fake, env_panel, sent):
    fake.route("GET", "s1/resources", _resources(cpu=95.0))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, include_log_signals=False,
    )
    assert result["abnormal"] == 1, "状态 running 但 CPU 超限，也该算异常"
    assert result["notified"] == 1
    assert "high_load" in sent[0], "合成状态要写成 high_load，不能写成「状态异常（running）」"
    assert "CPU 95.0%" in sent[0], "正文要带上超限的具体数值"


async def test_under_threshold_is_silent(fake, env_panel, sent):
    fake.route("GET", "s1/resources", _resources(cpu=10.0))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, include_log_signals=False,
    )
    assert result["abnormal"] == 0
    assert sent == []


async def test_memory_over_threshold_notifies(fake, env_panel, sent):
    fake.route("GET", "s1/resources", _resources(mem=950, lim=1000))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", memory_threshold=90, include_log_signals=False,
    )
    assert "内存" in sent[0]
    assert "95" in sent[0]


async def test_both_over_reports_both(fake, env_panel, sent):
    fake.route("GET", "s1/resources", _resources(cpu=99.0, mem=990, lim=1000))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, memory_threshold=90,
        include_log_signals=False,
    )
    assert "CPU" in sent[0] and "内存" in sent[0]


async def test_thresholds_zero_skips_resource_check(fake, env_panel, sent, monkeypatch):
    """两个阈值都是 0 时不该去算资源占用。

    注意 ``resources()`` 本身仍然会被调一次——那是为了拿 ``current_state``，
    跟阈值无关。所以这里监视的是 ``check_resource_thresholds``，而不是资源接口。
    """
    calls: list[tuple] = []

    def spy(res, cpu_threshold=0.0, memory_threshold=0.0, disk_threshold=0.0):
        calls.append((cpu_threshold, memory_threshold, disk_threshold))
        return []

    monkeypatch.setattr(alerts, "check_resource_thresholds", spy)
    fake.route("GET", "s1/resources", _resources(cpu=99.0))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=0, memory_threshold=0,
        include_log_signals=False,
    )
    assert result["abnormal"] == 0
    assert calls == [], "阈值为 0 时不该调 check_resource_thresholds"


async def test_thresholds_nonzero_does_call_check(fake, env_panel, sent, monkeypatch):
    """对照组：给了阈值就一定要算（否则上一条测试可能因为压根没走到而假通过）。"""
    calls: list[tuple] = []

    def spy(res, cpu_threshold=0.0, memory_threshold=0.0, disk_threshold=0.0):
        calls.append((cpu_threshold, memory_threshold, disk_threshold))
        return []

    monkeypatch.setattr(alerts, "check_resource_thresholds", spy)
    fake.route("GET", "s1/resources", _resources(cpu=99.0))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, include_log_signals=False,
    )
    assert calls == [(85, 0.0, 0.0)], "CPU 阈值应当被传下去（磁盘阈值默认为 0）"


async def test_abnormal_state_wins_over_resource_check(fake, env_panel, sent):
    """已经 offline 的服务器不去查资源阈值——状态本身就够说明问题了。"""
    fake.route("GET", "s1/resources", {"current_state": "offline"})
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, include_log_signals=False,
    )
    assert "offline" in sent[0]
    assert "high_load" not in sent[0], "离线优先，不能被合成为 high_load"


# ------------------------------------------------------------------ 与确认/冷却的配合

async def test_load_alert_respects_confirm_count(fake, env_panel, sent, tmp_path):
    state_file = tmp_path / "state.json"
    fake.route("GET", "s1/resources", _resources(cpu=95.0))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    first = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, confirm_count=2,
        state_file=state_file, include_log_signals=False,
    )
    assert first["pending"] == 1 and first["notified"] == 0, "第一次应当只是计数"

    second = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, confirm_count=2,
        state_file=state_file, include_log_signals=False,
    )
    assert second["notified"] == 1, "连续第二次才推"
    assert "high_load" in sent[0]


async def test_load_alert_respects_cooldown(fake, env_panel, sent, tmp_path):
    state_file = tmp_path / "state.json"
    fake.route("GET", "s1/resources", _resources(cpu=95.0))
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, cooldown_minutes=60,
        state_file=state_file, include_log_signals=False,
    )
    second = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85, cooldown_minutes=60,
        state_file=state_file, include_log_signals=False,
    )
    assert second["suppressed"] == 1
    assert len(sent) == 1, "冷却期内不重复推"


async def test_recovery_clears_confirm_counter(fake, env_panel, sent, tmp_path):
    """高负载恢复后确认计数要清零，否则下次会少等一轮就报。"""
    state_file = tmp_path / "state.json"
    fake.route("GET", "s1/resources", [
        _resources(cpu=95.0),   # 第 1 次：超限，计数 1
        _resources(cpu=10.0),   # 第 2 次：恢复，清零
        _resources(cpu=95.0),   # 第 3 次：又超限，计数 1（不该直接推）
    ])
    fake.route("GET", "s1/console", httpx.Response(200, text=""))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    for _ in range(3):
        await alerts.scan_and_notify(
            "https://hook.example/send", cpu_threshold=85, confirm_count=2,
            state_file=state_file, include_log_signals=False,
        )
    assert sent == [], "三次都是第一轮命中，计数被清零过，不该推送"


# ------------------------------------------------------------------ dry-run 也能看到

async def test_dry_run_plans_load_alert(fake, env_panel, sent):
    fake.route("GET", "s1/resources", _resources(cpu=95.0))
    fake.route("GET", "/api/client", _servers_payload(["s1"]))

    result = await alerts.scan_and_notify(
        "https://hook.example/send", cpu_threshold=85,
        include_log_signals=False, dry_run=True,
    )
    assert result["planned"] == 1
    assert result["plans"][0]["state"] == "high_load"
    assert sent == [], "演练模式不该真的发"
