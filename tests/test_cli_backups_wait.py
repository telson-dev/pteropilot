"""``--backups --create --wait``：等备份跑完并直接给下载链接。

动机：备份是后台任务，创建完只拿到 uuid。最常见的下一步是「等它跑完、取链接」，
而在此之前要手动盯着 `--backups` 反复看，跑完再跑一次 `--backup-url`。
既然创建是这条命令发起的，就把等待与取链接一并做掉——脚本一条命令备完。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pteropilot import server

UUID = "a1b2c3d4-0000-0000-0000-000000000000"


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    monkeypatch.delenv("PANEL_READONLY", raising=False)
    server.config.__init__()
    return monkeypatch


@pytest.fixture
def no_sleep(monkeypatch):
    async def fake_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)


def _panel(monkeypatch, *, polls_to_done: int = 2, url: str = "https://panel/dl/abc",
           bytes_: int = 50 * 1024 * 1024, fail_on: tuple[str, ...] = (),
           ids: tuple[str, ...] = ("smp",)):
    """假面板：备份在第 polls_to_done 次列表查询时完成。"""
    state = {"polls": 0, "created": [], "url_calls": 0}

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def create_backup(self, sid):
            state["created"].append(sid)
            if sid in fail_on:
                raise RuntimeError("panel said no")
            return {"attributes": {"uuid": UUID, "name": f"bk-{sid}"}}

        async def list_backups(self, sid):
            state["polls"] += 1
            done = state["polls"] >= polls_to_done
            return [{"attributes": {"uuid": UUID, "name": f"bk-{sid}", "bytes": bytes_,
                                    "completed_at": "2026-11-21T10:00:00Z" if done else None}}]

        async def backup_download_url(self, sid, bid):
            state["url_calls"] += 1
            return url

        async def aclose(self):
            pass

    async def resolve(wanted):
        return list(ids) if wanted == ["*"] else list(wanted)

    monkeypatch.setattr(server, "PanelClient", Fake)
    monkeypatch.setattr(server, "_resolve_target_ids", resolve)
    return state


# ------------------------------------------------------------------ _wait_backup

def test_wait_backup_returns_attrs_when_done() -> None:
    class C:
        def __init__(self):
            self.n = 0

        async def list_backups(self, sid):
            self.n += 1
            return [{"attributes": {"uuid": UUID,
                                    "completed_at": "x" if self.n >= 3 else None}}]

    orig = server.asyncio.sleep

    async def fast(_d):
        return None

    server.asyncio.sleep = fast
    try:
        attrs = asyncio.run(server._wait_backup(C(), "smp", UUID, poll=0.001, timeout=10))
    finally:
        server.asyncio.sleep = orig
    assert attrs is not None and attrs["uuid"] == UUID


def test_wait_backup_times_out_as_none() -> None:
    class C:
        async def list_backups(self, sid):
            return [{"attributes": {"uuid": UUID, "completed_at": None}}]

    orig = server.asyncio.sleep

    async def fast(_d):
        return None

    server.asyncio.sleep = fast
    try:
        out = asyncio.run(server._wait_backup(C(), "smp", UUID, poll=0.001, timeout=0.01))
    finally:
        server.asyncio.sleep = orig
    assert out is None


def test_wait_backup_ignores_other_uuids() -> None:
    class C:
        async def list_backups(self, sid):
            return [{"attributes": {"uuid": "other", "completed_at": "x"}}]

    orig = server.asyncio.sleep

    async def fast(_d):
        return None

    server.asyncio.sleep = fast
    try:
        out = asyncio.run(server._wait_backup(C(), "smp", UUID, poll=0.001, timeout=0.01))
    finally:
        server.asyncio.sleep = orig
    assert out is None, "别的备份完成不代表这份完成"


def test_wait_backup_survives_transient_error() -> None:
    """轮询期间的瞬时读取错误不该让等待失败。"""
    class C:
        def __init__(self):
            self.n = 0

        async def list_backups(self, sid):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("transient")
            return [{"attributes": {"uuid": UUID, "completed_at": "x"}}]

    orig = server.asyncio.sleep

    async def fast(_d):
        return None

    server.asyncio.sleep = fast
    try:
        out = asyncio.run(server._wait_backup(C(), "smp", UUID, poll=0.001, timeout=10))
    finally:
        server.asyncio.sleep = orig
    assert out is not None


# ------------------------------------------------------------------ CLI

def test_create_wait_reports_completion_and_url(panel, no_sleep, monkeypatch, capsys) -> None:
    _panel(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create", "--wait", "--wait-timeout", "30"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "备份完成" in out
    assert "https://panel/dl/abc" in out
    assert "一次性链接" in out, "要提醒链接用过即失效"


def test_create_wait_shows_size(panel, no_sleep, monkeypatch, capsys) -> None:
    _panel(monkeypatch)
    server._run_backups(["--backups", "smp", "--create", "--wait", "--wait-timeout", "30"])
    assert "50.0 MB" in capsys.readouterr().out


def test_create_without_wait_does_not_poll(panel, no_sleep, monkeypatch, capsys) -> None:
    """不加 --wait 时不该多打面板（保持原来「创建完就走」的语义）。"""
    state = _panel(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create"])
    assert rc == 0
    assert state["polls"] == 0
    assert "加 --wait" in capsys.readouterr().out, "要提示有 --wait 可用"


def test_create_wait_timeout_exits_4(panel, no_sleep, monkeypatch, capsys) -> None:
    """超时路径。

    这里直接 stub `_wait_backup` 返回 None，不靠真实时间流逝——`fake_sleep`
    下轮询会毫秒级跑完，靠 time.monotonic 的 deadline 测不准。
    """
    _panel(monkeypatch)

    async def immediate_timeout(*a, **k):
        return None

    monkeypatch.setattr(server, "_wait_backup", immediate_timeout)
    rc = server._run_backups(["--backups", "smp", "--create", "--wait", "--wait-timeout", "5"])
    err = capsys.readouterr().err
    assert rc == 4, "没等到完成要如实非零"
    assert "还没跑完" in err


def test_create_wait_url_failure_still_succeeds(panel, no_sleep, monkeypatch, capsys) -> None:
    """备份成功但取链接失败，不该把整条命令判定为失败。"""
    monkeypatch.setenv("PANEL_URL", "https://panel.example.com")
    monkeypatch.setenv("PANEL_API_KEY", "ptlc_" + "x" * 40)
    server.config.__init__()

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def create_backup(self, sid):
            return {"attributes": {"uuid": UUID, "name": "bk"}}

        async def list_backups(self, sid):
            return [{"attributes": {"uuid": UUID, "completed_at": "x", "bytes": 1024}}]

        async def backup_download_url(self, sid, bid):
            raise RuntimeError("no url")

        async def aclose(self):
            pass

    async def resolve(w):
        return ["smp"]

    monkeypatch.setattr(server, "PanelClient", Fake)
    monkeypatch.setattr(server, "_resolve_target_ids", resolve)
    rc = server._run_backups(["--backups", "smp", "--create", "--wait", "--wait-timeout", "30"])
    assert rc == 0
    assert "备份完成" in capsys.readouterr().out


def test_create_wait_json_has_completed_and_url(panel, no_sleep, monkeypatch, capsys) -> None:
    _panel(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create", "--wait",
                              "--wait-timeout", "30", "--json"])
    obj = json.loads(capsys.readouterr().out)
    assert rc == 0
    e = obj["created"][0]
    assert e["completed"] is True
    assert e["url"] == "https://panel/dl/abc"
    assert e["bytes"] == 50 * 1024 * 1024


def test_create_wait_partial_failure(panel, no_sleep, monkeypatch, capsys) -> None:
    state = _panel(monkeypatch, fail_on=("lobby",), ids=("smp", "lobby"))
    rc = server._run_backups(["--backups", "*", "--create", "--confirm", "--wait",
                              "--wait-timeout", "30"])
    captured = capsys.readouterr()
    assert rc == 4
    assert "lobby" in captured.err
    assert state["created"] == ["smp", "lobby"]


def test_wait_timeout_argument_validation(panel, no_sleep, capsys) -> None:
    assert server._run_backups(["--backups", "smp", "--create", "--wait", "--wait-timeout"]) == 2
    assert "跟秒数" in capsys.readouterr().err

    assert server._run_backups(["--backups", "smp", "--create", "--wait",
                                "--wait-timeout", "abc"]) == 2
    assert "需要数字" in capsys.readouterr().err


def test_wait_timeout_clamped(panel, no_sleep, monkeypatch, capsys) -> None:
    """给 9999 会夹到 3600（上限），不该报错。"""
    _panel(monkeypatch)
    rc = server._run_backups(["--backups", "smp", "--create", "--wait",
                              "--wait-timeout", "9999"])
    assert rc == 0
    assert "最多 3600 秒" in capsys.readouterr().out
