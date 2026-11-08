"""get_server_activity 工具测试：审计日志展示、limit 限制、系统事件标注、空记录。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    async def run():
        return await server.get_server_activity(*args, **kwargs)

    return asyncio.run(run())


def _event(event="server:power.start", actor="telson", ts="2026-11-08T10:20:31.000000Z", **kw):
    attrs = {"event": event, "timestamp": ts, "actor": actor, "is_system": False}
    attrs.update(kw)
    return {"attributes": attrs}


def test_activity_basic(make_client):
    make_client[2].route(
        "GET",
        "/servers/s1/activity",
        httpx.Response(
            200,
            json={"data": [_event(), _event(event="server:file.delete", actor="alice")]},
        ),
    )
    out = _call(make_client, "s1")
    assert "共 2 条记录" in out
    assert "server:power.start" in out
    assert "server:file.delete" in out
    assert "telson" in out and "alice" in out
    # 时间戳格式化：T 换空格、去掉小数秒
    assert "2026-11-08 10:20:31" in out


def test_activity_system_event_marked(make_client):
    make_client[2].route(
        "GET",
        "/servers/s1/activity",
        httpx.Response(200, json={"data": [_event(is_system=True, actor=None)]}),
    )
    out = _call(make_client, "s1")
    assert "（系统）" in out


def test_activity_limit_caps(make_client):
    data = [_event(event=f"server:evt.{i}") for i in range(10)]
    make_client[2].route("GET", "/servers/s1/activity", httpx.Response(200, json={"data": data}))
    out = _call(make_client, "s1", limit=3)
    assert "共 10 条记录，显示最新 3 条" in out
    assert "server:evt.0" in out and "server:evt.1" in out and "server:evt.2" in out
    assert "server:evt.3" not in out


def test_activity_limit_clamped(make_client):
    # 非法/越界 limit 会被钳制到 1-100，不应抛异常
    data = [_event(event=f"server:evt.{i}") for i in range(3)]
    make_client[2].route("GET", "/servers/s1/activity", httpx.Response(200, json={"data": data}))
    out = _call(make_client, "s1", limit=0)
    assert "显示最新 1 条" in out


def test_activity_metadata_hint(make_client):
    make_client[2].route(
        "GET",
        "/servers/s1/activity",
        httpx.Response(
            200,
            json={"data": [_event(event="server:file.delete", metadata={"file": "plugins/Foo.jar"})]},
        ),
    )
    out = _call(make_client, "s1")
    assert "[plugins/Foo.jar]" in out


def test_activity_empty(make_client):
    make_client[2].route("GET", "/servers/s1/activity", httpx.Response(200, json={"data": []}))
    out = _call(make_client, "s1")
    assert "没有操作记录" in out
