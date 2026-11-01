"""read_server_logs 工具测试：读日志尾部、空文件、超长截断、行数上限。"""

from __future__ import annotations

import asyncio

import httpx

from pteropilot import server


def _call(make_client, *args, **kwargs):
    client, cfg, fake = make_client

    async def run():
        return await server.read_server_logs(*args, **kwargs)

    return asyncio.run(run())


def test_read_server_logs_tail(make_client):
    log = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8"
    fake = make_client[2]
    fake.route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1")
    assert "logs/latest.log" in out
    assert "共 8 行" in out
    assert "显示末尾 200 行" not in out  # 行数不足时不标"末尾"
    assert out.rstrip().endswith("line8")


def test_read_server_logs_limited_lines(make_client):
    log = "\n".join(f"line{i}" for i in range(1, 21))
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1", lines=5)
    assert "显示末尾 5 行" in out
    assert "line16" in out and "line20" in out
    assert "line10" not in out and "line15" not in out  # 更早的行被排除


def test_read_server_logs_truncates_long(make_client):
    # 200 行 × 300 字符 → 超过 20000 字符，应只保留末尾 20000 字符并标注
    log = "\n".join(f"line{i:04d}" + "x" * 300 for i in range(1, 201))
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1", lines=200)
    assert "仅保留末尾 20000 字符" in out
    assert "line0200" in out  # 最新内容保留
    assert "line0001" not in out  # 最旧内容被截断


def test_read_server_logs_empty(make_client):
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=""))
    out = _call(make_client, "srv1")
    assert "为空" in out


def test_read_server_logs_lines_capped(make_client):
    log = "\n".join(f"line{i}" for i in range(1, 6000))
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text=log))
    out = _call(make_client, "srv1", lines=99999)  # 超过上限 → 截到 5000
    assert "显示末尾 5000 行" in out
    assert "line5999" in out
    assert "line1000" not in out


def test_read_server_logs_lines_min_one(make_client):
    make_client[2].route("GET", "/files/contents", httpx.Response(200, text="a\nb\nc"))
    out = _call(make_client, "srv1", lines=0)  # 下限 1
    assert out.rstrip().endswith("c")
