"""批量工具的并发上限。

原来所有批量工具都是 ``asyncio.gather(*(f(x) for x in items))``——没有上限。
20 台服务器就是 20 条 HTTPS 连接同时打进面板；面板是单实例 + 单数据库，
几十条连接一起来容易 429，或者把整机拖慢。改成有界并发，默认 8 条。
"""

from __future__ import annotations

import asyncio

import pytest

from pteropilot import server


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    for k in ("PANEL_URL", "PANEL_API_KEY", "PANEL_APP_KEY"):
        monkeypatch.setenv(k, "https://panel.example.com" if k == "PANEL_URL" else "ptlc_" + "x" * 40)
    server.config.__init__()


def test_bounded_preserves_order() -> None:
    async def fn(x: int) -> int:
        await asyncio.sleep(0.01 * (7 - x))   # 故意让后面的先完成
        return x * 2

    got = asyncio.run(server._gather_bounded(fn, range(8), limit=3))
    assert list(got) == [x * 2 for x in range(8)], "结果必须按输入顺序返回"


def test_bounded_respects_limit() -> None:
    """同时在跑的协程数不能超过 limit。"""
    live = 0
    peak = 0

    async def fn(x: int) -> int:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return x

    got = asyncio.run(server._gather_bounded(fn, range(40), limit=5))
    assert len(got) == 40
    assert peak <= 5, f"并发峰值 {peak} 超过上限 5"


def test_bounded_default_uses_max_concurrency() -> None:
    live = 0
    peak = 0

    async def fn(x: int) -> int:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.005)
        live -= 1
        return x

    asyncio.run(server._gather_bounded(fn, range(60)))
    assert peak <= server.MAX_CONCURRENCY


def test_bounded_empty() -> None:
    async def fn(x):
        raise AssertionError("不该被调用")

    assert asyncio.run(server._gather_bounded(fn, [])) == []


def test_bounded_single_item() -> None:
    async def fn(x):
        return x + 1

    assert list(asyncio.run(server._gather_bounded(fn, [41], limit=1))) == [42]


def test_bounded_limit_one_is_serial() -> None:
    order = []

    async def fn(x):
        order.append(("start", x))
        await asyncio.sleep(0.001)
        order.append(("end", x))
        return x

    asyncio.run(server._gather_bounded(fn, [1, 2, 3], limit=1))
    # limit=1 时必须严格串行：start/end 成对出现
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2), ("start", 3), ("end", 3)]


def test_bounded_propagates_exception() -> None:
    """和 gather 默认行为一致：第一个异常向上抛。"""

    async def fn(x):
        if x == 2:
            raise RuntimeError("boom")
        return x

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(server._gather_bounded(fn, [1, 2, 3], limit=2))


def test_concurrency_env_parsing(monkeypatch) -> None:
    assert server._read_concurrency() == 8                      # 未设置
    monkeypatch.setenv("PTEROPILOT_CONCURRENCY", "3")
    assert server._read_concurrency() == 3
    monkeypatch.setenv("PTEROPILOT_CONCURRENCY", "999")
    assert server._read_concurrency() == 32                     # 夹到上限
    monkeypatch.setenv("PTEROPILOT_CONCURRENCY", "0")
    assert server._read_concurrency() == 1                      # 夹到下限
    monkeypatch.setenv("PTEROPILOT_CONCURRENCY", "abc")
    assert server._read_concurrency() == 8                      # 非法值回退默认
    monkeypatch.setenv("PTEROPILOT_CONCURRENCY", "  ")
    assert server._read_concurrency() == 8


def test_batch_power_respects_cap(monkeypatch) -> None:
    """真实工具 batch_power 走的是有界并发，而不是又退回无上限 gather。

    这个测试的价值在于它调用的是**真实的 MCP 工具函数**，不是辅助函数本身——
    以后有人把 `_gather_bounded` 改回 `asyncio.gather`，这里会红。
    """
    live = 0
    peak = 0
    calls: list[str] = []

    async def fake_power(sid, signal):
        nonlocal live, peak
        calls.append(sid)
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1

    async def fake_resolve(ids):
        return [f"srv{i}" for i in range(30)]

    monkeypatch.setattr(server.panel, "power", fake_power)
    monkeypatch.setattr(server, "_resolve_target_ids", fake_resolve)
    monkeypatch.setattr(server, "MAX_CONCURRENCY", 4)

    # restart 属于高危，必须带 confirm=True 才会真的执行（不带会被二次确认拦住）
    asyncio.run(server.batch_power(server_ids=["*"], signal="restart", confirm=True))
    assert len(calls) == 30, "30 台都要执行到"
    assert peak <= 4, f"并发峰值 {peak} 超过上限 4 —— 说明又用了无上限 gather"

