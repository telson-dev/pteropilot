"""批量操作并发化（性能优化）测试：验证 gather 并发 + 保序 + 单台失败不阻断。"""

from __future__ import annotations

import asyncio
import time

from pteropilot import server


class SlowPanel:
    """带可控延迟的 panel stub：记录并发峰值与调用序列。"""

    def __init__(self, delay: float = 0.15) -> None:
        self.delay = delay
        self.powers: list[tuple[str, str]] = []
        self.inflight = 0
        self.max_inflight = 0
        self.fail_on: set[str] = set()

    async def power(self, sid: str, signal: str) -> None:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        await asyncio.sleep(self.delay)
        self.inflight -= 1
        if sid in self.fail_on:
            raise RuntimeError("panel down")
        self.powers.append((sid, signal))


def test_batch_power_concurrent(make_client, monkeypatch):
    client, cfg, fake = make_client
    sp = SlowPanel(delay=0.15)
    monkeypatch.setattr(server, "panel", sp)
    monkeypatch.setattr(server, "config", cfg)

    start = time.monotonic()
    out = asyncio.run(server.batch_power(["a", "b", "c"], "start"))
    elapsed = time.monotonic() - start

    # 三台并发执行：并发峰值应达 3，耗时远小于串行（3×0.15s）
    assert sp.max_inflight == 3
    assert elapsed < 0.15 * 2.2, f"耗时 {elapsed:.3f}s 疑似串行"
    assert out.count("✅") == 3
    # 保序：gather 结果按输入顺序
    assert sp.powers == [("a", "start"), ("b", "start"), ("c", "start")]


def test_batch_power_concurrent_partial_failure(make_client, monkeypatch):
    client, cfg, fake = make_client
    sp = SlowPanel(delay=0.05)
    sp.fail_on = {"b"}
    monkeypatch.setattr(server, "panel", sp)
    monkeypatch.setattr(server, "config", cfg)

    out = asyncio.run(server.batch_power(["a", "b", "c"], "start"))
    # 单台失败不阻断其余，输出行序与输入一致
    assert "成功 2 台、失败 1 台" in out
    assert "| ✅" in out and "| ❌" in out
    assert sp.powers == [("a", "start"), ("c", "start")]


def test_broadcast_concurrent(make_client, monkeypatch):
    client, cfg, fake = make_client
    sp = SlowPanel(delay=0.1)
    monkeypatch.setattr(server, "panel", sp)
    monkeypatch.setattr(server, "config", cfg)

    # broadcast 需要 client_servers() 与 send_command()：补 stub 方法
    async def client_servers():
        return [
            {"attributes": {"identifier": "a", "name": "A"}},
            {"attributes": {"identifier": "b", "name": "B"}},
        ]

    async def send_command(sid, command):
        sp.inflight += 1
        sp.max_inflight = max(sp.max_inflight, sp.inflight)
        await asyncio.sleep(sp.delay)
        sp.inflight -= 1
        sp.powers.append((sid, command))

    sp.client_servers = client_servers
    sp.send_command = send_command

    start = time.monotonic()
    out = asyncio.run(server.broadcast_message(["a", "b"], "维护公告", confirm=True))
    elapsed = time.monotonic() - start

    assert sp.max_inflight == 2
    assert elapsed < 0.1 * 1.8
    assert "已广播到 2 台" in out
