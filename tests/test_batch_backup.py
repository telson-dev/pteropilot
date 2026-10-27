"""批量备份工具测试（Roadmap「批量」收尾）。

batch_backup_status：只读，遍历可访问服务器列出备份数量/最近备份时间，单台失败不阻断。
batch_backup：写操作，整体二次确认 + 只读拒绝，逐台创建、单台失败不阻断。

注意路由顺序：具体端点（backups）必须注册在父路由 /api/client 之前。
"""

from __future__ import annotations

import httpx
import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


def _servers_payload(data: list[dict]) -> dict:
    return {
        "data": data,
        "meta": {"pagination": {"total_pages": 1, "current_page": 1, "total": len(data)}},
    }


def _server(identifier: str) -> dict:
    return {"attributes": {"identifier": identifier, "name": identifier}}


def _backups_payload(items: list[dict]) -> dict:
    return {"data": [{"attributes": it} for it in items]}


# ================= batch_backup_status（只读）=================


async def test_status_empty(fake):
    _bind(fake)
    fake.route("GET", "/api/client", _servers_payload([]))
    out = await srv.batch_backup_status()
    assert "没有可访问的服务器" in out


async def test_status_all_success(fake):
    _bind(fake)
    fake.route("GET", "abc/backups", _backups_payload([
        {"uuid": "b1", "created_at": "2026-10-01T00:00:00Z"},
        {"uuid": "b2", "created_at": "2026-10-05T00:00:00Z"},
    ]))
    fake.route("GET", "def/backups", _backups_payload([]))
    fake.route("GET", "/api/client", _servers_payload([_server("abc"), _server("def")]))

    out = await srv.batch_backup_status()
    assert "共 2 台" in out
    assert "abc" in out and "2 个备份" in out and "最近: 2026-10-05" in out
    assert "def" in out and "0 个备份" in out and "最近: 无" in out
    assert "查询失败" not in out


async def test_status_single_failure_not_blocking(fake):
    _bind(fake)
    fake.route("GET", "abc/backups", Exception("boom"))
    fake.route("GET", "def/backups", _backups_payload([{"uuid": "b1", "created_at": "2026-10-02T00:00:00Z"}]))
    fake.route("GET", "/api/client", _servers_payload([_server("abc"), _server("def")]))

    out = await srv.batch_backup_status()
    assert "失败 1 台" in out
    assert "abc" in out and "查询失败" in out
    assert "def" in out and "1 个备份" in out  # def 仍被处理


# ================= batch_backup（写）=================


async def test_backup_readonly_denied(fake):
    _bind(fake, readonly=True)
    out = await srv.batch_backup(["abc"], confirm=True)
    assert "只读" in out or "readonly" in out.lower()


async def test_backup_no_servers(fake):
    _bind(fake)
    out = await srv.batch_backup([], confirm=True)
    assert "未指定任何服务器" in out


async def test_backup_confirm_required(fake):
    _bind(fake)
    out = await srv.batch_backup(["abc"], confirm=False)
    assert "已拦截" in out


async def test_backup_all_success(fake):
    _bind(fake)
    fake.route("POST", "abc/backups", {"attributes": {"uuid": "u1"}})
    fake.route("POST", "def/backups", {"attributes": {"uuid": "u2"}})
    out = await srv.batch_backup(["abc", "def"], confirm=True)
    assert "成功 2 台、失败 0 台" in out
    assert "u1" in out and "u2" in out


async def test_backup_single_failure_not_blocking(fake):
    _bind(fake)
    fake.route("POST", "abc/backups", Exception("no perm"))
    fake.route("POST", "def/backups", {"attributes": {"uuid": "u2"}})
    out = await srv.batch_backup(["abc", "def"], confirm=True)
    assert "成功 1 台、失败 1 台" in out
    assert "abc" in out and "❌" in out
    assert "def" in out and "✅" in out
