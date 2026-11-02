"""schedule_restart 工具测试：确认拦截 / 只读拒绝 / 分钟钳制 / 公告+定时重启 / 公告失败取消。"""

from __future__ import annotations

import asyncio

from pteropilot import server


def _run(make_client, drain, *args, **kwargs):
    """在同一事件循环里调用工具；drain=True 时排空让后台任务跑完再退出循环。"""
    client, cfg, fake = make_client

    async def scenario():
        out = await server.schedule_restart(*args, **kwargs)
        if drain:
            for _ in range(30):
                await asyncio.sleep(0)
        return out

    return asyncio.run(scenario())


def test_schedule_restart_requires_confirm(make_client):
    fake = make_client[2]
    fake.route("POST", "/command", None)
    out = _run(make_client, False, "srv1", minutes=5)
    assert "高危操作" in out and "confirm" in out
    assert fake.requests == []  # 未发任何请求


def test_schedule_restart_readonly_rejected(make_client):
    client, cfg, fake = make_client
    cfg.readonly = True
    out = _run(make_client, False, "srv1", minutes=5, confirm=True)
    assert "只读模式" in out
    assert fake.requests == []


def test_schedule_restart_broadcast_and_restart(make_client, monkeypatch):
    client, cfg, fake = make_client
    fake.route("POST", "/command", {"ok": True})
    fake.route("POST", "/power", {"ok": True})

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    out = _run(make_client, True, "srv1", minutes=5, confirm=True)
    assert "5 分钟后自动重启" in out

    paths = [r.url.path for r in fake.requests]
    assert any("/command" in p for p in paths)
    assert any("/power" in p for p in paths)
    cmd_req = next(r for r in fake.requests if "/command" in r.url.path)
    assert b"say" in cmd_req.content and b"5" in cmd_req.content


def test_schedule_restart_custom_message(make_client, monkeypatch):
    client, cfg, fake = make_client
    fake.route("POST", "/command", {"ok": True})
    fake.route("POST", "/power", {"ok": True})

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    out = _run(make_client, True, "srv1", minutes=10, message="维护开始，稍后回来", confirm=True)
    assert "10 分钟后自动重启" in out
    cmd_req = next(r for r in fake.requests if "/command" in r.url.path)
    assert "维护开始，稍后回来".encode() in cmd_req.content


def test_schedule_restart_minutes_clamped(make_client, monkeypatch):
    client, cfg, fake = make_client
    fake.route("POST", "/command", {"ok": True})
    fake.route("POST", "/power", {"ok": True})

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    out_low = _run(make_client, True, "srv1", minutes=0, confirm=True)
    assert "1 分钟后自动重启" in out_low
    out_high = _run(make_client, True, "srv1", minutes=999, confirm=True)
    assert "120 分钟后自动重启" in out_high


def test_schedule_restart_broadcast_failure_cancels(make_client, monkeypatch):
    client, cfg, fake = make_client
    fake.routes.clear()
    fake.route("POST", "/command", RuntimeError("panel down"))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    out = _run(make_client, True, "srv1", minutes=5, confirm=True)
    assert "公告发送失败" in out and "已取消" in out
    # 没有 /power 请求（计划已取消）
    assert not any("/power" in r.url.path for r in fake.requests)


def test_schedule_restart_empty_message(make_client):
    fake = make_client[2]
    fake.route("POST", "/command", None)
    out = _run(make_client, False, "srv1", minutes=5, message="   ", confirm=True)
    assert "公告消息不能为空" in out
    assert fake.requests == []  # 未发任何请求


def test_schedule_restart_restart_failure_swallowed(make_client, monkeypatch):
    client, cfg, fake = make_client
    fake.route("POST", "/command", {"ok": True})
    fake.route("POST", "/power", RuntimeError("panel down"))  # 到点重启失败

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    out = _run(make_client, True, "srv1", minutes=5, confirm=True)
    # 公告成功即返回成功；后台重启失败被静默吞掉，不崩溃、不抛异常
    assert "已安排" in out
    assert any("/power" in r.url.path for r in fake.requests)
