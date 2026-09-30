"""MCP 工具层与护栏测试（只读拦截 / confirm 二次确认）。"""

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


async def test_list_my_servers(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/api/client",
        {"data": [{"attributes": {"identifier": "s1", "name": "survival", "uuid": "u1"}}]},
    )
    out = await srv.list_my_servers()
    assert "s1" in out and "survival" in out


async def test_status_formatting(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/resources",
        {
            "attributes": {
                "current_state": "running",
                "resources": {
                    "cpu_absolute": 33.0,
                    "memory_bytes": 1048576,
                    "disk_bytes": 2097152,
                    "uptime": 5000,
                },
            }
        },
    )
    out = await srv.get_server_status("s1")
    assert "running" in out and "33.0%" in out and "1.0 MB" in out


async def test_readonly_blocks_power(fake):
    _bind(fake, readonly=True)
    out = await srv.power_signal("s1", "start")
    assert "只读模式" in out
    # 没有任何请求真正发出
    assert fake.requests == []


async def test_restart_requires_confirm(fake):
    _bind(fake)
    out = await srv.power_signal("s1", "restart", confirm=False)
    assert "高危" in out
    assert not any(r.method == "POST" for r in fake.requests)


async def test_start_does_not_need_confirm(fake):
    _bind(fake)
    fake.route("POST", "/power", {})
    out = await srv.power_signal("s1", "start")
    assert "✅" in out


async def test_restart_with_confirm(fake):
    _bind(fake)
    fake.route("POST", "/power", {})
    out = await srv.power_signal("s1", "restart", confirm=True)
    assert "✅" in out


async def test_write_requires_confirm(fake):
    _bind(fake)
    out = await srv.write_file("s1", "/a.txt", "data")
    assert "高危" in out


async def test_delete_files_confirm_then_post(fake):
    _bind(fake)
    blocked = await srv.delete_files("s1", ["/x.txt"])
    assert "高危" in blocked
    fake.route("POST", "/files/delete", {})
    done = await srv.delete_files("s1", ["/x.txt"], confirm=True)
    assert "✅" in done


async def test_restore_backup_guard(fake):
    _bind(fake)
    blocked = await srv.restore_backup("s1", "b1")
    assert "高危" in blocked
    fake.route("POST", "/restore", {})
    done = await srv.restore_backup("s1", "b1", confirm=True)
    assert "✅" in done


async def test_create_backup_ok(fake):
    _bind(fake)
    fake.route("POST", "/backups", {"attributes": {"uuid": "bk1"}})
    out = await srv.create_backup("s1")
    assert "bk1" in out
