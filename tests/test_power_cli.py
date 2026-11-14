"""CLI --power 的测试：批量电源信号（不启动 MCP server）。

与 test_broadcast.py 的 CLI 部分同构：都要覆盖用法错误、确认拦截、
成功、部分失败、--json 结构化输出。
"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

import pteropilot.server as srv
from pteropilot.config import Config


class FakePanel:
    def __init__(self, servers=("s1", "s2"), fail_ids=()):
        self.servers = list(servers)
        self.fail_ids = set(fail_ids)
        self.signals: list[tuple[str, str]] = []

    async def client_servers(self):
        return [{"attributes": {"identifier": s, "name": f"srv-{s}"}} for s in self.servers]

    async def power(self, sid: str, signal: str):
        if sid in self.fail_ids:
            raise RuntimeError("boom")
        self.signals.append((sid, signal))


def _bind(fake, readonly: bool = False):
    srv.panel = fake
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)


@pytest.fixture
def fake():
    f = FakePanel()
    _bind(f)
    yield f
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=False)


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = srv._run_power(argv)
    return rc, buf.getvalue()


# ---------------- 用法 ----------------

def test_usage_missing_signal(capsys):
    rc = srv._run_power(["--power"])
    assert rc == 2
    assert "用法" in capsys.readouterr().err


def test_usage_signal_is_another_flag(capsys):
    rc = srv._run_power(["--power", "--confirm"])
    assert rc == 2


def test_usage_requires_servers(capsys):
    rc = srv._run_power(["--power", "start"])
    assert rc == 2
    assert "--servers 必填" in capsys.readouterr().err


# ---------------- 成功 ----------------

def test_power_start_no_confirm_needed(fake):
    rc, out = _run(["--power", "start", "--servers", "s1,s2"])
    assert rc == 0
    assert "成功 2 台" in out
    assert sorted(fake.signals) == [("s1", "start"), ("s2", "start")]


def test_power_restart_needs_confirm(fake):
    rc, out = _run(["--power", "restart", "--servers", "s1"])
    assert rc == 3
    assert "高危操作" in out
    assert fake.signals == []


def test_power_restart_with_confirm(fake):
    rc, out = _run(["--power", "restart", "--servers", "s1", "--confirm"])
    assert rc == 0
    assert fake.signals == [("s1", "restart")]


def test_power_star_start_also_needs_confirm(fake):
    """作用于全部服务器时，连 start 也要确认（防误启动全站）。"""
    rc, out = _run(["--power", "start", "--servers", "*"])
    assert rc == 3
    assert fake.signals == []


def test_power_star_with_confirm_hits_all(fake):
    rc, out = _run(["--power", "start", "--servers", "*", "--confirm"])
    assert rc == 0
    assert sorted(sid for sid, _ in fake.signals) == ["s1", "s2"]


def test_power_readonly_refused(fake):
    _bind(fake, readonly=True)
    rc, out = _run(["--power", "stop", "--servers", "s1", "--confirm"])
    assert rc == 3
    assert "只读" in out
    assert fake.signals == []


def test_power_bad_signal(fake):
    rc, out = _run(["--power", "explode", "--servers", "s1"])
    assert rc == 3
    assert "非法信号" in out
    assert fake.signals == []


def test_power_partial_failure_exit_1():
    f = FakePanel(fail_ids={"s1"})
    _bind(f)
    rc, out = _run(["--power", "start", "--servers", "s1,s2"])
    assert rc == 1
    assert "成功 1 台" in out and "失败 1 台" in out


# ---------------- --json ----------------

def test_power_json_payload(fake):
    rc, out = _run(["--power", "start", "--servers", "s1,s2", "--json"])
    d = json.loads(out)
    assert rc == 0
    assert d["status"] == "ok"
    assert d["signal"] == "start"
    assert d["scope"] == "explicit"
    assert d["total"] == 2 and d["ok"] == 2 and d["failed"] == 0
    assert [r["id"] for r in d["results"]] == ["s1", "s2"]


def test_power_json_refused_reason(fake):
    rc, out = _run(["--power", "stop", "--servers", "s1", "--json"])
    d = json.loads(out)
    assert rc == 3
    assert d["status"] == "refused"
    assert d["reason"] == "need_confirm"
    assert d["results"] == []


def test_power_json_partial_failure_error_name():
    f = FakePanel(fail_ids={"s1"})
    _bind(f)
    rc, out = _run(["--power", "start", "--servers", "s1,s2", "--json"])
    d = json.loads(out)
    assert rc == 1
    bad = [r for r in d["results"] if not r["ok"]]
    assert bad and bad[0]["error"] == "RuntimeError"


def test_power_tool_and_cli_share_logic(fake):
    """batch_power 与 --power 必须走同一份结构化逻辑。"""
    import asyncio

    d = asyncio.run(srv._power_data(["s1"], "start", False))
    assert d["status"] == "ok" and d["total"] == 1
    text = srv._format_power(d)
    assert "共 1 台" in text
