"""CLI --status 的测试：不开 MCP 客户端直接看服务器状态。"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

import pteropilot.server as srv
from pteropilot.config import Config

GB = 1024**3


class FakePanel:
    def __init__(self, servers=None, fail_ids=(), players=None):
        # servers: {id: (state, cpu, mem_bytes, mem_limit)}
        # 注意用 is None 判断：显式传 {} 表示「一台都没有」，不能被默认值覆盖
        self.servers = (
            {
                "s1": ("running", 34.2, 4.1 * GB, 6 * GB),
                "s2": ("offline", 0, 0, 6 * GB),
            }
            if servers is None
            else servers
        )
        self.fail_ids = set(fail_ids)
        self.players = players if players is not None else ["Steve", "Alex"]

    async def client_servers(self):
        return [{"attributes": {"identifier": k, "name": f"srv-{k}"}} for k in sorted(self.servers)]

    async def resources(self, sid):
        if sid in self.fail_ids:
            raise RuntimeError("boom")
        state, cpu, mem, lim = self.servers[sid]
        return {
            "current_state": state,
            "resources": {
                "cpu_absolute": cpu,
                "memory_bytes": mem,
                "memory_limit": lim,
                "disk_bytes": 1 * GB,
            },
        }

    async def online_players(self, sid):
        return list(self.players)


def _bind(fake):
    srv.panel = fake
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=False)


@pytest.fixture
def fake():
    f = FakePanel()
    _bind(f)
    yield f
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=False)


def _run(argv):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = srv._run_status(argv)
    return rc, buf.getvalue(), err.getvalue()


# ---------------- 全量 ----------------

def test_status_lists_all(fake):
    rc, out, _ = _run(["--status"])
    assert "s1" in out and "s2" in out
    assert "34.2%" in out              # CPU
    assert "4198M" in out or "4199M" in out   # 4.1 GB → MiB
    assert "🟢" in out and "🔴" in out


def test_status_exit_1_when_any_unhealthy(fake):
    rc, _, _ = _run(["--status"])
    assert rc == 1                    # s2 是 offline


def test_status_exit_0_when_all_healthy():
    f = FakePanel(servers={"s1": ("running", 10.0, 1 * GB, 6 * GB)})
    _bind(f)
    rc, out, _ = _run(["--status"])
    assert rc == 0
    assert "🟢" in out


def test_status_partial_failure_listed_and_exit_4():
    """部分服务器查不到 = 查询侧问题，不当成「有服务器异常」。"""
    f = FakePanel(fail_ids={"s2"})
    _bind(f)
    rc, out, _ = _run(["--status"])
    assert rc == 4
    assert "⚠" in out and "s2" in out
    assert "s1" in out                # 单台失败不阻断其余


def test_status_json(fake):
    rc, out, _ = _run(["--status", "--json"])
    d = json.loads(out)
    assert rc == 1
    ids = [s["id"] for s in d["servers"]]
    assert ids == ["s1", "s2"]
    s1 = d["servers"][0]
    assert s1["state"] == "running"
    assert s1["cpu"] == 34.2
    assert s1["memory_limit"] == 6 * GB
    assert d["errors"] == []


def test_status_json_has_errors_field():
    f = FakePanel(fail_ids={"s2"})
    _bind(f)
    rc, out, _ = _run(["--status", "--json"])
    d = json.loads(out)
    assert rc == 4, "有 errors 字段说明查询侧出问题 → 4"
    assert any("s2" in e for e in d["errors"])


# ---------------- 单台 ----------------

def test_status_single(fake):
    rc, out, _ = _run(["--status", "s1"])
    assert rc == 0
    assert "s1" in out and "s2" not in out
    assert "34.2%" in out
    assert "2" in out                 # 玩家数 2


def test_status_single_json_players(fake):
    rc, out, _ = _run(["--status", "s1", "--json"])
    d = json.loads(out)
    assert rc == 0
    assert d["servers"][0]["players"] == 2


def test_status_single_players_none_when_log_fails():
    f = FakePanel()

    async def boom(_sid):
        raise RuntimeError("no log")

    f.online_players = boom
    _bind(f)
    rc, out, _ = _run(["--status", "s1", "--json"])
    d = json.loads(out)
    assert d["servers"][0]["players"] is None   # 拿不到就不编


# ---------------- 边界 ----------------

def test_status_empty_servers():
    f = FakePanel(servers={})
    _bind(f)
    rc, out, _ = _run(["--status"])
    assert "没有可访问的服务器" in out


def test_status_missing_fields_do_not_crash():
    class Weird:
        async def client_servers(self):
            return [{"attributes": {"identifier": "w1", "name": "w"}}]

        async def resources(self, sid):
            return {"current_state": "running"}      # 没有 resources 子字典

    _bind(Weird())
    rc, out, _ = _run(["--status"])
    assert "w1" in out
    assert "-" in out                 # 缺字段显示 '-' 而不是崩


def test_status_panel_unreachable(monkeypatch):
    class Dead:
        async def client_servers(self):
            raise RuntimeError("connection refused")

    _bind(Dead())
    rc, out, err = _run(["--status"])
    assert rc == 4, "面板连不上 → 4（配置/网络问题，不是服务器异常）"
    assert "查询失败" in err


def test_fmt_mb():
    assert srv._fmt_mb(0) == "-"
    assert srv._fmt_mb(None) == "-"
    assert srv._fmt_mb("x") == "-"
    assert srv._fmt_mb(4 * 1024**2) == "4M"
