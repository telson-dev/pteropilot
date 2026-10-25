"""服务器工具层补齐：只读列表、截断、空响应、非法输入等未覆盖路径。"""

from __future__ import annotations

import base64

import httpx
import pytest

import pteropilot.server as srv
from pteropilot.client import PanelClient
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


async def test_list_my_servers_empty(fake):
    _bind(fake)
    fake.route("GET", "/api/client", {"data": []})
    out = await srv.list_my_servers()
    assert "没有可访问" in out


async def test_list_all_servers(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/servers",
        {
            "data": [
                {"attributes": {"identifier": "a1", "name": "srv", "node": 3, "suspended": True}},
                {"attributes": {"identifier": "a2", "name": "s2", "node": 5, "suspended": False}},
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
    )
    out = await srv.list_all_servers()
    assert "a1" in out and "已挂起" in out and "正常" in out


async def test_list_all_servers_empty(fake):
    _bind(fake)
    fake.route("GET", "/servers", {"data": []})
    out = await srv.list_all_servers()
    assert "暂无服务器" in out


async def test_list_nodes_tool(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/nodes",
        {
            "data": [{"attributes": {"id": 1, "name": "n1", "fqdn": "n1.example.com"}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
    )
    out = await srv.list_nodes()
    assert "n1" in out and "n1.example.com" in out


async def test_list_nodes_tool_empty(fake):
    _bind(fake)
    fake.route("GET", "/nodes", {"data": []})
    out = await srv.list_nodes()
    assert "暂无节点" in out


async def test_list_files_tool(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/files/list",
        {"data": [
            {"attributes": {"name": "a.txt", "size": 1, "is_file": True}},
            {"attributes": {"name": "d", "size": None, "is_file": False}},
        ]},
    )
    out = await srv.list_files("s1", "/")
    assert "[FILE] a.txt" in out and "[DIR ] d" in out


async def test_list_files_tool_empty(fake):
    _bind(fake)
    fake.route("GET", "/files/list", {"data": []})
    out = await srv.list_files("s1", "/")
    assert "为空" in out


async def test_read_file_truncates(fake):
    _bind(fake)
    fake.route("GET", "/files/contents", httpx.Response(200, text="x" * 25000))
    out = await srv.read_file("s1", "/big.txt")
    assert "已截断" in out and out.startswith("x" * 20000)


async def test_read_file_no_truncate(fake):
    _bind(fake)
    fake.route("GET", "/files/contents", httpx.Response(200, text="short"))
    assert await srv.read_file("s1", "/a.txt") == "short"


async def test_download_file_empty(fake):
    _bind(fake)
    fake.route("GET", "/files/download", {"object": "signed_url", "attributes": {"url": ""}})
    out = await srv.download_file("s1", "/a.txt")
    assert "未获取到" in out


async def test_list_backups_tool(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/backups",
        {"data": [
            {"attributes": {"uuid": "b1", "name": "full", "bytes": 10, "completed_at": "x", "is_locked": True}},
            {"attributes": {"uuid": "b2", "name": "part", "bytes": 5, "completed_at": None, "is_locked": False}},
        ]},
    )
    out = await srv.list_backups("s1")
    assert "完成" in out and "进行中" in out and "已锁定" in out and "未锁定" in out


async def test_list_backups_empty(fake):
    _bind(fake)
    fake.route("GET", "/backups", {"data": []})
    out = await srv.list_backups("s1")
    assert "暂无备份" in out


async def test_power_invalid_signal(fake):
    _bind(fake)
    out = await srv.power_signal("s1", "explode")
    assert "非法信号" in out


async def test_write_file_with_confirm(fake):
    _bind(fake)
    fake.route("POST", "/files/write", {})
    out = await srv.write_file("s1", "/a.txt", "hello", confirm=True)
    assert "✅" in out and "5 字符" in out


async def test_create_folder_tool_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.create_folder("s1", "/", "mods")
    assert "只读模式" in out


async def test_delete_backup_confirm(fake):
    _bind(fake)
    blocked = await srv.delete_backup("s1", "b1")
    assert "高危" in blocked
    fake.route("DELETE", "/backups", {})
    done = await srv.delete_backup("s1", "b1", confirm=True)
    assert "✅" in done


async def test_delete_backup_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.delete_backup("s1", "b1", confirm=True)
    assert "只读模式" in out
    assert fake.requests == []


async def test_upload_invalid_base64(fake):
    _bind(fake)
    out = await srv.upload_file("s1", "/", "a.jar", "!!!not-base64!!!", confirm=True)
    assert "base64" in out


async def test_send_command_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.send_command("s1", "say hi")
    assert "只读模式" in out


async def test_create_backup_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.create_backup("s1")
    assert "只读模式" in out


async def test_restore_backup_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.restore_backup("s1", "b1")
    assert "只读模式" in out


async def test_rename_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.rename_file("s1", "/", "a", "b")
    assert "只读模式" in out


async def test_upload_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.upload_file("s1", "/", "a.jar", base64.b64encode(b"x").decode())
    assert "只读模式" in out


async def test_delete_files_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.delete_files("s1", ["/x"])
    assert "只读模式" in out


async def test_write_readonly(fake):
    _bind(fake, readonly=True)
    out = await srv.write_file("s1", "/a.txt", "x")
    assert "只读模式" in out


async def test_get_server_status_string_values(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/resources",
        {"attributes": {"current_state": "running", "resources": {"memory_bytes": None, "disk_bytes": None}}},
    )
    out = await srv.get_server_status("s1")
    assert "running" in out


async def test_console_log_truncates(fake):
    _bind(fake)
    fake.route("GET", "/console", httpx.Response(200, text="y" * 10000))
    out = await srv.get_console_log("s1", max_chars=8000)
    assert "已截断" in out and out.endswith("y" * 8000 + "\n...[已截断，仅显示末尾 8000 字符]")


async def test_list_backups_unknown_name(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/backups",
        {"data": [{"attributes": {"uuid": "b1", "bytes": 0, "completed_at": "x", "is_locked": False}}]},
    )
    out = await srv.list_backups("s1")
    assert "?" in out  # _name 回退


def test_main_validate_error(monkeypatch, capsys):
    # 配置缺失时 main() 走启动失败分支
    import sys

    monkeypatch.setattr(srv.config, "panel_url", "")
    monkeypatch.setattr(srv.config, "panel_api_key", "")
    monkeypatch.setattr(srv.mcp, "run", lambda: (_ for _ in ()).throw(AssertionError("should not run")))
    with pytest.raises(SystemExit) as e:
        srv.main()
    assert e.value.code == 1
    captured = capsys.readouterr()
    assert "启动失败" in captured.err