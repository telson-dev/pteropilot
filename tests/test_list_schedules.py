"""list_schedules 工具测试：定时任务展示（cron/启用状态/上次下次运行/步骤数）。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.list_schedules(*args, **kwargs)

    return asyncio.run(run())


def _sched(name="每日重启", minute="0", hour="4", active=True, online=False,
           last="2026-11-08T04:00:00.000000Z", next_="2026-11-09T04:00:00.000000Z",
           tasks=2):
    attrs = {
        "name": name,
        "cron": {"minute": minute, "hour": hour, "day_of_month": "*", "month": "*", "day_of_week": "*"},
        "is_active": active,
        "only_when_online": online,
        "last_run_at": last,
        "next_run_at": next_,
        "relationships": {"tasks": {"data": [{"id": i} for i in range(tasks)]}},
    }
    return {"attributes": attrs}


def test_list_schedules_basic(make_client):
    make_client[2].route(
        "GET", "/servers/s1/schedules", httpx.Response(200, json={"data": [_sched()]})
    )
    out = _call(make_client, "s1")
    assert "共 1 个定时任务" in out
    assert "每日重启" in out
    assert "cron 0 4 * * *" in out          # cron 五段拼装
    assert "启用" in out
    assert "总是执行" in out
    assert "2 步" in out                    # 任务步骤数
    assert "2026-11-08 04:00:00" in out     # 上次运行（T→空格、秒后截断）
    assert "2026-11-09 04:00:00" in out


def test_list_schedules_disabled_and_online_only(make_client):
    make_client[2].route(
        "GET", "/servers/s1/schedules",
        httpx.Response(200, json={"data": [_sched(active=False, online=True)]}),
    )
    out = _call(make_client, "s1")
    assert "已停用" in out
    assert "仅在线时执行" in out


def test_list_schedules_never_run(make_client):
    make_client[2].route(
        "GET", "/servers/s1/schedules",
        httpx.Response(200, json={"data": [_sched(last=None, next_=None)]}),
    )
    out = _call(make_client, "s1")
    assert "从未" in out
    assert "下次运行 —" in out


def test_list_schedules_empty(make_client):
    make_client[2].route("GET", "/servers/s1/schedules", httpx.Response(200, json={"data": []}))
    out = _call(make_client, "s1")
    assert "没有配置定时任务" in out
    assert "schedule_restart" in out          # 引导到临时重启工具


def test_list_schedules_unnamed_and_no_tasks(make_client):
    make_client[2].route(
        "GET", "/servers/s1/schedules",
        httpx.Response(200, json={"data": [{"attributes": {"cron": {"minute": "30", "hour": "*"}}}]}),
    )
    out = _call(make_client, "s1")
    assert "(未命名)" in out
    assert "cron 30 * * * *" in out
