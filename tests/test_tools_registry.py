"""工具注册完整性 / 文档一致性测试。

防止「代码加了工具但文档没同步」或「工具被误删但文档还写着」：
- 实际注册的工具数与 docs/README.md 工具表一致；
- 每个工具都有描述（否则 MCP 客户端列表里是空说明）；
- 关键工具名不丢。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pteropilot import server

DOCS = Path(__file__).resolve().parent.parent / "docs" / "README.md"

# 文档工具表里的行形如：| `tool_name` | 只读 | 说明 | — |
_ROW_RE = re.compile(r"^\| `([a-z_]+)` \|", re.M)


def _actual_tools():
    return asyncio.run(server.mcp.list_tools())


def _doc_tools() -> set[str]:
    return set(_ROW_RE.findall(DOCS.read_text(encoding="utf-8")))


def test_doc_tool_table_matches_registration():
    """README 工具表与代码注册的工具集合必须完全一致。"""
    actual = {t.name for t in _actual_tools()}
    documented = _doc_tools()
    assert documented, "docs/README.md 里没有解析到工具表（格式变了？）"
    only_code = actual - documented
    only_doc = documented - actual
    assert not only_code, f"代码有但文档缺：{sorted(only_code)}"
    assert not only_doc, f"文档有但代码缺：{sorted(only_doc)}"


def test_tool_count_is_39():
    """当前工具数固定为 39；新增/删除工具时同步更新本断言与 docs/README.md。"""
    assert len(_actual_tools()) == 39


def test_every_tool_has_description():
    """每个 MCP 工具必须有非空描述。"""
    for t in _actual_tools():
        assert t.description and t.description.strip(), f"{t.name} 缺少描述"


def test_core_tools_present():
    """关键工具不能被误删。"""
    names = {t.name for t in _actual_tools()}
    for expected in (
        "list_my_servers",
        "check_panel_connection",
        "get_server_status",
        "power_signal",
        "send_command",
        "list_backups",
        "search_server_logs",
        "schedule_restart",
        "batch_get_status",
        "broadcast_message",
        "batch_get_console_log",
        "batch_list_players",
    ):
        assert expected in names, f"缺少核心工具 {expected}"
