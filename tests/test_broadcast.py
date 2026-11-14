"""broadcast_command 的测试：批量发控制台指令。"""

from __future__ import annotations

import pytest

import pteropilot.server as srv
from pteropilot.config import Config


def _bind(fake, readonly: bool = False):
    """把 server.py 模块里的 panel / config 换成假的（照 test_batch_power.py 的写法）。"""
    srv.panel = fake
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)


class FakePanel:
    """记录被调用的 send_command / resources，可指定失败与状态。"""

    def __init__(self, states=None, fail_ids=()):
        self.states = states or {}
        self.fail_ids = set(fail_ids)
        self.sent: list[tuple[str, str]] = []
        self.resources_calls: list[str] = []

    async def client_servers(self):
        return [
            {"attributes": {"identifier": sid, "name": f"srv-{sid}"}}
            for sid in sorted(self.states.keys())
        ]

    async def resources(self, sid: str):
        self.resources_calls.append(sid)
        return {"current_state": self.states.get(sid, "offline")}

    async def send_command(self, sid: str, command: str):
        if sid in self.fail_ids:
            raise RuntimeError("boom")
        self.sent.append((sid, command))


@pytest.fixture
def fake(monkeypatch):
    f = FakePanel({"a": "running", "b": "running", "c": "offline"})
    _bind(f)
    yield f
    # 兜底：把全局恢复成默认配置，避免影响同批次的其它测试
    srv.config = Config(panel_url="https://panel", panel_api_key="k", readonly=False)


@pytest.mark.asyncio
async def test_broadcast_to_running_by_default(fake):
    out = await srv.broadcast_command("say hi")
    assert "成功 2" in out
    assert sorted(fake.sent) == [("a", "say hi"), ("b", "say hi")]
    # 停机的 c 不应被发到
    assert all(sid != "c" for sid, _ in fake.sent)


@pytest.mark.asyncio
async def test_broadcast_explicit_ids(fake):
    out = await srv.broadcast_command("say hi", server_ids=["b", "c"])
    assert "指定 2 台" in out
    assert sorted(sid for sid, _ in fake.sent) == ["b", "c"]


@pytest.mark.asyncio
async def test_broadcast_star_includes_offline(fake):
    out = await srv.broadcast_command("say hi", server_ids=["*"])
    assert "全部服务器" in out
    assert sorted(sid for sid, _ in fake.sent) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_empty_command_rejected(fake):
    out = await srv.broadcast_command("   ")
    assert "指令为空" in out
    assert fake.sent == []


@pytest.mark.asyncio
async def test_readonly_blocks_everything(fake):
    _bind(fake, readonly=True)
    out = await srv.broadcast_command("say hi")
    assert "只读模式" in out
    assert fake.sent == []


@pytest.mark.parametrize("danger", ["stop", "kill", "restart", "op Steve", "ban Steve", "whitelist off"])
@pytest.mark.asyncio
async def test_dangerous_needs_confirm(fake, danger):
    out = await srv.broadcast_command(danger)
    assert "高危操作" in out
    assert fake.sent == []


@pytest.mark.asyncio
async def test_dangerous_with_confirm_goes_through(fake):
    out = await srv.broadcast_command("stop", confirm=True)
    assert "成功 2" in out
    assert len(fake.sent) == 2


@pytest.mark.parametrize("safe", ["say 大家好", "list", "whitelist add Steve", "time set day"])
@pytest.mark.asyncio
async def test_safe_command_no_confirm_needed(fake, safe):
    out = await srv.broadcast_command(safe)
    assert "高危" not in out
    assert len(fake.sent) == 2


@pytest.mark.asyncio
async def test_single_failure_does_not_block_others():
    f = FakePanel({"a": "running", "b": "running"}, fail_ids={"a"})
    _bind(f)
    out = await srv.broadcast_command("say hi")
    assert "成功 1" in out
    assert "失败 1" in out
    assert [sid for sid, _ in f.sent] == ["b"]


@pytest.mark.asyncio
async def test_no_running_servers():
    f = FakePanel({"x": "offline"})
    _bind(f)
    out = await srv.broadcast_command("say hi")
    assert "没有 running" in out
    assert f.sent == []


def test_dangerous_prefix_matcher():
    assert srv._is_dangerous_broadcast("stop")
    assert srv._is_dangerous_broadcast("  STOP  ")       # 大小写与空白
    assert srv._is_dangerous_broadcast("op Steve")
    assert srv._is_dangerous_broadcast("whitelist off")
    # 不该误伤
    assert not srv._is_dangerous_broadcast("say stop is coming")
    assert not srv._is_dangerous_broadcast("list")
    assert not srv._is_dangerous_broadcast("whitelist add Steve")
    assert not srv._is_dangerous_broadcast("")


# ---------------- CLI: pteropilot --broadcast ----------------

def test_cli_missing_command_prints_usage(capsys):
    rc = srv._run_broadcast(["--broadcast"])
    assert rc == 2
    assert "用法" in capsys.readouterr().err


def test_cli_rejects_option_as_command(capsys):
    rc = srv._run_broadcast(["--broadcast", "--confirm"])
    assert rc == 2
    assert "用法" in capsys.readouterr().err


def test_cli_broadcast_ok(fake, capsys):
    rc = srv._run_broadcast(["--broadcast", "say hi"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "成功 2" in out


def test_cli_servers_filter(fake, capsys):
    rc = srv._run_broadcast(["--broadcast", "say hi", "--servers", "b"])
    out = capsys.readouterr().out
    assert rc == 0
    assert [sid for sid, _ in fake.sent] == ["b"]


def test_cli_dangerous_needs_confirm(fake, capsys):
    rc = srv._run_broadcast(["--broadcast", "stop"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "高危操作" in out
    assert fake.sent == []


def test_cli_dangerous_with_confirm(fake, capsys):
    rc = srv._run_broadcast(["--broadcast", "stop", "--confirm"])
    out = capsys.readouterr().out
    assert rc == 0
    assert len(fake.sent) == 2
