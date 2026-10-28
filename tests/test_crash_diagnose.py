"""diagnose_crash（崩服告警检测点：日志级崩溃信号诊断）工具测试。

只读工具：只发 GET 请求，不触发任何写入/危险操作。
"""

from __future__ import annotations

import httpx
import pytest

import pteropilot.server as srv
from pteropilot.server import _CRASH_PATTERNS


# ================= 纯数据：崩溃信号关键词表 =================


def test_crash_patterns_have_labels_and_keywords():
    for cat, (label, kws) in _CRASH_PATTERNS.items():
        assert label and cat
        assert isinstance(kws, tuple) and len(kws) >= 1
        assert all(isinstance(k, str) and k == k.lower() for k in kws)


# ================= 工具层 diagnose_crash =================


def _route_console(fake, server_id: str, text: str) -> None:
    fake.route("GET", f"/servers/{server_id}/console", httpx.Response(200, text=text))


def _route_resources(fake, server_id: str, state: str) -> None:
    fake.route("GET", f"/servers/{server_id}/resources", {"current_state": state})


async def test_diagnose_crash_offline_with_oom(fake, make_client):
    _route_resources(fake, "s1", "offline")
    _route_console(fake, "s1", "line1\njava.lang.OutOfMemoryError: Java heap space\nKilled\nline2")
    out = await srv.diagnose_crash("s1")
    assert "当前状态: offline" in out
    assert "🔴" in out
    assert "崩溃信号: 2 类" in out
    # java OOM 同时命中「内存不足」与「JVM 崩溃」两类
    assert "内存不足（Out of Memory）" in out
    assert "JVM 崩溃" in out
    assert "疑似反复崩溃" not in out
    assert "建议" in out


async def test_diagnose_crash_healthy_clean_log(fake, make_client):
    _route_resources(fake, "s1", "running")
    _route_console(fake, "s1", "Server started on port 25565\nDone (1.2s)!\n玩家 Steve 加入了游戏")
    out = await srv.diagnose_crash("s1")
    assert "崩溃信号: 未发现" in out
    assert "无需干预" in out
    assert "当前状态: running" in out


async def test_diagnose_crash_signal_while_running(fake, make_client):
    _route_resources(fake, "s1", "running")
    _route_console(fake, "s1", "normal\nserver thread has stopped\nA fatal error has been detected")
    out = await srv.diagnose_crash("s1")
    assert "崩溃信号: " in out
    assert "进程异常退出" in out
    assert "日志发现崩溃信号但当前状态正常" in out


async def test_diagnose_crash_restart_loop(fake, make_client):
    _route_resources(fake, "s1", "error")
    crashy = "\n".join(["fatal: unable to bind"] * 3)
    _route_console(fake, "s1", crashy)
    out = await srv.diagnose_crash("s1")
    assert "疑似反复崩溃" in out
    assert "致命错误" in out


async def test_diagnose_crash_empty_log(fake, make_client):
    _route_resources(fake, "s1", "running")
    _route_console(fake, "s1", "")
    out = await srv.diagnose_crash("s1")
    assert "崩溃信号: 未发现" in out
    assert "(日志为空)" in out


async def test_diagnose_crash_respects_tail(fake, make_client):
    _route_resources(fake, "s1", "running")
    # 崩溃行在 100 行之前，tail=10 应扫不到
    text = "\n".join(["normal-%d" % i for i in range(100)] + ["Killed"])
    _route_console(fake, "s1", text)
    out = await srv.diagnose_crash("s1", tail=10)
    assert "崩溃信号: 未发现" in out


# ================= 安全：只读复审 =================


async def test_diagnose_crash_only_issues_get(fake, make_client):
    _route_resources(fake, "s1", "running")
    _route_console(fake, "s1", "hello")
    await srv.diagnose_crash("s1")
    methods = [r.method for r in fake.requests]
    assert all(m == "GET" for m in methods), f"只应发 GET，实际: {methods}"


async def test_diagnose_crash_error_does_not_leak_key(fake, make_client):
    _route_resources(fake, "s1", "running")
    fake.route("GET", "/servers/s1/console", Exception("boom"))
    with pytest.raises(Exception) as ei:
        await srv.diagnose_crash("s1")
    # 面板错误原样向上抛（由 MCP 层转错误返回），绝不把 API key 拼进消息
    assert "test" not in str(ei.value)


# ================= 看门狗超时 / 原生崩溃 SIGSEGV 关键词扩充 =================


def test_crash_patterns_include_watchdog_and_native_categories():
    assert "watchdog" in _CRASH_PATTERNS
    assert "native" in _CRASH_PATTERNS
    wd_label, wd_kws = _CRASH_PATTERNS["watchdog"]
    assert all(k == k.lower() for k in wd_kws)
    # 明确不属于崩溃信号的 WARN 级措辞不应被收录（误报防护）
    assert not any("can't keep up" in k for k in wd_kws)
    assert not any(k == "killed" or k == "timed out" for k in wd_kws)


async def test_diagnose_crash_watchdog_tick_timeout(fake, make_client):
    _route_resources(fake, "s1", "offline")
    _route_console(fake, "s1",
                   "[Server Watchdog Thread/ERROR]: A single server tick took 60.00 seconds "
                   "(should be max 0.05)\nThe server has not responded for 60 seconds!")
    out = await srv.diagnose_crash("s1")
    assert "看门狗超时（tick 卡死）" in out
    assert "崩溃信号: " in out
    assert "🔴" in out


async def test_diagnose_crash_zigsegv_native(fake, make_client):
    _route_resources(fake, "s1", "error")
    _route_console(fake, "s1", "# A fatal error has been detected by the Java Runtime Environment\n# SIGSEGV (0xb) at pc=0x00007f")
    out = await srv.diagnose_crash("s1")
    assert "原生崩溃（SIGSEGV）" in out


async def test_diagnose_crash_chinese_watchdog(fake, make_client):
    _route_resources(fake, "s1", "offline")
    _route_console(fake, "s1", "[看门狗] 主线程响应超时，已强制重启服务端")
    out = await srv.diagnose_crash("s1")
    assert "看门狗超时（tick 卡死）" in out


async def test_diagnose_crash_cant_keep_up_is_not_crash(fake, make_client):
    """误报防护：Can't keep up! 是负载警告而非崩溃，不应命中崩溃信号。"""
    _route_resources(fake, "s1", "running")
    _route_console(fake, "s1", "[Server thread/WARN]: Can't keep up! Is the server overloaded? "
                              "Running 2050ms or 41 ticks behind")
    out = await srv.diagnose_crash("s1")
    assert "崩溃信号: 未发现" in out


async def test_diagnose_crash_connection_reset_is_not_crash(fake, make_client):
    """误报防护：Connection reset by peer 是网络错误，不应命中崩溃信号。"""
    _route_resources(fake, "s1", "running")
    _route_console(fake, "s1", "Connection reset by peer")
    out = await srv.diagnose_crash("s1")
    assert "崩溃信号: 未发现" in out
