"""面板请求的自动重试（直接测 _Guarded）。

注意：``conftest.fake`` 给 ``PanelClient`` 装的是**裸 httpx.AsyncClient**，
不经过 ``_Guarded``，所以那条夹具测不到重试。这里自己把一个 MockTransport
塞进 ``_Guarded``，测的就是真实的重试代码路径。

只读请求遇到 429 / 502 / 503 / 504 或连接错误时退避重试；
**写操作绝不重试**——一次被限流的 `stop` 再补发一次，比让用户看到错误危险得多。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from pteropilot.client import _Guarded, PanelError, _backoff_delay, _read_base_delay, _read_retries


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    """把退避压到几乎为 0，测试不用真的等。"""
    monkeypatch.setenv("PTEROPILOT_RETRY_BASE", "0.001")
    monkeypatch.setenv("PTEROPILOT_RETRIES", "2")


class Queue:
    """按顺序吐出预置响应；吐完就返回 200 空对象。"""

    def __init__(self, items: list) -> None:
        self.items = list(items)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.items.pop(0) if self.items else httpx.Response(200, json={})
        if isinstance(item, Exception):
            raise item
        return item


def make_guarded(items: list) -> tuple[_Guarded, Queue]:
    q = Queue(items)
    inner = httpx.AsyncClient(transport=httpx.MockTransport(q.handler), base_url="https://panel")
    return _Guarded(inner), q


# ------------------------------------------------------------------ 参数解析

def test_read_retries(monkeypatch) -> None:
    monkeypatch.delenv("PTEROPILOT_RETRIES", raising=False)
    assert _read_retries() == 2
    monkeypatch.setenv("PTEROPILOT_RETRIES", "0")
    assert _read_retries() == 0
    monkeypatch.setenv("PTEROPILOT_RETRIES", "9")
    assert _read_retries() == 5           # 夹到上限
    monkeypatch.setenv("PTEROPILOT_RETRIES", "abc")
    assert _read_retries() == 2           # 非法值回退


def test_read_base_delay(monkeypatch) -> None:
    monkeypatch.delenv("PTEROPILOT_RETRY_BASE", raising=False)
    assert _read_base_delay() == 0.5
    monkeypatch.setenv("PTEROPILOT_RETRY_BASE", "0.01")
    assert _read_base_delay() == pytest.approx(0.01)
    monkeypatch.setenv("PTEROPILOT_RETRY_BASE", "99")
    assert _read_base_delay() == 5.0      # 夹到上限
    monkeypatch.setenv("PTEROPILOT_RETRY_BASE", "x")
    assert _read_base_delay() == 0.5


def test_backoff_respects_retry_after() -> None:
    r = httpx.Response(429, headers={"Retry-After": "2"})
    assert _backoff_delay(0, r) == pytest.approx(2.0)


def test_backoff_caps_absurd_retry_after() -> None:
    """面板说 600 秒也不能真的等 10 分钟。"""
    r = httpx.Response(429, headers={"Retry-After": "600"})
    assert _backoff_delay(0, r) <= 5.0


def test_backoff_is_bounded_without_header() -> None:
    for attempt in range(6):
        assert _backoff_delay(attempt) <= 5.0 * 1.25


# ------------------------------------------------------------------ GET 会重试

def test_get_retries_on_429_then_succeeds() -> None:
    g, q = make_guarded([httpx.Response(429), httpx.Response(200, json={"ok": True})])
    r = asyncio.run(g.get("/x"))
    assert r.status_code == 200
    assert len(q.requests) == 2


def test_get_retries_on_503_and_504() -> None:
    for code in (503, 504, 502):
        g, q = make_guarded([httpx.Response(code), httpx.Response(200, json={})])
        asyncio.run(g.get("/x"))
        assert len(q.requests) == 2, f"{code} 应当重试"


def test_get_gives_up_after_max_retries() -> None:
    g, q = make_guarded([httpx.Response(429)] * 10)
    with pytest.raises(PanelError, match="429"):
        asyncio.run(g.get("/x"))
    assert len(q.requests) == 3, "1 次原始请求 + 2 次重试"


def test_get_does_not_retry_on_404() -> None:
    g, q = make_guarded([httpx.Response(404)])
    with pytest.raises(PanelError):
        asyncio.run(g.get("/x"))
    assert len(q.requests) == 1, "404 不是瞬时故障，重试没意义"


def test_get_retries_on_connect_error() -> None:
    g, q = make_guarded([httpx.ConnectError("boom"), httpx.Response(200, json={})])
    asyncio.run(g.get("/x"))
    assert len(q.requests) == 2


def test_get_retries_on_timeout() -> None:
    g, q = make_guarded([httpx.ReadTimeout("slow"), httpx.Response(200, json={})])
    asyncio.run(g.get("/x"))
    assert len(q.requests) == 2


def test_retries_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("PTEROPILOT_RETRIES", "0")
    g, q = make_guarded([httpx.Response(429), httpx.Response(200, json={})])
    with pytest.raises(PanelError, match="429"):
        asyncio.run(g.get("/x"))
    assert len(q.requests) == 1, "PTEROPILOT_RETRIES=0 时应关闭重试"


# ------------------------------------------------------------------ 写操作不重试

def test_post_never_retries_on_429() -> None:
    """写操作被限流必须立刻报错，不能偷偷补发。"""
    g, q = make_guarded([httpx.Response(429)])
    with pytest.raises(PanelError, match="429"):
        asyncio.run(g.post("/x"))
    assert len(q.requests) == 1


def test_put_never_retries() -> None:
    g, q = make_guarded([httpx.Response(503)])
    with pytest.raises(PanelError):
        asyncio.run(g.put("/x"))
    assert len(q.requests) == 1


def test_delete_never_retries() -> None:
    g, q = make_guarded([httpx.Response(503)])
    with pytest.raises(PanelError):
        asyncio.run(g.delete("/x"))
    assert len(q.requests) == 1


def test_post_never_retries_on_connect_error() -> None:
    """连不上也别重发写请求：可能已经到面板了，只是响应丢了。"""
    g, q = make_guarded([httpx.ConnectError("boom")])
    with pytest.raises(PanelError, match="连不上面板"):
        asyncio.run(g.post("/x"))
    assert len(q.requests) == 1
