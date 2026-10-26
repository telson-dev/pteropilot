"""batch_power（批量电源操作）工具测试。

面板无原生批量电源端点，退化为逐台循环调用 client.power(sid, signal) + 整体二次确认。
覆盖：空列表、部分失败（单台 500 不阻断整体）、confirm 缺省拒绝（stop/restart/kill）、
start 免确认、非法 signal、只读拦截（不发任何请求）。

注意：power 端点是 POST /api/client/servers/<id>/power。MockTransport handler 用
`contains in path` 匹配，裸「/power」能同时命中所有单台路径；但同批次多台会让同一
路由被重复命中——此处用 `list` 响应在不同 sid 之间区分，或直接对每台用裸「/power」
顺序返回即可（handler 对 list 响应按序 pop）。
"""

from __future__ import annotations

import httpx
import pytest

import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


async def test_batch_power_empty_list(fake):
    _bind(fake)
    out = await srv.batch_power([], "start")
    assert "未指定任何服务器" in out
    assert fake.requests == []


async def test_batch_power_invalid_signal(fake):
    _bind(fake)
    out = await srv.batch_power(["a", "b"], "explode")
    assert "非法信号" in out
    assert fake.requests == []


async def test_batch_power_start_no_confirm(fake):
    # start 免确认，逐台成功
    _bind(fake)
    fake.route("POST", "/power", {})
    out = await srv.batch_power(["s1", "s2"], "start")
    assert "批量 start" in out
    assert "成功 2 台、失败 0 台" in out
    assert "s1 | ✅" in out and "s2 | ✅" in out
    # 发出两次 POST
    assert len(fake.requests) == 2


async def test_batch_power_stop_requires_confirm(fake):
    # stop 属高危，缺省 confirm 应整体拦截、不发请求
    _bind(fake)
    out = await srv.batch_power(["s1", "s2"], "stop")
    assert "高危" in out
    assert "confirm=True" in out
    assert fake.requests == []


async def test_batch_power_stop_with_confirm(fake):
    _bind(fake)
    fake.route("POST", "/power", {})
    out = await srv.batch_power(["s1", "s2", "s3"], "stop", confirm=True)
    assert "成功 3 台、失败 0 台" in out
    assert len(fake.requests) == 3


async def test_batch_power_partial_failure(fake):
    # s2 的 power 抛异常（面板 500），不阻断 s1/s3
    _bind(fake)
    fake.route("POST", "s1/power", {})
    fake.route("POST", "s2/power", httpx.Response(500, json={"errors": [{"detail": "boom"}]}))
    fake.route("POST", "s3/power", {})
    out = await srv.batch_power(["s1", "s2", "s3"], "restart", confirm=True)
    assert "成功 2 台、失败 1 台" in out
    assert "s1 | ✅" in out
    assert "s2 | ❌ 失败" in out
    assert "s3 | ✅" in out
    assert len(fake.requests) == 3


async def test_batch_power_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.batch_power(["s1"], "start")
    assert "只读模式" in out
    assert fake.requests == []


async def test_batch_power_readonly_stop_confirm(fake):
    # 即便带了 confirm，只读模式也优先拦截、不发请求
    _bind(fake, readonly=True)
    out = await srv.batch_power(["s1", "s2"], "stop", confirm=True)
    assert "只读模式" in out
    assert fake.requests == []