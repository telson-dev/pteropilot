"""list_players（查在线玩家）工具与日志解析测试。

面板无原生「在线玩家」端点，因此从控制台日志解析加入/退出行，重放得到在线名单。
覆盖：解析函数、客户端 online_players、工具层、空名单、无玩家行、异常去重。
"""

from __future__ import annotations

import httpx
import pytest

import pteropilot.server as srv
from pteropilot.client import PanelClient, parse_join, parse_leave
from pteropilot.config import Config


def _bind(fake, readonly: bool = False) -> None:
    cfg = Config(panel_url="https://panel", panel_api_key="k", readonly=readonly)
    srv.panel = PanelClient(cfg)
    srv.config = cfg


# ================= 解析函数（纯函数） =================


def test_parse_join_vanilla():
    assert parse_join("[12:00:00] [Server thread/INFO]: Steve joined the game") == "Steve"


def test_parse_join_chinese():
    assert parse_join("小明 加入了游戏") == "小明"


def test_parse_join_none():
    assert parse_join("[Server] Starting...") is None


def test_parse_leave_vanilla():
    assert parse_leave("Steve left the game") == "Steve"


def test_parse_leave_disconnected():
    assert parse_leave("Alex disconnected") == "Alex"


def test_parse_leave_chinese():
    assert parse_leave("小明 断开了连接") == "小明"


def test_parse_leave_none():
    assert parse_leave("[Server] Saving world") is None


# ================= 客户端 online_players =================


async def test_online_players_replay_order(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route(
        "GET",
        "/console",
        httpx.Response(
            200,
            text=(
                "Steve joined the game\n"
                "Alex joined the game\n"
                "Steve left the game\n"
            ),
        ),
    )
    assert await client.online_players("s1") == ["Alex"]


async def test_online_players_join_leave_rejoin(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route(
        "GET",
        "/console",
        httpx.Response(
            200,
            text=(
                "Steve joined the game\n"
                "Steve left the game\n"
                "Steve joined the game\n"
            ),
        ),
    )
    assert await client.online_players("s1") == ["Steve"]


async def test_online_players_empty_log(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("GET", "/console", httpx.Response(200, text=""))
    assert await client.online_players("s1") == []


async def test_online_players_no_player_lines(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route(
        "GET",
        "/console",
        httpx.Response(200, text="[Server] Starting...\n[DONE] ready"),
    )
    assert await client.online_players("s1") == []


async def test_online_players_dedup_duplicate_join(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route(
        "GET",
        "/console",
        httpx.Response(200, text="Steve joined the game\nSteve joined the game\n"),
    )
    assert await client.online_players("s1") == ["Steve"]


# ================= 工具层 list_players =================


async def test_list_players_tool(fake):
    _bind(fake)
    fake.route(
        "GET",
        "/console",
        httpx.Response(200, text="Steve joined the game\nAlex joined the game\n"),
    )
    out = await srv.list_players("s1")
    assert "2 人" in out and "Steve" in out and "Alex" in out


async def test_list_players_tool_empty(fake):
    _bind(fake)
    fake.route("GET", "/console", httpx.Response(200, text=""))
    out = await srv.list_players("s1")
    assert "没有检测到在线玩家" in out