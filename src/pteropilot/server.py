"""PteroPilot MCP server。

工具分两类：
- 只读：列表 / 状态 / 读取，随时可用。
- 写入/危险：电源、指令、写文件、删除、恢复等——
  * PANEL_READONLY=1 时一律拒绝；
  * 高危操作（restart/kill、覆盖、删除、恢复备份）必须显式 confirm=True。
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from . import playerlog
from .client import POWER_SIGNALS, PanelClient, redact
from .config import Config, key_role_warnings

mcp = FastMCP("pteropilot")
# mcp 1.30 的 FastMCP 不接 version 参数，握手时会 fallback 成 mcp 框架自己的
# 版本号（1.30.0）。只能直接改 low-level server，客户端才能看到真实版本。
mcp._mcp_server.version = __version__
config = Config()
panel = PanelClient(config)


# 批量工具同时最多打多少台服务器。
# 面板是单实例 + 单数据库，几十条连接同时进来容易 429 或把整机拖慢；
# 8 条在实测里既快又不惹人，可以用环境变量调（夹在 1–32）。
def _read_concurrency() -> int:
    raw = os.environ.get("PTEROPILOT_CONCURRENCY", "").strip()
    if not raw:
        return 8
    try:
        return max(1, min(32, int(raw)))
    except ValueError:
        return 8


MAX_CONCURRENCY = _read_concurrency()


async def _gather_bounded(fn, items, limit: int | None = None):
    """像 ``asyncio.gather`` 一样保序，但同时只跑 ``limit`` 个。

    ``fn`` 通常自己把异常吞掉并转成一行错误文本（批量工具的口径是「单台失败
    不阻断其余」），所以这里不需要 ``return_exceptions``。
    """
    everything = list(items)
    if not everything:
        return []
    lim = max(1, limit or MAX_CONCURRENCY)
    sem = asyncio.Semaphore(lim)

    async def _one(item):
        async with sem:
            return await fn(item)

    return await asyncio.gather(*(_one(x) for x in everything))


def _attrs(item: Any) -> dict:
    if isinstance(item, dict) and "attributes" in item:
        return item["attributes"]
    return item if isinstance(item, dict) else {}


def _denied_if_readonly() -> str | None:
    if config.readonly:
        return "⛔ 当前为只读模式（PANEL_READONLY=1），已拒绝该操作。"
    return None


def _need_confirm(confirm: bool, what: str) -> str | None:
    if not confirm:
        return f"⚠️ {what} 属于高危操作，已拦截。确认无误后请带 confirm=True 重新调用。"
    return None


def _name(attrs: dict) -> str:
    return attrs.get("name") or attrs.get("identifier") or "?"


async def _resolve_target_ids(server_ids: list[str]) -> list[str]:
    """批量工具的目标解析：``["*"]`` → 当前可访问的全部服务器标识；否则原样返回。"""
    if server_ids != ["*"]:
        return list(server_ids)
    servers = await panel.client_servers()
    ids: list[str] = []
    for s in servers:
        a = _attrs(s)
        sid = a.get("identifier") or a.get("id")
        if sid:
            ids.append(str(sid))
    return ids


# ================= 只读工具 =================


@mcp.tool()
async def list_my_servers(status: str = "", with_status: bool = False) -> str:
    """列出当前 API Key 可访问的服务器：标识、名称、UUID。

    - ``with_status=True``：并发拉取每台实时状态并显示（running / offline / ...）；
    - ``status`` 非空时按状态过滤（如 ``"running"``，不区分大小写），
      需要 ``with_status=True`` 才生效——适合"列出所有在跑的服务器"。
    """
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"
    rows = []
    for s in servers:
        a = _attrs(s)
        rows.append((a, _name(a)))

    if not with_status:
        lines = []
        for a, name in rows:
            lines.append(f"- {a.get('identifier', '?')} | {name} | {a.get('uuid', '?')}")
        return "\n".join(lines)

    async def _state(sid: str) -> str:
        try:
            res = await panel.resources(sid)
            return res.get("current_state", "?")
        except Exception:
            return "?"  # 单台状态拉取失败不阻断

    states = await _gather_bounded(lambda a: _state(a.get("identifier") or a.get("id") or "?"), [a for a, _ in rows])
    lines = [
        f"- {a.get('identifier', '?')} | {name} | {a.get('uuid', '?')} | {st}"
        for (a, name), st in zip(rows, states)
    ]
    if status:
        want = status.strip().lower()
        matched = [l for l in lines if l.split(" | ")[-1].lower() == want]
        if not matched:
            return f"(没有状态为 {status} 的服务器)"
        return f"状态过滤: {status}\n" + "\n".join(matched)
    return "\n".join(lines)


@mcp.tool()
async def check_panel_connection() -> str:
    """诊断面板连接（首次配置后先跑一次）。

    验证 PANEL_URL / API Key 是否可用，报告可访问服务器数量与密钥角色提示；
    失败时给出可操作的排查顺序（地址 / 网络 / 密钥）。
    """
    url = (config.panel_url or "").rstrip("/")
    if not url:
        return (
            "❌ 未配置面板地址（PANEL_URL）。\n"
            "请在 MCP 配置里设置形如 https://your-panel.example.com 的地址（要带 https://）。"
        )
    lines = [
        f"面板地址: {url}",
        f"运行模式: {'只读' if config.readonly else '可写'}",
    ]
    try:
        servers = await panel.client_servers()
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        lines.append(f"❌ 连接失败：{e}")
        if status == 401:
            lines.append("提示：401 = API Key 无效或已撤销，请到面板「账户 → API 凭证」重新生成。")
        elif status == 403:
            lines.append(
                "提示：403 = 密钥权限不足。核心工具走客户端 API，请确认 PANEL_API_KEY 用的是"
                "客户端密钥（ptlc_ / pacc_），而不是应用密钥（ptla_ / papp_）。"
            )
        elif status == 404:
            lines.append("提示：404 = 地址不对。检查是否漏了路径，或填的不是面板根地址。")
        else:
            lines.append(
                "提示：依次检查 ① 地址是否正确且可从本机访问（浏览器打开面板首页试试）"
                "② 网络/代理设置 ③ API Key 是否复制完整。"
            )
        return "\n".join(lines)

    lines.append(f"✅ 已连通，可访问服务器 {len(servers)} 台")
    if servers:
        shown = []
        for s in servers[:5]:
            a = _attrs(s)
            shown.append(f"{a.get('identifier', '?')}({_name(a)})")
        lines.append("示例: " + "、".join(shown) + (" 等" if len(servers) > 5 else ""))
    warns = key_role_warnings(config)
    if warns:
        lines.append("密钥角色提示:")
        lines.extend(f"- {w}" for w in warns)
    return "\n".join(lines)


@mcp.tool()
async def list_all_servers() -> str:
    """【管理员】列出面板上全部服务器：标识、名称、所属节点、挂起状态。"""
    servers = await panel.list_servers()
    if not servers:
        return "(面板上暂无服务器)"
    lines = []
    for s in servers:
        a = _attrs(s)
        state = "已挂起" if a.get("suspended") else "正常"
        lines.append(
            f"- {a.get('identifier', '?')} | {_name(a)} | 节点 {a.get('node', '?')} | {state}"
        )
    return "\n".join(lines)


@mcp.tool()
async def list_nodes() -> str:
    """【管理员】列出所有节点：名称、位置、FQDN。"""
    nodes = await panel.list_nodes()
    if not nodes:
        return "(暂无节点)"
    lines = []
    for n in nodes:
        a = _attrs(n)
        lines.append(f"- {a.get('id', '?')} | {_name(a)} | {a.get('fqdn', '?')}")
    return "\n".join(lines)


def _fmt_uptime(ms: Any) -> str:
    """把毫秒在线时长格式化成人类可读（如 3661000 → '1 小时 1 分 1 秒'）。"""
    if not isinstance(ms, (int, float)) or ms <= 0:
        return str(ms) if isinstance(ms, (int, float)) else str(ms)
    total = int(ms // 1000)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    if minutes:
        parts.append(f"{minutes} 分")
    if seconds or not parts:
        parts.append(f"{seconds} 秒")
    return " ".join(parts)


@mcp.tool()
async def get_server_status(server_id: str) -> str:
    """查看单个服务器实时状态与资源占用：状态、CPU、内存、磁盘、网络、在线时长、在线玩家数（日志推断）。"""
    res = await panel.resources(server_id)
    state = res.get("current_state", "?")
    r = res.get("resources", res)

    def mb(x: Any) -> str:
        return f"{x / 1024 / 1024:.1f} MB" if isinstance(x, (int, float)) else str(x)

    lines = [
        f"服务器: {server_id}",
        f"状态: {state}",
        f"CPU: {r.get('cpu_absolute', '?')}%",
        f"内存: {mb(r.get('memory_bytes'))}",
        f"磁盘: {mb(r.get('disk_bytes'))}",
        f"网络 rx/tx: {_fmt_size(r.get('network_rx_bytes'))} / {_fmt_size(r.get('network_tx_bytes'))}",
        f"在线时长: {_fmt_uptime(r.get('uptime', 0))}",
    ]
    # 资源上限（面板返回时才显示，如内存/磁盘配额）
    mem_limit = r.get("memory_limit")
    if isinstance(mem_limit, (int, float)) and mem_limit > 0:
        lines.append(f"内存上限: {mb(mem_limit)}")
    disk_limit = r.get("disk_limit")
    if isinstance(disk_limit, (int, float)) and disk_limit > 0:
        lines.append(f"磁盘上限: {mb(disk_limit)}")
    # 在线玩家数：基于控制台日志推断（非权威）；失败静默省略，不影响主输出
    try:
        players = await panel.online_players(server_id)
        lines.append(f"在线玩家: {len(players)}（日志推断）")
    except Exception:
        pass
    return "\n".join(lines)


# 面板 current_state 常见取值及其健康判定。
# healthy=运行正常；transitional=启动/停机中间态（通常无需干预）；
# abnormal=需要关注的异常态。未识别取值归入 unknown。
_STATE_HEALTH = {
    "running": ("healthy", "服务器运行正常。"),
    "starting": ("transitional", "服务器正在启动，稍候即就绪。"),
    "stopping": ("transitional", "服务器正在停机，稍候即离线。"),
    "offline": ("abnormal", "服务器已离线（进程未在运行）。若应在线，可能崩溃或被手动停止。"),
    "error": ("abnormal", "服务器处于错误状态。通常是启动失败或运行中崩溃。"),
    "suspended": ("abnormal", "服务器已被挂起（通常是管理员或欠费）。需管理员解除挂起。"),
    "installing": ("transitional", "服务器正在安装/重装环境。"),
    "reinstalling": ("transitional", "服务器正在重装，请勿打断。"),
    "restoring_backup": ("transitional", "服务器正在从备份恢复。"),
    "transferring": ("transitional", "服务器正在迁移节点。"),
}


def classify_state(state: str) -> tuple[str, str]:
    """把面板 current_state 翻译成 (健康等级, 中文提示)。"""
    return _STATE_HEALTH.get(state, ("unknown", f"未知状态「{state}」，请人工确认。"))


@mcp.tool()
async def batch_get_status() -> str:
    """批量查看我可访问的所有服务器的状态与健康度摘要（Roadmap「批量开服」骨架）。

    并发拉取当前 API Key 可访问的每台服务器，返回「标识 | 名称 | 状态 | 健康等级」。
    只读、纯摘要，不触碰任何写入/危险操作（批量操作暂不扩展，逐台 confirm 语义不变）。
    单台调用超时或失败不会阻断整体，会单独标注为「查询失败」。
    """
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"

    async def _one(s: dict) -> tuple[str, bool]:
        a = _attrs(s)
        ident = a.get("identifier") or a.get("uuid") or "?"
        name = _name(a)
        try:
            res = await panel.resources(ident)
            state = res.get("current_state", "?")
            level, _hint = classify_state(state)
            level_cn = {"healthy": "🟢", "transitional": "🟡", "abnormal": "🔴", "unknown": "⚪"}[level]
            return f"- {ident} | {name} | {state} | {level_cn}", True
        except Exception:
            # 单台失败（超时/无权限/面板报错）不阻断整体，标注后继续。
            return f"- {ident} | {name} | 查询失败（超时或面板报错）", False

    results = await _gather_bounded(_one, servers)
    lines = [line for line, _ok in results]
    ok = sum(1 for _line, success in results if success)
    failed = len(lines) - ok

    header = f"共 {len(servers)} 台服务器，成功 {ok} 台、失败 {failed} 台："
    return header + "\n" + "\n".join(lines)


@mcp.tool()
async def get_server_network(server_id: str) -> str:
    """查看服务器的网络分配（IP / 端口，只读）：主地址与附加端口、备注。"""
    allocs = await panel.network_allocations(server_id)
    if not allocs:
        return "(没有网络分配记录)"
    lines = []
    primary_addr = ""
    for it in allocs:
        a = _attrs(it)
        ip = a.get("ip_alias") or a.get("ip") or "?"
        port = a.get("port", "?")
        mark = " ⭐主地址" if a.get("is_primary") else ""
        notes = a.get("notes") or ""
        note_txt = f"（{notes}）" if notes else ""
        addr = f"{ip}:{port}"
        if a.get("is_primary") and not primary_addr:
            primary_addr = addr
        lines.append(f"- {addr}{mark}{note_txt}")
    head = f"共 {len(allocs)} 个网络分配"
    if primary_addr:
        head += f"，主地址: {primary_addr}"
    return head + "：\n" + "\n".join(lines)


@mcp.tool()
async def list_databases(server_id: str) -> str:
    """查看服务器在面板里已分配的数据库（MySQL）：主机地址、库名、用户名、来源限制。

    只读，不创建/删除数据库、不修改权限。适合 MC 服主配置插件（如 LuckPerms /
    CoreProtect 需要 MySQL）时查「数据库地址和账号是什么」。
    **密码不在客户端 API 返回范围内**，需到面板网页端查看。
    """
    dbs = await panel.databases(server_id)
    if not dbs:
        return "(该服务器没有分配数据库；如需 MySQL 请联系面板管理员在面板中创建)"
    lines: list[str] = []
    for d in dbs:
        a = _attrs(d)
        host = a.get("host") or {}
        host_addr = host.get("host") or host.get("address") or "?"
        port = host.get("port", "?")
        name = a.get("name") or "?"
        user = a.get("username") or "?"
        conn = a.get("connections_from") or "%"
        lines.append(
            f"- {name} | 主机 {host_addr}:{port} | 用户名 {user} | 来源限制 {conn}"
        )
    return f"共 {len(dbs)} 个数据库（密码需到面板网页端查看）：\n" + "\n".join(lines)


@mcp.tool()
async def list_schedules(server_id: str) -> str:
    """查看服务器在面板里配置的定时任务（计划任务：cron 表达式 + 执行内容）。

    只读，不创建/不修改/不触发任务。适合回答「自动重启配好了吗」「这个服有哪些
    定时任务」「下次几点备份」。若想临时安排一次重启，用 schedule_restart。
    """
    items = await panel.schedules(server_id)
    if not items:
        return "(该服务器没有配置定时任务；可在面板「Schedules」页新建，或用 schedule_restart 临时安排一次重启)"
    rows: list[str] = []
    for it in items:
        a = _attrs(it)
        name = a.get("name") or "(未命名)"
        cron = a.get("cron") or {}
        # 面板把 cron 拆成五段字段
        cron_txt = " ".join(
            str(cron.get(k, "*")) for k in ("minute", "hour", "day_of_month", "month", "day_of_week")
        )
        only_online = "仅在线时执行" if a.get("only_when_online") else "总是执行"
        enabled = "启用" if a.get("is_active") else "已停用"
        last_run = (a.get("last_run_at") or "").split(".")[0].replace("T", " ") or "从未"
        next_run = (a.get("next_run_at") or "").split(".")[0].replace("T", " ") or "—"
        # 任务步骤数（每次触发要执行几条指令）
        rel = a.get("relationships") or {}
        tasks = (rel.get("tasks") or {}).get("data") if isinstance(rel, dict) else None
        steps = f"，{len(tasks)} 步" if isinstance(tasks, list) and tasks else ""
        rows.append(
            f"- {name} | cron {cron_txt} | {enabled} | {only_online}{steps}\n"
            f"    上次运行 {last_run} | 下次运行 {next_run}"
        )
    return f"共 {len(items)} 个定时任务：\n" + "\n".join(rows)


@mcp.tool()
async def get_server_activity(server_id: str, limit: int = 20) -> str:
    """查看服务器的操作审计日志（谁在什么时候做了什么，只读）。

    面板记录的服务器事件流：电源操作、文件上传/删除、备份、指令等，都会带上
    操作者与时间。用来回答「谁重启了我的服务器」「谁删了这个文件」「最近谁动过配置」。

    limit 控制返回条数（1-100，默认 20，取最新的在前）。只读，不改任何设置。
    """
    limit = max(1, min(int(limit), 100))
    events = await panel.activity(server_id)
    if not events:
        return "(没有操作记录；面板可能未开启活动日志，或该服务器近期无操作)"
    rows: list[str] = []
    for e in events[:limit]:
        a = _attrs(e)
        event = a.get("event") or "?"
        ts_raw = a.get("timestamp") or ""
        # 面板返回 ISO8601；这里只保留到分钟以便阅读
        ts = ts_raw.split(".")[0].replace("T", " ") if ts_raw else "?"
        actor = a.get("actor") or "?"
        if isinstance(actor, dict):
            actor = actor.get("username") or actor.get("email") or "?"
        # is_system=True 表示面板自身/计划任务触发，不是人操作
        who = f"{actor}（系统）" if a.get("is_system") else str(actor)
        meta = a.get("metadata")
        extra = ""
        if isinstance(meta, dict) and meta:
            # 只取有意义的短字段，避免刷屏
            for key in ("name", "file", "directory", "signal"):
                if meta.get(key):
                    extra = f" [{meta[key]}]"
                    break
        rows.append(f"- {ts} | {who} | {event}{extra}")
    header = f"共 {len(events)} 条记录，显示最新 {len(rows)} 条："
    return header + "\n" + "\n".join(rows)


@mcp.tool()
async def check_server_health(server_id: str) -> str:
    """检查服务器健康度：对比 current_state，把异常态翻译成中文结构化提示。

    只读、不发外部告警。返回健康等级 + 状态说明 + 建议动作，供 AI 判断是否
    需要进一步排查（如看控制台日志）或提醒用户。
    """
    res = await panel.resources(server_id)
    state = res.get("current_state", "?")
    level, hint = classify_state(state)
    level_cn = {"healthy": "🟢 正常", "transitional": "🟡 中间态", "abnormal": "🔴 异常", "unknown": "⚪ 未知"}[level]
    actions = {
        "healthy": "无需干预。",
        "transitional": "通常无需干预，可稍后复查；若长时间停留在此状态再排查。",
        "abnormal": "建议：① 用 get_console_log 查看最近日志定位崩溃/停止原因；② 若确认需恢复，用 power_signal start 拉起；③ 若为挂起，联系管理员。",
        "unknown": "面板返回了未识别的状态，建议用 get_server_status 查看完整信息并人工确认。",
    }[level]
    return (
        f"服务器: {server_id}\n"
        f"状态: {state}\n"
        f"健康度: {level_cn}\n"
        f"说明: {hint}\n"
        f"建议: {actions}"
    )


# 崩溃信号关键词表（小写匹配，按类别归类）。
# 宁可漏报也不误报：只收录明确的崩溃/异常措辞，避免把普通警告当崩溃。
_CRASH_PATTERNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "memory": (
        "内存不足（Out of Memory）",
        ("out of memory", "outofmemory", "oom-kill", "cannot allocate memory", "memory exhausted", "unable to allocate"),
    ),
    "jvm": (
        "JVM 崩溃",
        ("hs_err_pid", "fatal error has been detected", "exception in thread", "java.lang.outofmemoryerror"),
    ),
    "exit": (
        "进程异常退出",
        ("process exited with code", "exit code", "server thread has stopped", "shutting down the server with an error"),
    ),
    "fatal": (
        "致命错误",
        ("a fatal error", "crash report", "crash-reports", "fatal: unable"),
    ),
    # Paper/Spigot 看门狗：主线程 tick 卡死超时、看门狗强杀，是明确的运行中崩溃信号
    # （比「内存不足」更早出现，常伴随 freeze 后 watchdog 重启）。只收录明确的
    # watchdog 措辞，不收录「Can't keep up!」这类 WARN 级负载告警（会误报健康日志）。
    "watchdog": (
        "看门狗超时（tick 卡死）",
        ("server watchdog thread", "a single server tick took", "thread watchdog",
         "has not responded for", "看门狗"),
    ),
    # JVM 原生崩溃：SIGSEGV/segfault 是 hs_err_pid 之外出现在 stderr/控制台的裸崩溃标记。
    "native": (
        "原生崩溃（SIGSEGV）",
        ("sigsegv", "segmentation fault", "segfault"),
    ),
}


@mcp.tool()
async def diagnose_crash(server_id: str, tail: int = 60) -> str:
    """诊断服务器是否崩溃/异常（Roadmap「崩服告警」检测点，只读）。

    结合当前状态与最近控制台日志：扫描日志中的崩溃信号（内存不足 / JVM 崩溃 /
    看门狗超时 / 原生崩溃 SIGSEGV / 进程异常退出 / 致命错误），输出检测结果、命中行数与建议动作。
    供 AI 判断是否需要提醒用户或执行恢复（如 power_signal start）。
    只读、不发外部告警；tail 控制扫描的日志行数。
    """
    res = await panel.resources(server_id)
    state = res.get("current_state", "?")
    level, _hint = classify_state(state)
    level_cn = {"healthy": "🟢 正常", "transitional": "🟡 中间态", "abnormal": "🔴 异常", "unknown": "⚪ 未知"}[level]

    log = await panel.console_log(server_id)
    recent = log.splitlines()[-max(tail, 1):]

    hits: dict[str, tuple[str, list[str]]] = {}
    for ln in recent:
        low = ln.lower()
        for cat, (label, kws) in _CRASH_PATTERNS.items():
            if any(k in low for k in kws):
                if cat not in hits:
                    hits[cat] = (label, [])
                hits[cat][1].append(ln)

    out = [f"服务器: {server_id}", f"当前状态: {state}", f"健康度: {level_cn}"]
    if hits:
        out.append(f"崩溃信号: {len(hits)} 类")
        for cat, (label, matched) in hits.items():
            out.append(f"  - {label}: 命中 {len(matched)} 行，最近一条: {matched[-1][:160]}")
        if sum(len(m) for _, m in hits.values()) >= 3:
            out.append("疑似反复崩溃（短时间内多次出现崩溃信号），建议优先处理。")
    else:
        out.append("崩溃信号: 未发现（最近日志中未见明显崩溃措辞）")

    out.append("最近日志（尾部，最多 10 行）:")
    out.append("\n".join(recent[-10:]) or "(日志为空)")

    if level == "abnormal":
        out.append("建议: ① 依据上方信号定位原因；② 确认后可用 power_signal start 拉起；③ 反复崩溃请检查内存/启动参数。")
    elif hits:
        out.append("建议: 日志发现崩溃信号但当前状态正常，可能是历史残留或即将崩溃，建议观察或重启排查。")
    else:
        out.append("建议: 状态正常且无崩溃信号，无需干预。")
    return "\n".join(out)


@mcp.tool()
async def list_mounts(server_id: str) -> str:
    """查看服务器的挂载点（额外存储盘）：挂了哪些目录、是否只读。

    只读。挂载点由面板管理员分配，决定这个服能访问哪些额外文件
    （常见于把世界存档 / 插件配置放在共享存储上）。「我上传的文件怎么看不到」
    往往是挂载没配或只读。
    """
    mounts = await panel.server_mounts(server_id)
    if not mounts:
        return "(该服务器没有挂载点：所有文件都在本机卷内；如需要共享存储，请在面板为该服分配 mount)"

    rows: list[str] = []
    for m in mounts:
        attrs = (m or {}).get("attributes") or {}
        name = attrs.get("name") or attrs.get("id") or "?"
        source = attrs.get("source") or ""
        target = attrs.get("target") or ""
        ro = attrs.get("read_only")
        flags: list[str] = []
        if ro is True:
            flags.append("只读")
        elif ro is False:
            flags.append("可写")
        if attrs.get("user_mountable"):
            flags.append("用户可自行挂载")
        path = f"{source} → {target}" if source or target else ""
        row = f"- {name}" + (f" | {path}" if path else "") + (f" | {'、'.join(flags)}" if flags else "")
        rows.append(row)
    return f"共 {len(mounts)} 个挂载点：\n" + "\n".join(rows)


@mcp.tool()
async def list_server_users(server_id: str) -> str:
    """查看能管理这台服务器的子用户（邮箱 + 权限数量），确认「谁能动我的服」。

    只读。返回面板为该服务器分配的 subuser 列表；子用户由面板管理员在
    「Users」页添加，权限（如能否重启、能否改文件）决定他们能做什么。
    """
    users = await panel.server_users(server_id)
    if not users:
        return "(该服务器没有添加子用户：只有面板账号本人能管理；如需要他人协助，可在面板「Users」页添加并勾选权限)"

    rows: list[str] = []
    for u in users:
        a = _attrs(u)
        email = a.get("email") or a.get("username") or "?"
        name = a.get("username") or ""
        perms = a.get("server_permissions") or []
        # server_permissions 通常是 [{permission: "control.restart"}, ...]
        keys: list[str] = []
        for item in perms:
            if isinstance(item, dict):
                p = item.get("permission") or ""
            else:
                p = str(item)
            if p:
                keys.append(p.split(".")[0])
        uniq = sorted(set(keys))
        perm_txt = f"{len(perms)} 项（涉及 {'/'.join(uniq)}）" if perms else "无权限记录"
        label = f"{email}" + (f"（{name}）" if name and name != email else "")
        rows.append(f"- {label} | 权限 {perm_txt}")
    return f"共 {len(users)} 个子用户：\n" + "\n".join(rows)


@mcp.tool()
async def analyze_player_activity(server_id: str, top: int = 10) -> str:
    """分析最近控制台日志里的玩家加入/退出事件：活跃玩家排行与高峰时段。

    只读。回答「谁最常来」「大概什么时候人多」。数据来源于面板能取到的
    最近一段日志（时间窗口受面板日志长度限制），**不是完整历史统计**。
    - top：排行显示人数，1-20，默认 10。
    """
    import re
    from collections import Counter

    from .client import parse_join, parse_leave

    log = await panel.console_log(server_id)
    lines = log.splitlines()
    top = max(1, min(int(top), 20))

    joins: Counter[str] = Counter()
    leaves: Counter[str] = Counter()
    hours: Counter[int] = Counter()
    order: list[str] = []
    # 时间戳形如 [12:34:56] 或 2026-11-09 12:34:56
    ts_re = re.compile(r"\b(\d{2}):\d{2}:\d{2}\b")

    for line in lines:
        name = parse_join(line)
        if name:
            joins[name] += 1
            if name not in order:
                order.append(name)
            m = ts_re.search(line)
            if m:
                hours[int(m.group(1))] += 1
            continue
        name = parse_leave(line)
        if name:
            leaves[name] += 1
            if name not in order:
                order.append(name)

    if not joins and not leaves:
        return (
            "最近日志里没有找到玩家加入/退出记录（可能日志被截断、玩家很少、"
            "或服务器用的不是常见的 MC 日志格式）。"
        )

    rows = [f"- {name}: 进 {joins[name]} 次 / 退 {leaves[name]} 次" for name in order[:top]]
    out = [f"最近日志中的玩家活动（共 {len(order)} 个玩家名）", ""]
    out += rows
    if len(order) > top:
        out.append(f"（只显示前 {top} 个，共 {len(order)} 个）")

    if hours:
        peak = hours.most_common(3)
        peak_txt = "、".join(f"{h:02d} 点（{n} 次进入）" for h, n in peak)
        out += ["", f"高峰时段：{peak_txt}"]

    # 只进没退的玩家 = 最近应当还在线（与 list_players 口径一致）
    still = [n for n in order if joins[n] > leaves[n]]
    if still:
        out += ["", f"日志末尾仍在线（进多退少）：{', '.join(still[:top])}"]

    out += ["", "⚠️ 统计窗口＝面板能返回的最近一段日志，不代表完整历史。"]
    return "\n".join(out)


@mcp.tool()
async def list_players(server_id: str) -> str:
    """查当前在线的玩家：从最近控制台日志解析玩家加入/退出，列出在线名单。
    
    注意：此名单基于最近控制台日志推断，**非权威**。控制台读取端点通常只返回最近约 1000 行，
    更早的玩家加入/退出或服务器重启可能已被截断，名单可能不完整或包含已离线的玩家。
    需要权威在线人数时，请结合服务端权威来源（如 RCON / 面板资源接口）另行核实。
    """
    players = await panel.online_players(server_id)
    if not players:
        return "(当前没有检测到在线玩家，或日志里没有玩家进出记录)"
    # 顺手记一条本地采样，供 player_stats 看趋势；记录失败不影响本次返回
    recorded = playerlog.record(server_id, players)
    tail = "\n\n（已记录本次采样，可用 player_stats 查看活跃趋势）" if recorded else ""
    return "当前在线玩家 (" + str(len(players)) + " 人):\n" + "\n".join(f"- {p}" for p in players) + tail


@mcp.tool()
async def batch_list_players(server_ids: list[str]) -> str:
    """批量查看多台服务器当前在线玩家（只读，并发执行）。

    ``server_ids`` 传 ``["*"]`` 表示全部可访问服务器（口径同 broadcast_message）。
    每台基于最近控制台日志推断（**非权威**，口径同 list_players）；
    单台查询失败单独标注、不阻断其余；结果按输入顺序排列。
    """
    if not server_ids:
        return "(未指定任何服务器)"
    target_ids = await _resolve_target_ids(server_ids)
    if not target_ids:
        return "(没有可访问的服务器)"

    async def _one(sid: str) -> str:
        try:
            players = await panel.online_players(sid)
            if not players:
                return f"### {sid}\n(无在线玩家记录)"
            return f"### {sid}（{len(players)} 人）\n" + "\n".join(f"- {p}" for p in players)
        except Exception as e:
            return f"### {sid}\n❌ 查询失败: {e}"

    results = await _gather_bounded(_one, target_ids)
    return "\n\n".join(results)


@mcp.tool()
async def player_stats(server_id: str, days: int = 7) -> str:
    """查看玩家活跃历史：谁最常来、出现在哪几个时段。

    数据来源是**本机记录**（每次 list_players 会记一条采样），纯本地文件
    `~/.pteropilot/playerlog/`，不上传任何数据。采样越频繁，时长估算越接近真实。
    与 list_players 同口径：在线名单本身由控制台日志推断，不是权威在线时长。

    ``days`` 是回看天数（默认 7，最少 1 天）。
    """
    if days < 1:
        return "⛔ days 至少为 1。"
    data = playerlog.stats(server_id, days=days)
    return playerlog.format_stats(server_id, data, days=days)


@mcp.tool()
async def list_files(server_id: str, directory: str = "/") -> str:
    """列出服务器某目录下的文件与子目录（含大小/类型，大小人类可读）。"""
    files = await panel.list_files(server_id, directory)
    if not files:
        return f"(目录 {directory} 为空)"
    lines = []
    for f in files:
        a = _attrs(f)
        name = a.get("name", "?")
        if a.get("is_file") is False:
            lines.append(f"- [DIR ] {name}")
        else:
            lines.append(f"- [FILE] {name} ({_fmt_size(a.get('size'))})")
    return "\n".join(lines)


@mcp.tool()
async def read_file(server_id: str, path: str, max_chars: int = 20000) -> str:
    """读取服务器上的文本文件内容（默认最多返回 20000 字符）。"""
    content = await panel.read_file(server_id, path)
    if len(content) > max_chars:
        return content[:max_chars] + f"\n...[已截断，文件共 {len(content)} 字符]"
    return content


@mcp.tool()
async def read_server_logs(server_id: str, path: str = "logs/latest.log", lines: int = 200) -> str:
    """读取游戏服务器日志的末尾若干行（默认 logs/latest.log 最后 200 行）。

    用于排查崩溃/报错：AI 可据此分析最近一次崩溃原因（如 OutOfMemoryError、
    缺依赖、端口占用等）。lines 上限 5000；内容过长时只保留末尾 20000 字符
    （即最新日志），不影响定位最近的报错。
    """
    lines = max(1, min(int(lines), 5000))
    content = await panel.read_file(server_id, path)
    all_lines = content.splitlines()
    tail = all_lines[-lines:]
    text = "\n".join(tail)
    max_chars = 20000
    truncated = False
    if len(text) > max_chars:
        text = text[-max_chars:]
        truncated = True
    if not tail:
        return f"(文件 {path} 为空)"
    head = f"📄 {path} · 共 {len(all_lines)} 行"
    if len(tail) < len(all_lines):
        head += f"，显示末尾 {len(tail)} 行"
    if truncated:
        head += "（内容过长，仅保留末尾 20000 字符）"
    return head + "\n" + text


@mcp.tool()
async def search_server_logs(
    server_id: str,
    pattern: str,
    path: str = "logs/latest.log",
    limit: int = 50,
) -> str:
    """在服务器日志里搜索关键字（不区分大小写），返回匹配行（带绝对行号）。

    适合排查崩溃/报错：如 ``pattern="error"``、``"OutOfMemoryError"``、``"crash"``。
    只检索日志末尾最多 5000 行；匹配行数超过 ``limit``（上限 200）时只显示前 N 行。
    """
    needle = pattern.strip()
    if not needle:
        return "搜索关键字不能为空。"
    limit = max(1, min(int(limit), 200))
    content = await panel.read_file(server_id, path)
    if not content:
        return f"(文件 {path} 为空)"
    all_lines = content.splitlines()
    offset = max(0, len(all_lines) - 5000)
    window = all_lines[offset:]
    low = needle.lower()
    hits = [(offset + idx + 1, ln) for idx, ln in enumerate(window) if low in ln.lower()]
    if not hits:
        return f"(日志 {path} 末尾 5000 行中没有匹配「{pattern}」的行)"
    head = f"🔍 日志 {path} 中匹配「{pattern}」：共 {len(hits)} 行"
    if len(hits) > limit:
        head += f"（仅显示前 {limit} 行）"
    body = "\n".join(f"{lineno}: {line}" for lineno, line in hits[:limit])
    return head + "\n" + body


# 玩家在线历史解析：基于 Minecraft 控制台日志行（join/leave/lost connection）。
_JOIN_MARK = " joined the game"
_LEAVE_MARKS = (" left the game", " lost connection")


def _parse_player_events(content: str, max_lines: int) -> list[tuple[str, str, str]]:
    """从日志内容解析玩家上线/下线事件，返回 [(时间, 玩家, "joined"|"left"), ...]（按时间序）。"""
    import re

    events: list[tuple[str, str, str]] = []
    lines = content.splitlines()[-max_lines:]
    for line in lines:
        stamp = ""
        if line.startswith("[") and "] " in line:
            stamp = line[1 : line.index("] ")]
        msg = line.split("]: ", 1)[-1] if "]: " in line else line
        kind = None
        name = ""
        if _JOIN_MARK in msg:
            kind = "joined"
            name = msg.split(_JOIN_MARK, 1)[0]
        else:
            for mark in _LEAVE_MARKS:
                if mark in msg:
                    kind = "left"
                    name = msg.split(mark, 1)[0]
                    break
        if kind is None:
            continue
        # 清理名字：去掉 /IP:port 后缀与首尾空白
        name = re.sub(r"\[/.*?\]\s*$", "", name).strip()
        if name:
            events.append((stamp, name, kind))
    return events


@mcp.tool()
async def player_history_stats(
    server_id: str, log_path: str = "logs/latest.log", lines: int = 5000
) -> str:
    """基于服务器控制台日志统计玩家在线历史：当前在线、上线次数、最近活动。

    用于了解服务器活跃度/谁常来。注意：基于最近日志行推断（非权威）——
    早于日志窗口的退出可能被截断，导致"当前在线"高估。
    """
    lines = max(100, min(int(lines), 20000))
    content = await panel.read_file(server_id, log_path)
    events = _parse_player_events(content, lines)
    if not events:
        return f"(最近 {lines} 行日志中没有发现加入/离开记录——服务器可能没人玩，或日志路径不对：{log_path})"

    # 当前在线：join - left 净值 > 0
    balance: dict[str, int] = {}
    joins: dict[str, int] = {}
    for _stamp, name, kind in events:
        balance[name] = balance.get(name, 0) + (1 if kind == "joined" else -1)
        if kind == "joined":
            joins[name] = joins.get(name, 0) + 1
    online = [n for n, b in balance.items() if b > 0]
    online.sort(key=lambda n: (-balance[n], n))

    lines_out = [f"📄 {log_path} · 分析最近 {len(events)} 条加入/离开事件"]
    if online:
        lines_out.append(
            "当前在线（推断）: " + "、".join(f"{n}（净+{balance[n]}）" for n in online)
        )
    else:
        lines_out.append("当前在线（推断）: 无人")
    top = sorted(joins.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    lines_out.append("上线次数 Top: " + "、".join(f"{n} ×{c}" for n, c in top))
    lines_out.append("最近活动:")
    for stamp, name, kind in events[-5:]:
        lines_out.append(f"  {stamp or '??:??:??'} {name} {'加入' if kind == 'joined' else '离开'}")
    lines_out.append("（基于日志推断，非权威；仅供参考活跃度）")
    return "\n".join(lines_out)


@mcp.tool()
async def download_file(server_id: str, path: str) -> str:
    """获取服务器上某文件的一次性下载链接（约 15 分钟有效）。"""
    url = await panel.download_url(server_id, path)
    if not url:
        return "(未获取到下载链接)"
    return f"🔗 下载链接（约 15 分钟有效）:\n{url}"


def _fmt_size(n: Any) -> str:
    """把字节数格式化成人类可读（如 1048576 → '1.0 MB'；非法值 → '?'）。"""
    if not isinstance(n, (int, float)) or n < 0:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    val = float(n)
    while val >= 1024 and i < len(units) - 1:
        val /= 1024
        i += 1
    return f"{val:.1f} {units[i]}"


@mcp.tool()
async def list_backups(server_id: str) -> str:
    """列出服务器备份：名称、UUID、大小（人类可读）、创建日期、完成状态、是否锁定。"""
    backups = await panel.list_backups(server_id)
    if not backups:
        return "(暂无备份)"
    lines = []
    for b in backups:
        a = _attrs(b)
        done = "完成" if a.get("completed_at") else "进行中"
        locked = "已锁定" if a.get("is_locked") else "未锁定"
        created = (a.get("created_at") or "")[:10]
        created_txt = f"创建 {created}" if created else "创建 ?"
        lines.append(
            f"- {a.get('uuid', '?')} | {_name(a)} | {_fmt_size(a.get('bytes'))} | {created_txt} | {done} | {locked}"
        )
    return "\n".join(lines)


@mcp.tool()
async def get_backup_download(server_id: str, backup_id: str) -> str:
    """获取备份的一次性下载链接（只读）。配合 list_backups 输出的 UUID 使用。"""
    url = await panel.backup_download_url(server_id, backup_id)
    if not url:
        return "(未获取到下载链接)"
    return f"🔗 备份下载链接（一次性）:\n{url}"


@mcp.tool()
async def get_server_startup(server_id: str) -> str:
    """查看服务器的启动命令与启动变量（egg 配置，只读）。

    适合排查启动失败（如变量没配、启动命令被改坏），或了解服务器跑的是什么。
    """
    attrs = await panel.get_startup(server_id)
    cmd = attrs.get("startup_command") or attrs.get("startup") or "(无)"
    egg = attrs.get("egg") or attrs.get("egg_id") or ""
    variables = attrs.get("variables") or []
    lines = [f"🧩 启动命令: {cmd}"]
    if egg:
        lines.append(f"Egg: {egg}")
    if variables:
        lines.append(f"启动变量（{len(variables)} 个）：")
        for v in variables[:30]:
            name = v.get("name") or v.get("env_variable") or "?"
            val = v.get("server_value") or v.get("default_value") or "(空)"
            vis = "可见" if v.get("user_viewable", True) else "隐藏"
            edit = "可改" if v.get("user_editable", False) else "只读"
            lines.append(f"- {name} = {val} [{vis}/{edit}]")
        if len(variables) > 30:
            lines.append(f"…共 {len(variables)} 个变量，仅显示前 30 个")
    else:
        lines.append("(无启动变量)")
    return "\n".join(lines)


# ================= 写入 / 危险工具 =================


@mcp.tool()
async def power_signal(server_id: str, signal: str, confirm: bool = False) -> str:
    """控制服务器电源：start/stop/restart/kill。stop/restart/kill 需 confirm=True。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    if signal not in POWER_SIGNALS:
        return f"非法信号 {signal}，可选: {', '.join(POWER_SIGNALS)}"
    if signal in ("stop", "restart", "kill"):
        blocked = _need_confirm(confirm, f"{signal} 电源操作")
        if blocked:
            return blocked
    await panel.power(server_id, signal)
    return f"✅ 已向 {server_id} 发送电源信号: {signal}"


async def _power_data(
    server_ids: list[str],
    signal: str,
    confirm: bool = False,
) -> dict:
    """批量电源的结构化结果（MCP 工具与 CLI ``--power`` 共用）。

    ``status``：``ok`` 已执行 / ``refused`` 被拦截（只读、非法信号、缺 confirm、无目标）。
    被拦截时 ``reason`` 说明原因，``results`` 为空。
    """
    data: dict = {
        "signal": signal,
        "status": "ok",
        "reason": None,
        "scope": None,
        "total": 0,
        "ok": 0,
        "failed": 0,
        "results": [],
    }

    denied = _denied_if_readonly()
    if denied:
        data.update(status="refused", reason="readonly")
        return data
    if not server_ids:
        data.update(status="refused", reason="no_targets")
        return data
    if signal not in POWER_SIGNALS:
        data.update(status="refused", reason="bad_signal")
        return data

    target_ids = await _resolve_target_ids(server_ids)
    if not target_ids:
        data.update(status="refused", reason="no_servers")
        return data

    all_servers = server_ids == ["*"]
    data["scope"] = "all" if all_servers else "explicit"

    # 全量操作任何信号都要确认（防止误启动全部服务器）；
    # 显式列表时只有停服类信号要确认，start 免确认。
    if signal in ("stop", "restart", "kill") or all_servers:
        if not confirm:
            data.update(status="refused", reason="need_confirm")
            return data

    async def _apply(sid: str) -> dict:
        try:
            await panel.power(sid, signal)
            return {"id": sid, "ok": True, "error": None}
        except Exception as exc:  # noqa: BLE001 - 单台失败要如实带回来
            return {"id": sid, "ok": False, "error": type(exc).__name__}

    results = list(await _gather_bounded(_apply, target_ids))
    data["results"] = results
    data["total"] = len(results)
    data["ok"] = sum(1 for r in results if r["ok"])
    data["failed"] = data["total"] - data["ok"]
    return data


def _format_power(data: dict) -> str:
    """把结构化电源结果渲染成人看的文本。"""
    reason_text = {
        "readonly": "⛔ 当前为只读模式（PANEL_READONLY=1），已拒绝该操作。",
        "no_targets": "(未指定任何服务器)",
        "bad_signal": f"非法信号 {(data.get('signal') or '')}，可选: {', '.join(POWER_SIGNALS)}",
        "no_servers": "(没有可访问的服务器)",
        "need_confirm": "⚠️ 该电源操作属于高危操作，已拦截。确认无误后请带 confirm=True 重新调用。",
    }
    if data["status"] != "ok":
        return reason_text.get(data["reason"] or "", "(未执行)")

    # 保持和重构前完全一致的文案（既有测试锁住了这个格式）
    header = (
        f"批量 {data['signal']}：共 {data['total']} 台，"
        f"成功 {data['ok']} 台、失败 {data['failed']} 台："
    )
    lines = [
        f"- {r['id']} | " + (f"✅ 已发送 {data['signal']}" if r["ok"] else f"❌ 失败（{r['error']}）")
        for r in data["results"]
    ]
    return header + ("\n" + "\n".join(lines) if lines else "")


@mcp.tool()
async def batch_power(server_ids: list[str], signal: str, confirm: bool = False) -> str:
    """批量控制一组服务器的电源（Roadmap「批量开服」写路径收尾）。

    面板 API 无原生批量电源端点（Pterodactyl/Pelican 都只有单台
    `/api/client/servers/{id}/power`），故退化为「逐台循环调用单台 power + 整体二次确认」：

    - 只读模式下整体拒绝，不发出任何请求；
    - signal 非法则整体拒绝；
    - ``server_ids=["*"]`` 表示全部可访问服务器——**写操作作用于全部服务器，任何
      信号都必须 confirm=True**（即使是 start，防止误启动全部服务器）；
    - 显式列表时 stop/restart/kill 需整体 confirm=True（一次确认覆盖整批，start 免确认）；
    - 逐台 apply，单台失败（超时/无权限/面板报错）单独标注、不阻断其余服务器。

    CLI 里 ``pteropilot --power restart --servers smp --confirm`` 走同一份逻辑。
    """
    return _format_power(await _power_data(server_ids, signal, confirm))


# 广播时视为高危的指令（会关服 / 改权限 / 踢人 / 关白名单）。
# 只做前缀匹配，宁可多拦一次让用户补 confirm，也不要让一条 op 指令一次发给所有服。
_BROADCAST_DANGEROUS = (
    "stop",
    "kill",
    "restart",
    "op ",
    "deop ",
    "ban ",
    "ban-ip ",
    "pardon ",
    "whitelist off",
    "difficulty peaceful",
)


def _is_dangerous_broadcast(command: str) -> bool:
    """判断一条待广播的指令是否属于高危（前缀匹配，忽略大小写与首尾空白）。"""
    c = command.strip().lower()
    if not c:
        return False
    return any(c == d.strip() or c.startswith(d) for d in _BROADCAST_DANGEROUS)


async def _broadcast_data(
    command: str,
    server_ids: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """广播的结构化结果（MCP 工具与 CLI ``--json`` 共用，避免两套逻辑走偏）。

    返回的 dict 一定包含 ``status`` 字段：
      ``ok`` 已发送 / ``refused`` 被拦截（只读、缺 confirm、空指令、无目标）
    被拦截时 ``reason`` 说明原因，``results`` 为空。
    """
    data: dict = {
        "command": (command or "").strip(),
        "status": "ok",
        "reason": None,
        "scope": None,
        "dangerous": False,
        "total": 0,
        "ok": 0,
        "failed": 0,
        "results": [],
    }

    denied = _denied_if_readonly()
    if denied:
        data.update(status="refused", reason="readonly")
        return data

    cmd = data["command"]
    if not cmd:
        data.update(status="refused", reason="empty_command")
        return data

    # 1) 解析目标
    if server_ids is None:
        servers = await panel.client_servers()
        if not servers:
            data.update(status="refused", reason="no_servers")
            return data

        async def _state(sid: str) -> str:
            try:
                attrs = await panel.resources(sid)
                return str(attrs.get("current_state") or "?")
            except Exception:
                return "?"

        pairs = []
        for s in servers:
            a = _attrs(s)
            sid = a.get("identifier") or a.get("id")
            if sid:
                pairs.append((str(sid), a.get("name") or str(sid)))
        states = await _gather_bounded(_state, [sid for sid, _ in pairs])
        target_ids = [sid for (sid, _n), st in zip(pairs, states) if st == "running"]
        if not target_ids:
            data.update(status="refused", reason="no_running")
            return data
        data["scope"] = "running"
    else:
        if not server_ids:
            data.update(status="refused", reason="no_targets")
            return data
        target_ids = await _resolve_target_ids(server_ids)
        if not target_ids:
            data.update(status="refused", reason="no_targets")
            return data
        data["scope"] = "all" if server_ids == ["*"] else "explicit"

    # 2) 高危指令二次确认
    data["dangerous"] = _is_dangerous_broadcast(cmd)
    if data["dangerous"] and not confirm:
        data.update(status="refused", reason="need_confirm")
        return data

    # 3) 并发发送
    async def _apply(sid: str) -> dict:
        try:
            await panel.send_command(sid, cmd)
            return {"id": sid, "ok": True, "error": None}
        except Exception as exc:  # noqa: BLE001 - 单台失败要如实带回来
            return {"id": sid, "ok": False, "error": type(exc).__name__}

    results = list(await _gather_bounded(_apply, target_ids))
    data["results"] = results
    data["total"] = len(results)
    data["ok"] = sum(1 for r in results if r["ok"])
    data["failed"] = data["total"] - data["ok"]
    return data


def _format_broadcast(data: dict) -> str:
    """把结构化结果渲染成人看的文本（MCP 工具返回用）。"""
    reason_text = {
        "readonly": "⛔ 当前为只读模式（PANEL_READONLY=1），已拒绝该操作。",
        "empty_command": "(指令为空，什么都没发)",
        "no_servers": "(没有可访问的服务器)",
        "no_running": "(当前没有 running 的服务器，未发送)",
        "no_targets": "(未指定任何服务器)",
        "need_confirm": "⚠️ 广播高危指令属于高危操作，已拦截。确认无误后请带 confirm=True 重新调用。",
    }
    if data["status"] != "ok":
        return reason_text.get(data["reason"] or "", "(未执行)")

    scope_text = {
        "running": "所有 running 服务器",
        "all": "全部服务器",
        "explicit": f"指定 {data['total']} 台",
    }[data["scope"] or "explicit"]
    header = (
        f"广播「{data['command']}」→ {scope_text}："
        f"共 {data['total']} 台，成功 {data['ok']}、失败 {data['failed']}"
    )
    lines = [
        f"- {r['id']} | " + ("✅ 已发送" if r["ok"] else f"❌ 失败（{r['error']}）")
        for r in data["results"]
    ]
    return header + ("\n" + "\n".join(lines) if lines else "")


@mcp.tool()
async def broadcast_command(
    command: str,
    server_ids: list[str] | None = None,
    confirm: bool = False,
) -> str:
    """给多台服务器**同时发送同一条控制台指令**（广播）。

    典型场景：维护前提前通知（``say 服务器 5 分钟后重启``）、统一刷白名单、
    统一改游戏规则。面板只有单台的 ``/command`` 端点，这里退化为「逐台并发调用」。

    - ``server_ids`` 省略时只发给**当前 running** 的服务器（停机的发了也收不到）；
      传 ``["*"]`` 表示所有可访问服务器（含停机的，会在结果里标出没发成功）；
    - 高危指令（``stop`` / ``kill`` / ``op`` / ``ban`` / ``whitelist off`` 等）必须
      ``confirm=True``——一次误发会同时作用于所有服务器；
    - 只读模式下整体拒绝，一个请求都不发出；
    - 单台失败（无权限/超时/面板报错）单独标注，不阻断其余服务器。

    CLI 里 ``pteropilot --broadcast "..." --json`` 输出的是同一份结构化结果。
    """
    return _format_broadcast(await _broadcast_data(command, server_ids, confirm))


@mcp.tool()
async def batch_backup_status() -> str:
    """批量备份概览（只读）：遍历我可访问的服务器，逐台列出备份数量、总大小与最近备份时间。

    适合快速回答「哪些服务器没有最近备份」。单台查询失败单独标注、不阻断整体；
    多台并发查询。
    """
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"

    async def _one(s: dict) -> str:
        attrs = s.get("attributes") or {}
        sid = attrs.get("identifier") or attrs.get("id") or "?"
        name = attrs.get("name") or sid
        try:
            backups = await panel.list_backups(sid)
            count = len(backups)
            total = 0
            newest = ""
            for b in backups:
                a = b.get("attributes") or {}
                try:
                    total += int(a.get("bytes") or 0)
                except (TypeError, ValueError):
                    pass
                created = a.get("created_at") or ""
                if created and (not newest or created > newest):
                    newest = created
            newest_txt = newest.split("T")[0] if newest else "无"
            return f"- {sid} | {name} | {count} 个备份（{_fmt_size(total)}）| 最近: {newest_txt}"
        except Exception:
            return f"- {sid} | {name} | ❌ 查询失败"

    results = await _gather_bounded(_one, servers)
    failed = sum(1 for line in results if "查询失败" in line)
    header = f"批量备份概览：共 {len(servers)} 台，失败 {failed} 台："
    return header + "\n" + "\n".join(results)


@mcp.tool()
async def batch_backup(server_ids: list[str], confirm: bool = False) -> str:
    """批量创建备份（写操作，需 confirm=True）。

    面板 API 无原生批量备份端点，退化为「逐台循环调用单台 create_backup + 整体二次确认」：
    - 只读模式下整体拒绝；
    - 未指定服务器或 confirm=False 时整体拒绝；
    - ``server_ids=["*"]`` 表示全部可访问服务器（写操作作用于全部，必须 confirm=True）；
    - 逐台创建，单台失败（超时/无权限/面板报错）单独标注、不阻断其余服务器。
    """
    denied = _denied_if_readonly()
    if denied:
        return denied
    if not server_ids:
        return "(未指定任何服务器)"
    target_ids = await _resolve_target_ids(server_ids)
    if not target_ids:
        return "(没有可访问的服务器)"
    blocked = _need_confirm(confirm, f"批量创建备份（{len(target_ids)} 台服务器）")
    if blocked:
        return blocked

    ok = 0
    failed = 0
    lines: list[str] = []

    async def _create(sid: str) -> str:
        try:
            backup = await panel.create_backup(sid)
            return f"- {sid} | ✅ 备份任务已创建（{backup.get('uuid', '?')}）"
        except Exception:
            return f"- {sid} | ❌ 创建失败（无权限/超时/面板报错）"

    # 并发执行（gather 保序）：面板无批量备份端点，并发调用缩短整批耗时。
    for line in await _gather_bounded(_create, target_ids):
        lines.append(line)
        if "✅" in line:
            ok += 1
        else:
            failed += 1

    header = f"批量创建备份：共 {len(target_ids)} 台，成功 {ok} 台、失败 {failed} 台："
    return header + "\n" + "\n".join(lines)


@mcp.tool()
async def get_console_log(server_id: str, max_chars: int = 8000) -> str:
    """读取服务器控制台最近的日志输出（用于查看指令回馈、报错、启动引导）。"""
    log = await panel.console_log(server_id)
    if len(log) > max_chars:
        return log[-max_chars:] + f"\n...[已截断，仅显示末尾 {max_chars} 字符]"
    return log


@mcp.tool()
async def batch_get_console_log(server_ids: list[str], max_chars: int = 3000) -> str:
    """批量读取多台服务器的控制台最近日志（只读，并发执行）。

    ``server_ids`` 传 ``["*"]`` 表示全部可访问服务器（口径同 broadcast_message）。
    适合「多台服务器同时出问题」的场景：一次拿多台的控制台输出对比。
    单台读取失败单独标注、不阻断其余；每台输出按输入顺序排列。
    """
    if not server_ids:
        return "(未指定任何服务器)"
    target_ids = await _resolve_target_ids(server_ids)
    if not target_ids:
        return "(没有可访问的服务器)"

    async def _one(sid: str) -> str:
        try:
            log = await panel.console_log(sid)
            if len(log) > max_chars:
                log = log[-max_chars:] + f"\n...[已截断，仅显示末尾 {max_chars} 字符]"
            return f"### {sid}\n{log}"
        except Exception as e:
            return f"### {sid}\n❌ 读取失败: {e}"

    results = await _gather_bounded(_one, target_ids)
    return "\n\n".join(results)


@mcp.tool()
async def send_command(server_id: str, command: str) -> str:
    """向服务器控制台发送一条指令。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    await panel.send_command(server_id, command)
    return f"✅ 指令已发送: {command}"


@mcp.tool()
async def broadcast_message(server_ids: list[str], message: str, confirm: bool = False) -> str:
    """【批量】向多台服务器控制台广播同一条消息（如停机维护公告）。

    实际发送 ``say <message>``（Minecraft 聊天广播）。``server_ids`` 传
    ``["*"]`` 表示全部可访问服务器。会同时写入多台服务器控制台，属于高危
    操作，必须 ``confirm=True`` 才会执行；逐台失败不阻断其余服务器。
    """
    denied = _denied_if_readonly()
    if denied:
        return denied
    if not confirm:
        return "⚠️ 广播会同时写入多台服务器控制台，属于高危操作，已拦截。确认无误后请带 confirm=True 重新调用。"
    msg = message.strip()
    if not msg:
        return "消息不能为空。"
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"
    targets: list[tuple[str, str]] = []
    for s in servers:
        a = _attrs(s)
        sid = a.get("identifier") or a.get("id")
        if not sid:
            continue
        if server_ids == ["*"] or sid in server_ids:
            targets.append((sid, _name(a)))
    if not targets:
        return "(指定的服务器都不存在或无权限)"
    ok, fail = [], []

    async def _bcast(sid: str, name: str) -> tuple[str | None, str | None]:
        try:
            await panel.send_command(sid, f"say {msg}")
            return name, None
        except Exception as e:  # 单台失败不阻断
            return None, f"{name}: {e}"

    # 并发广播（保序，但同时只打 MAX_CONCURRENCY 台）：
    # 多台服务器同时发公告能显著缩短整批耗时，但一次性几十条连接会被面板限流。
    for done_name, done_fail in await _gather_bounded(
        lambda t: _bcast(t[0], t[1]), targets
    ):
        if done_fail is None:
            ok.append(done_name)
        else:
            fail.append(done_fail)
    lines = [f"✅ 已广播到 {len(ok)} 台：{'、'.join(ok)}"]
    if fail:
        lines.append(f"❌ 失败 {len(fail)} 台：{'；'.join(fail)}")
    return "\n".join(lines)


def _spawn_after(delay: float, coro_factory: Any) -> None:
    """在当前事件循环上安排后台任务：delay 秒后执行 coro_factory()。"""

    async def _runner() -> None:
        await asyncio.sleep(delay)
        await coro_factory()

    asyncio.get_running_loop().create_task(_runner())


@mcp.tool()
async def schedule_restart(
    server_id: str, minutes: int = 5, message: str | None = None, confirm: bool = False
) -> str:
    """安排服务器在 N 分钟后自动重启（先广播公告，到点自动 restart）。

    - 立即向服务器控制台广播公告（say "服务器将在 N 分钟后自动重启..."，
      可自定义 message）；
    - N 分钟后自动执行 restart（与手动 power restart 一致）；
    - 高危写操作，必须 ``confirm=True``；minutes 范围 1-120，自动钳制；
    - ⚠️ 计划随本 MCP 进程存活：进程退出/被终止则计划取消（无持久化），
      适合"提前 5 分钟提醒 + 定时重启"这类运维场景。
    """
    denied = _denied_if_readonly()
    if denied:
        return denied
    minutes = max(1, min(int(minutes), 120))
    blocked = _need_confirm(confirm, f"安排 {server_id} 在 {minutes} 分钟后重启")
    if blocked:
        return blocked

    text = (message or f"服务器将在 {minutes} 分钟后自动重启，请及时保存并退出。").strip()
    if not text:
        return "公告消息不能为空。"
    try:
        await panel.send_command(server_id, f"say {text}")
    except Exception as e:
        return f"❌ 公告发送失败（计划已取消）：{e}"

    async def _do_restart() -> None:
        try:
            await panel.power(server_id, "restart")
        except Exception:
            pass  # 到点重启失败：静默（AI 可稍后用 power_signal 检查）

    _spawn_after(minutes * 60, _do_restart)
    return (
        f"✅ 已安排 {server_id} 在 {minutes} 分钟后自动重启，公告已广播。"
        f"⚠️ 计划随本进程存活，进程退出则取消。"
    )


@mcp.tool()
async def write_file(server_id: str, path: str, content: str, confirm: bool = False) -> str:
    """写入文本文件；若文件已存在将被覆盖（需 confirm=True）。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    blocked = _need_confirm(confirm, f"写入/覆盖文件 {path}")
    if blocked:
        return blocked
    await panel.write_file(server_id, path, content)
    return f"✅ 已写入文件: {path}（{len(content)} 字符）"


@mcp.tool()
async def delete_files(server_id: str, paths: list[str], confirm: bool = False) -> str:
    """删除一个或多个文件/目录（路径数组）。高危，需 confirm=True。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    blocked = _need_confirm(confirm, f"删除 {len(paths)} 个文件/目录")
    if blocked:
        return blocked
    await panel.delete_files(server_id, root="/", paths=paths)
    return f"✅ 已删除 {len(paths)} 项: {', '.join(paths)}"


@mcp.tool()
async def create_folder(server_id: str, directory: str, name: str) -> str:
    """在服务器指定目录下新建一个文件夹。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    await panel.create_folder(server_id, directory, name)
    return f"✅ 已创建文件夹: {directory.rstrip('/')}/{name}"


@mcp.tool()
async def rename_file(
    server_id: str, directory: str, old_name: str, new_name: str, confirm: bool = False
) -> str:
    """重命名/移动服务器上的文件或文件夹（覆盖同名目标需 confirm=True）。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    blocked = _need_confirm(confirm, "重命名/移动文件")
    if blocked:
        return blocked
    await panel.rename_file(server_id, directory, old_name, new_name)
    return f"✅ 已重命名: {old_name} → {new_name}"


@mcp.tool()
async def upload_file(
    server_id: str, directory: str, filename: str, content_b64: str, confirm: bool = False
) -> str:
    """上传文件（content_b64 为 base64 编码的文件内容；覆盖同名文件需 confirm=True）。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    blocked = _need_confirm(confirm, "上传/覆盖文件")
    if blocked:
        return blocked
    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception:
        return "⚠️ content_b64 不是合法的 base64 内容。"
    await panel.upload_file(server_id, directory, filename, content)
    return f"✅ 已上传: {filename}（{len(content)} 字节）到 {directory}"


@mcp.tool()
async def create_backup(server_id: str) -> str:
    """为服务器创建一次备份。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    backup = await panel.create_backup(server_id)
    return f"✅ 备份任务已创建: {backup.get('uuid', '?')}"


@mcp.tool()
async def restore_backup(server_id: str, backup_id: str, confirm: bool = False) -> str:
    """用指定备份恢复服务器（回滚当前文件）。高危，需 confirm=True。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    blocked = _need_confirm(confirm, f"恢复备份 {backup_id}")
    if blocked:
        return blocked
    await panel.restore_backup(server_id, backup_id)
    return f"✅ 已触发恢复: 备份 {backup_id}"


@mcp.tool()
async def delete_backup(server_id: str, backup_id: str, confirm: bool = False) -> str:
    """删除一次备份。已锁定的备份需先在面板解锁。高危，需 confirm=True。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    blocked = _need_confirm(confirm, f"删除备份 {backup_id}")
    if blocked:
        return blocked
    await panel.delete_backup(server_id, backup_id)
    return f"✅ 已删除备份: {backup_id}"


def _tool_count() -> int:
    """已注册的工具数量：用于 --version 输出，让用户一眼看出装的是不是最新版。"""
    try:
        manager = getattr(mcp, "_tool_manager", None)
        tools = getattr(manager, "_tools", None) if manager is not None else None
        if isinstance(tools, dict) and tools:
            return len(tools)
    except Exception:  # noqa: BLE001 - 内部结构变了也不能让 --version 崩
        pass
    try:
        import re
        from pathlib import Path as _Path

        src = _Path(__file__).read_text(encoding="utf-8")
        return len(re.findall(r"@mcp\.tool\(\)", src))
    except Exception:  # noqa: BLE001
        return 0


def _print_version() -> None:
    count = _tool_count()
    tail = f"（{count} 个 MCP 工具）" if count else ""
    print(f"pteropilot v{__version__}{tail}")
    if count and count < 30:
        print(
            "提示：工具数偏少，你可能装到了旧版本（PyPI 发布可能滞后）。"
            "请对照 README 里的工具清单，或从 GitHub Releases 安装最新的 .whl。",
            file=sys.stderr,
        )


def _version_tuple(v: str) -> tuple[int, ...]:
    """把 ``0.2.33`` 这样的版本号拆成可比较的元组。

    不能用字符串比较：``"0.2.9" > "0.2.33"`` 是 True，会误判成「不需要升级」。
    非数字段（如 ``rc1``）一律截断，只比前面的纯数字部分。
    """
    parts: list[int] = []
    for seg in v.strip().lstrip("vV").split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            break
        parts.append(int(num))
    return tuple(parts)


def _latest_release_version(timeout: float = 5.0) -> str | None:
    """查 GitHub 最新 release 的版本号；失败返回 None（调用方不该因此报错）。

    不查 PyPI 而查 GitHub Releases：PyPI 上的包可能长期滞后（本项目就卡过
    33 个版本），拿它当「最新」会得出相反结论。
    """
    import urllib.request

    url = "https://api.github.com/repos/telson-dev/pteropilot/releases/latest"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"pteropilot/{__version__}",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = str(data.get("tag_name") or "").strip()
        return tag.lstrip("vV") or None
    except Exception:  # noqa: BLE001 - 网络/限流/被墙都只意味着「不知道」
        return None


def _run_doctor(skip_network: bool = False) -> int:
    """环境自检：Python 版本、配置、密钥前缀、面板连通、告警配置。

    用户报「连不上面板/工具没反应」时，让他跑 `pteropilot --doctor` 并把输出贴过来，
    比自己猜环境快得多。输出**不含任何密钥**（只显示前缀与规范化后的地址）。
    """
    import platform as _platform

    # 与 --version 用同一行措辞：用户会拿两处输出互相核对，
    # 「45 个 MCP 工具」和「45 个工具」两种说法只会让人以为装了两个版本。
    _print_version()
    print(f"Python {_platform.python_version()}　{_platform.system()} {_platform.machine()}")
    problems: list[str] = []
    hints: list[str] = []

    # 1) 配置
    raw_url = os.environ.get("PANEL_URL", "")
    api_key = os.environ.get("PANEL_API_KEY", "")
    app_key = os.environ.get("PANEL_APP_KEY", "")
    if not raw_url:
        problems.append("未设置 PANEL_URL")
        hints.append("在 MCP 客户端配置的 env 里填面板根地址，如 https://panel.example.com")
    else:
        norm = config.panel_url
        suffix_note = ""
        if raw_url.strip().rstrip("/") != norm:
            suffix_note = f"（已自动规范化：{raw_url} → {norm}）"
        print(f"PANEL_URL: {norm} {suffix_note}".rstrip())

    if not api_key:
        problems.append("未设置 PANEL_API_KEY")
        hints.append("面板「账户 → API 凭证」生成客户端密钥（ptlc_ / pacc_）")
    else:
        prefix = api_key.split("_")[0] + "_" if "_" in api_key[:6] else "（前缀无法识别）"
        print(f"PANEL_API_KEY: {prefix}…（共 {len(api_key)} 字符）")
    if app_key:
        print(f"PANEL_APP_KEY: {app_key[:5]}…（共 {len(app_key)} 字符）")
    else:
        hints.append("未设置 PANEL_APP_KEY：list_nodes / list_users 等应用级工具会 403（可选）")

    # 2) 密钥角色（复用启动时的诊断）
    for w in key_role_warnings(config):
        hints.append(w)

    # 3) 只读模式
    if config.readonly:
        print("PANEL_READONLY: 开启（所有写操作会被拒绝）")

    # 3.5) 版本新鲜度：装得比最新 release 旧时明确告知怎么升
    #      放这里而不是最后，因为配置不全时下面会提前 return，而「装了旧版」
    #      恰恰常被误判成「工具少/功能不对」，越早看到越好。
    if not skip_network:
        latest = _latest_release_version()
        if latest is None:
            print(f"版本：v{__version__}（查不到最新版，跳过对比）")
        elif _version_tuple(latest) > _version_tuple(__version__):
            print(f"版本：v{__version__} → 最新 v{latest}（落后）")
            hints.append(
                f"装的是旧版：最新 v{latest}，当前 v{__version__}。升级命令：\n"
                f"      uv tool install --force "
                f"https://github.com/telson-dev/pteropilot/releases/latest/download/pteropilot-{latest}-py3-none-any.whl"
            )
        else:
            print(f"版本：v{__version__}（已是最新）")
    # 4) 面板连通
    if problems:
        print()
        print("结论：配置不完整，先补齐下面的项：")
        for x in problems:
            print(f"  ✗ {x}")
        for h in hints:
            print(f"  · {h}")
        return 1

    if skip_network:
        print()
        print("（已跳过网络检查 --no-network）")
        for h in hints:
            print(f"  · {h}")
        return 0

    print()
    print("连接面板中…")
    try:
        client = PanelClient(config, timeout=15.0)

        async def _check() -> tuple[int, list[str]]:
            try:
                servers = await client.client_servers()
                names = []
                for s in servers[:5]:
                    a = (s or {}).get("attributes") or {}
                    names.append(a.get("name") or a.get("identifier") or "?")
                return len(servers), names
            finally:
                await client.aclose()

        count, names = asyncio.run(_check())
        print(f"✅ 连接成功：可访问 {count} 台服务器" + (f"（{('、'.join(names))}）" if names else ""))
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(f"❌ 连接失败：{type(exc).__name__}: {redact(str(exc))}")
        if status == 401:
            problems.append("API Key 被面板拒绝（401）")
            hints.append("密钥无效或已撤销，去「账户 → API 凭证」重新生成")
        elif status == 403:
            problems.append("密钥权限不足（403）")
            hints.append("核心工具走客户端 API，请用客户端密钥 ptlc_ / pacc_ 作为 PANEL_API_KEY")
        elif status == 404:
            problems.append("地址 404")
            hints.append("确认填的是面板根地址（不要带 /api、/api/client）")
        else:
            problems.append("无法连接面板")
            hints.append("检查面板是否在线、网络能否访问该域名（可先用浏览器打开面板）")

    # 5) 告警相关（可选功能）
    import shutil

    if os.environ.get("WEBHOOK_URL") or os.environ.get("SMTP_HOST"):
        print("告警渠道：已配置（跑 `pteropilot-alerts --test` 可验证能否发出去）")
    else:
        print("告警渠道：未配置（可选；装好后用 `pteropilot-alerts --test` 验证）")
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        names = {ep.name for ep in (eps.select(group="console_scripts") if hasattr(eps, "select") else eps.get("console_scripts", []))}
        if "pteropilot-alerts" not in names and not shutil.which("pteropilot-alerts"):
            hints.append("未找到 pteropilot-alerts 命令（pip/uv 安装后重启终端）")
    except Exception:  # noqa: BLE001
        if not shutil.which("pteropilot-alerts"):
            hints.append("未找到 pteropilot-alerts 命令（pip/uv 安装后重启终端）")

    print()
    if problems:
        print("结论：有问题需要处理：")
        for x in problems:
            print(f"  ✗ {x}")
    else:
        print("结论：环境正常。")
    for h in hints:
        print(f"  · {h}")
    return 1 if problems else 0


# 计划执行：--in / --at 的上限。再长就该交给 cron/systemd，而不是让进程干等。
_MAX_WAIT_SECONDS = 24 * 3600


def _parse_duration(raw: str) -> float:
    """``5m`` / ``300s`` / ``2h`` / ``1d`` / ``90`` → 秒。

    单位可省略（当秒）；只接受一个单位后缀，不玩 ``1h30m`` 这种组合，
    免得算错半天才发现。
    """
    s = (raw or "").strip().lower()
    if not s:
        raise ValueError("空的时间间隔")
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = s[-1]
    if unit in mult:
        num_part, m = s[:-1], mult[unit]
    else:
        num_part, m = s, 1
    try:
        n = float(num_part)
    except ValueError as exc:
        raise ValueError(f"看不懂的时间间隔: {raw}") from exc
    # float() 认得 "nan" / "inf" 这类字面量：nan 会让 time.sleep() 抛
    # ValueError（未被捕获就糊一屏堆栈），inf 虽然会被上限拦住，但报「要等
    # inf 小时」很难看。两者都当作用户输错。
    if not math.isfinite(n):
        raise ValueError(f"看不懂的时间间隔: {raw}")
    if n < 0:
        raise ValueError("时间间隔不能为负")
    return n * m


def _seconds_until_clock(raw: str, now: datetime | None = None) -> float:
    """``03:00`` / ``3:00`` / ``03:00:30`` → 距离**下一个**该时刻的秒数。

    已经过了今天这个点就顺延到明天；返回 0 表示正好到点（极少见）。
    """
    s = (raw or "").strip()
    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"看不懂的时刻: {raw}（要 HH:MM 或 HH:MM:SS）")
    try:
        nums = [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"看不懂的时刻: {raw}") from exc
    hh, mm = nums[0], nums[1]
    ss = nums[2] if len(parts) == 3 else 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise ValueError(f"时刻超出范围: {raw}")

    base = now or datetime.now()
    target = base.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if target <= base:
        target += timedelta(days=1)
    return (target - base).total_seconds()


def _scheduled_delay(argv: list[str]) -> tuple[float | None, str | None]:
    """从 argv 里取 ``--in`` / ``--at``，返回 (等待秒数, 错误说明)。

    两个都给算用法错误（语义冲突，不猜用户想哪个）。
    """
    has_in = "--in" in argv
    has_at = "--at" in argv
    if has_in and has_at:
        return None, "--in 和 --at 只能给一个"
    try:
        if has_in:
            j = argv.index("--in")
            raw = argv[j + 1] if j + 1 < len(argv) else ""
            if not raw or raw.startswith("--"):
                return None, "--in 后面要跟时间间隔（如 5m / 300s / 2h）"
            return _parse_duration(raw), None
        if has_at:
            j = argv.index("--at")
            raw = argv[j + 1] if j + 1 < len(argv) else ""
            if not raw or raw.startswith("--"):
                return None, "--at 后面要跟时刻（如 03:00）"
            return _seconds_until_clock(raw), None
    except ValueError as exc:
        return None, str(exc)
    return None, None


def _wait_until(delay: float, fmt: str) -> bool:
    """等到点再执行；返回 True 表示该继续执行，False 表示被取消。

    刻意用同步 ``time.sleep``：CLI 是一次性进程，没有别的事要干，
    异步等待只会多一层嵌套。Ctrl+C 能中断 sleep，会如实返回取消。
    """
    if delay <= 0:
        return True
    if delay > _MAX_WAIT_SECONDS:
        hours = delay / 3600
        print(
            f"✗ 要等 {hours:.1f} 小时（上限 24 小时）。这么长请交给 cron / systemd，"
            "而不是让进程干等。",
            file=sys.stderr,
        )
        return False
    when = datetime.now() + timedelta(seconds=delay)
    print(
        f"⏳ 将在 {when.strftime('%Y-%m-%d %H:%M:%S')} 执行"
        f"（{delay:.0f} 秒后，Ctrl+C 取消）: {fmt}",
        file=sys.stderr,
    )
    try:
        time.sleep(delay)
    except KeyboardInterrupt:
        print("\n已取消（等待期间收到 Ctrl+C）。", file=sys.stderr)
        return False
    return True


_BROADCAST_USAGE = (
    '用法: pteropilot --broadcast "say 服务器 5 分钟后重启" '
    "[--servers a,b] [--confirm] [--json]"
)


def _run_broadcast(argv: list[str]) -> int:
    """``pteropilot --broadcast "指令"``：不启动 MCP server，直接广播一次后退出。

    给运维脚本/计划任务用——服务器维护前在 crontab 里跑一条就够了，
    不必先起 MCP 客户端。高危指令同样要 ``--confirm``。
    """
    try:
        i = argv.index("--broadcast")
        command = argv[i + 1] if i + 1 < len(argv) else ""
    except ValueError:
        command = ""
    # 下一个 token 是另一个选项 → 说明没给指令
    if not command or command.startswith("--"):
        print(_BROADCAST_USAGE, file=sys.stderr)
        return 2

    ids: list[str] | None = None
    if "--servers" in argv:
        j = argv.index("--servers")
        if j + 1 < len(argv) and not argv[j + 1].startswith("--"):
            ids = [x.strip() for x in argv[j + 1].split(",") if x.strip()]
            if not ids:
                ids = None

    confirm = "--confirm" in argv
    as_json = "--json" in argv

    # --in / --at：等到点再发（省得为了「5 分钟后再发」写一圈 shell sleep）
    delay, err = _scheduled_delay(argv)
    if err:
        print(f"✗ {err}", file=sys.stderr)
        return 2
    if delay is not None and not _wait_until(delay, f'broadcast "{command}"'):
        return 4

    data = asyncio.run(_broadcast_data(command, ids, confirm))

    if as_json:
        # 结构化输出：给脚本用（jq / 计划任务里判断退出码 + 解析结果）
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(_format_broadcast(data))

    # 被拦截（只读 / 缺 confirm / 空指令 / 无目标）也算非零，方便脚本判断
    if data["status"] == "refused":
        return 3
    return 0 if data["failed"] == 0 else 1


_POWER_USAGE = (
    '用法: pteropilot --power <start|stop|restart|kill> --servers a,b '
    '[--confirm] [--json] [--in 5m | --at 03:00]'
)


def _run_power(argv: list[str]) -> int:
    """``pteropilot --power restart``：不启动 MCP server，批量发电源信号后退出。

    与 ``--broadcast`` 对称，给 cron / 计划任务用。stop/restart/kill 以及
    ``--servers "*"``（全部服务器）必须 ``--confirm``。
    """
    try:
        i = argv.index("--power")
        signal = argv[i + 1] if i + 1 < len(argv) else ""
    except ValueError:
        signal = ""
    if not signal or signal.startswith("--"):
        print(_POWER_USAGE, file=sys.stderr)
        return 2

    ids: list[str] | None = None
    if "--servers" in argv:
        j = argv.index("--servers")
        if j + 1 < len(argv) and not argv[j + 1].startswith("--"):
            raw = [x.strip() for x in argv[j + 1].split(",") if x.strip()]
            ids = raw or None
    if ids is None:
        # 电源是写操作，不给目标时不像广播那样猜范围，直接要求显式指定
        print(
            _POWER_USAGE + '\n（--servers 必填；要作用于全部服务器请写 --servers "*"）',
            file=sys.stderr,
        )
        return 2

    confirm = "--confirm" in argv
    as_json = "--json" in argv

    delay, err = _scheduled_delay(argv)
    if err:
        print(f"✗ {err}", file=sys.stderr)
        return 2
    if delay is not None and not _wait_until(delay, f"power {signal} -> {','.join(ids)}"):
        return 4

    data = asyncio.run(_power_data(ids, signal, confirm))

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(_format_power(data))

    if data["status"] == "refused":
        return 3
    return 0 if data["failed"] == 0 else 1


_STATUS_USAGE = "用法: pteropilot --status [服务器标识] [--json]"


def _fmt_mb(x: Any) -> str:
    """字节 → MiB；非数值给 '?'。"""
    return f"{x / 1024 / 1024:.0f}M" if isinstance(x, (int, float)) and x else "-"


async def _backup_fields(ident: str, client) -> dict:
    """查这台服务器的最新备份，拼成可放进 status 行的字段。

    备份接口单独失败不能连累状态查询——用户至少还该看到 CPU 和状态。
    """
    if client is None:
        return {}
    try:
        backups = await client.list_backups(ident)
    except Exception:  # noqa: BLE001 - 备份查不到就标未知，不影响状态
        return {"backup_age_days": None, "backup_newest": None, "backup_ok_count": None}
    newest, age_days, ok_count = _newest_backup_age(backups)
    return {"backup_age_days": age_days, "backup_newest": newest, "backup_ok_count": ok_count}


def _parse_iso_utc(raw: str) -> datetime | None:
    """解析面板返回的时间戳（``2026-11-15T12:34:56.000000Z``）；认不出返回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _newest_backup_age(
    backups: list[dict], now: datetime | None = None
) -> tuple[str | None, float | None, int]:
    """从备份列表里挑出**最新的成功备份**，返回 (ISO 时间, 距今天数, 成功数量)。

    只看 ``is_successful`` 为真的——Pterodactyl 里失败/进行中的备份也可能带
    created_at，拿它当作「有备份」会让用户以为数据安全，实际恢复不了。
    面板版本较老、没有这个字段时按成功处理（宁可漏判，不要全报「从未备份」）。
    """
    moment = now or datetime.now(timezone.utc)
    best: datetime | None = None
    ok = 0
    for b in backups or []:
        a = b.get("attributes", b) or {}
        if a.get("is_successful") is False:
            continue
        ok += 1
        dt = _parse_iso_utc(str(a.get("created_at") or ""))
        if dt is not None and (best is None or dt > best):
            best = dt
    if best is None:
        return None, None, ok
    days = (moment - best).total_seconds() / 86400
    return best.isoformat(), max(0.0, days), ok


async def _status_data(server_id: str | None = None, with_backups: bool = False) -> dict:
    """服务器状态的结构化结果（CLI ``--status`` 用）。

    不复用 ``batch_get_status``：那个只给状态与健康度，命令行最想看的
    CPU / 内存 / 玩家数它没有。这里直接拉 resources，一次拿全。
    """
    if server_id:
        res = await panel.resources(server_id)
        state = res.get("current_state", "?")
        r = res.get("resources", res) or {}
        level, _ = classify_state(state)
        try:
            players = await panel.online_players(server_id)
            players_n: int | None = len(players)
        except Exception:
            players_n = None
        return {
            "servers": [
                {
                    "id": server_id,
                    "name": server_id,
                    "state": state,
                    "health": level,
                    "cpu": r.get("cpu_absolute"),
                    "memory_bytes": r.get("memory_bytes"),
                    "memory_limit": r.get("memory_limit"),
                    "disk_bytes": r.get("disk_bytes"),
                    "disk_limit": r.get("disk_limit"),
                    "players": players_n,
                }
            ],
            "errors": [],
        }

    servers = await panel.client_servers()
    rows: list[dict] = []
    errors: list[str] = []

    async def _one(s: dict) -> None:
        a = _attrs(s)
        ident = str(a.get("identifier") or a.get("uuid") or "?")
        name = _name(a)
        try:
            res = await panel.resources(ident)
            state = res.get("current_state", "?")
            r = res.get("resources", res) or {}
            level, _ = classify_state(state)
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "state": state,
                    "health": level,
                    "cpu": r.get("cpu_absolute"),
                    "memory_bytes": r.get("memory_bytes"),
                    "memory_limit": r.get("memory_limit"),
                    "disk_bytes": r.get("disk_bytes"),
                    "disk_limit": r.get("disk_limit"),
                    "players": None,
                    **(await _backup_fields(ident, panel) if with_backups else {}),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单台失败不阻断整体
            errors.append(f"{ident}: {type(exc).__name__}")

    await _gather_bounded(_one, servers)
    rows.sort(key=lambda x: x["id"])
    return {"servers": rows, "errors": errors}


def _display_width(s: str) -> int:
    """字符串在等宽终端里占几列（中日韩全角算 2，其余算 1）。

    表格分隔线必须按这个算：用 ``len(s)`` 的话，中文列会让横线比表头短一截。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _fmt_disk(n) -> str:
    """磁盘列用的人类可读格式：≥1G 用 G，否则用 M。

    不复用 ``_fmt_size``：那个是给网络流量/备份大小用的（``1.0 MB`` 带空格），
    表格里每列都占宽度，``102400.0 MB`` 会把表撑变形。这里输出 ``100G`` / ``512M``。
    """
    if not isinstance(n, (int, float)):
        return "-"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f}G".replace(".0G", "G")
    return f"{n / 1024 ** 2:.0f}M"


def _fmt_backup_age(row: dict) -> str:
    """把一行里的备份信息渲染成单元格文案。

    ``backup_ok_count`` 为 ``None`` 表示查询本身失败（未知），为 0 表示查到了但
    一个成功备份都没有（从未）——这两个必须区分开，否则用户会以为「从来没备份过」，
    而实际只是面板没查通。
    """
    if "backup_age_days" not in row:
        return "-"
    age = row.get("backup_age_days")
    if age is None:
        return "未知" if row.get("backup_ok_count") is None else "从未"
    if age < 1:
        return "今天"
    return f"{age:.0f} 天前"


def _format_status(data: dict) -> str:
    """把状态结果排成表（等宽，终端里好读）。"""
    rows = data["servers"]
    if not rows and not data["errors"]:
        return "(没有可访问的服务器)"

    icon = {"healthy": "🟢", "transitional": "🟡", "abnormal": "🔴", "unknown": "⚪"}
    # 只有显式要了备份信息才多一列，避免默认输出变宽
    with_backup = any("backup_age_days" in r for r in rows)
    # 列全部服务器时 players 恒为 None——整列都是 `-` 只会让人以为坏了。
    # 有任意一台拿得到人数才显示这一列（单台查询会填 players）。
    with_players = any(r.get("players") is not None for r in rows)
    header = f"{'标识':<18} {'名称':<20} {'状态':<12} {'CPU':>7} {'内存':>12} {'磁盘':>12}"
    if with_players:
        header += f" {'玩家':>5}"
    if with_backup:
        header += f" {'最新备份':>10}"
    lines = [header, "-" * _display_width(header)]
    for r in rows:
        mem = _fmt_mb(r["memory_bytes"])
        lim = r["memory_limit"]
        mem_s = f"{mem}/{_fmt_mb(lim)}" if isinstance(lim, (int, float)) and lim else mem
        cpu = f"{r['cpu']:.1f}%" if isinstance(r["cpu"], (int, float)) else "-"
        players = "-" if r["players"] is None else str(r["players"])
        name = (r["name"] or "")[:20]
        # 磁盘和内存同样处理：有上限就显示 used/limit，没有就只显示 used。
        # 磁盘写满会让存档写不进去，是游戏服务器最常见的事故之一，
        # 而 disk_bytes 之前取了却从来没显示过。
        disk = r.get("disk_bytes")
        dlim = r.get("disk_limit")
        if isinstance(disk, (int, float)):
            disk_s = (f"{_fmt_disk(disk)}/{_fmt_disk(dlim)}"
                      if isinstance(dlim, (int, float)) and dlim > 0 else _fmt_disk(disk))
        else:
            disk_s = "-"
        line = (
            f"{r['id'][:18]:<18} {name:<20} "
            f"{icon.get(r['health'], '⚪')} {r['state'][:10]:<10} {cpu:>7} {mem_s:>12} {disk_s:>12}"
        )
        if with_players:
            line += f" {players:>5}"
        if with_backup:
            line += f" {_fmt_backup_age(r):>10}"
        lines.append(line)
    for e in data["errors"]:
        lines.append(f"  ⚠ {e}")
    return "\n".join(lines)


def _status_signature(data: dict) -> str:
    """把一份 status 数据压成可比较的签名。

    只包含**状态类**字段（id / state / health / 在线人数），刻意不含 CPU 与
    内存——这两项每轮都在小幅抖动，放进来会导致 watch 每轮都刷一整屏表格，
    反而看不出什么时候真的出事了。
    """
    rows = sorted(data.get("servers") or [], key=lambda r: str(r.get("id") or ""))
    return "\n".join(
        f"{r.get('id')}|{r.get('state')}|{r.get('health')}|{r.get('players')}" for r in rows
    )


async def _watch_loop(fetch, *, interval: float, emit, heartbeat, max_rounds: int | None = None):
    """轮询 status：第一次和每次「状态真的变了」时调 ``emit``，其余调 ``heartbeat``。

    把 I/O 全部拿出去（``emit`` / ``heartbeat`` 由调用方给），这样这个循环
    本身可以在测试里跑，不需要真的连面板、也不需要真等。
    返回最后一轮的数据，调用方据此算退出码。
    """
    prev: str | None = None
    data: dict = {}
    rounds = 0
    while True:
        data = await fetch()
        sig = _status_signature(data)
        if prev is None or sig != prev:
            emit(data, prev is None)
            prev = sig
        else:
            heartbeat(data)
        rounds += 1
        if max_rounds is not None and rounds >= max_rounds:
            return data
        await asyncio.sleep(interval)


def _print_help() -> None:
    """``pteropilot --help``：命令行速查。

    必须**不依赖面板配置**——用户拿到命令后第一个敲的就是 ``--help``，
    以前这里会走到启动流程、报「需要设置 PANEL_URL」然后退出 1。
    """
    print(f"pteropilot v{__version__} — 让 AI 助手直接管理 Pterodactyl / Pelican 服务器")
    print()
    print("不带任何参数运行 = 启动 MCP server（给 Claude / Cursor 等客户端用）。")
    print()
    print("自检与信息")
    print("  pteropilot --help                     显示这份说明")
    print("  pteropilot --version                  版本与工具数")
    print("  pteropilot --doctor                   环境自检（--no-network 只查配置）")
    print()
    print("看服务器（都是只读，不需要启动 MCP）")
    print("  pteropilot --status                   全部服务器：状态 / 健康 / CPU / 内存")
    print("  pteropilot --status smp               单台，附带在线人数")
    print("  pteropilot --status --watch           盯着看，只在状态变化时刷屏")
    print("  pteropilot --status --watch --interval 10   轮询间隔 5–3600 秒")
    print("  pteropilot --status --backups          附带「最新备份多久以前」并提醒过期的")
    print("  pteropilot --log smp                  控制台末 80 行")
    print("  pteropilot --log smp --grep error     只匹配行（不区分大小写）")
    print("  pteropilot --log smp --lines 300      往前翻，1–2000 行")
    print("  pteropilot --log \"*\"                 所有服务器一起看（也支持 a,b 逗号列表）")
    print("  pteropilot --players                  本机有采样记录的服务器")
    print("  pteropilot --players smp --days 7     近 7 天在线时长排行（离线也能看）")
    print("  pteropilot --players --record         现在采一条样写进历史（可挂 cron 积累）")
    print("  （以上都支持 --json，输出机器可读）")
    print()
    print("动手（写操作，重要的要加 --confirm）")
    print('  pteropilot --broadcast "say 5 分钟后重启"')
    print('  pteropilot --broadcast "stop" --confirm')
    print("  pteropilot --power restart --servers smp,creative --confirm")
    print("  pteropilot --broadcast \"stop\" --in 5m --confirm     等到点再发（5m / 300s / 2h）")
    print("  pteropilot --power restart --servers smp --at 03:00 --confirm   等到某个时刻")
    print()
    print("环境变量")
    print("  PANEL_URL            面板根地址，如 https://panel.example.com")
    print("  PANEL_API_KEY        客户端密钥（ptlc_ / pacc_），电源/控制台/文件需要它")
    print("  PANEL_APP_KEY        应用密钥（ptla_ / papp_），list_nodes 等需要它（可选）")
    print("  PANEL_READONLY=1     只读模式，所有写操作被拒")
    print("  PTEROPILOT_CONCURRENCY=8     批量工具并发上限（1–32）")
    print("  PTEROPILOT_RETRIES=2         只读请求的额外重试次数（写操作从不重试）")
    print()
    print("文档 https://telson.dev/pteropilot.html　·　教程 https://telson.dev/tutorial.html")


def _warn_stale_backups(data: dict, max_days: int) -> None:
    """把「最新备份太旧 / 从未备份 / 查不到」的服务器单独列出来。

    这一句比表格里的数字更容易被看见——用户扫一眼表格往往只关注红色状态，
    而「上次备份是 90 天前」在崩溃之后才会变成事故。
    """
    never: list[str] = []
    stale: list[tuple[float, str]] = []
    unknown: list[str] = []
    for r in data.get("servers") or []:
        if "backup_age_days" not in r:
            continue
        age = r.get("backup_age_days")
        if age is None:
            (unknown if r.get("backup_ok_count") is None else never).append(str(r["id"]))
        elif age > max_days:
            stale.append((age, str(r["id"])))
    if not (never or stale or unknown):
        print(f"\n备份：全部在 {max_days} 天内有成功备份。")
        return
    print(f"\n⚠ 备份提醒（阈值 {max_days} 天）：")
    for ident in never:
        print(f"  · {ident}：**从未有过成功备份**")
    for age, ident in sorted(stale, reverse=True):
        print(f"  · {ident}：最新备份在 {age:.0f} 天前")
    for ident in unknown:
        print(f"  · {ident}：备份列表查询失败（状态未知）")


def _run_status(argv: list[str]) -> int:
    """``pteropilot --status [标识]``：不开 MCP 客户端，直接看服务器状态。

    不给标识时列出全部（并发拉取，单台失败单独标注不阻断）；
    给标识时额外读一次日志推断在线玩家数。
    """
    sid: str | None = None
    if "--status" in argv:
        i = argv.index("--status")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            sid = argv[i + 1]

    as_json = "--json" in argv
    watch = "--watch" in argv
    # 备份信息要额外请求一次备份接口，所以默认不开
    with_backups = "--backups" in argv
    try:
        config.validate()
    except ValueError as exc:
        print(f"pteropilot --status 需要面板配置：{exc}", file=sys.stderr)
        return 3   # 配置错要和「服务器真的挂了」区分开

    def _int_opt(flag: str, default: int, lo: int, hi: int) -> int:
        if flag not in argv:
            return default
        i = argv.index(flag)
        if i + 1 >= len(argv):
            raise ValueError(f"{flag} 后面要跟一个数字")
        try:
            return max(lo, min(hi, int(argv[i + 1])))
        except ValueError:
            raise ValueError(f"{flag} 需要整数，收到 {argv[i + 1]!r}") from None

    if watch:
        try:
            interval = _int_opt("--interval", 30, 5, 3600)
            rounds = _int_opt("--rounds", 0, 0, 100000) or None   # 0 = 无限
        except ValueError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2

        def _stamp() -> str:
            return datetime.now().strftime("%H:%M:%S")

        def emit(data: dict, first: bool) -> None:
            if as_json:
                print(json.dumps(data, ensure_ascii=False))      # 每行一个对象，方便管道
                return
            head = "首次" if first else "状态变化"
            print(f"\n[{_stamp()}] {head}")
            print(_format_status(data))

        def beat(data: dict) -> None:
            if as_json:
                return
            servers = data.get("servers") or []
            ok = sum(1 for r in servers if r.get("health") == "healthy")
            print(f"[{_stamp()}] 无变化（{ok}/{len(servers)} 正常）", flush=True)

        async def _fetch() -> dict:
            return await _status_data(sid, with_backups=with_backups)

        print(f"每 {interval} 秒检查一次" + (f"，共 {rounds} 轮" if rounds else "") + "，Ctrl+C 退出")
        try:
            data = asyncio.run(_watch_loop(
                _fetch, interval=interval, emit=emit, heartbeat=beat, max_rounds=rounds,
            ))
        except KeyboardInterrupt:
            print("\n已停止。")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"✗ 查询失败：{type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
            return 4   # 面板不可达 / 凭据被拒：不是「服务器异常」
    else:
        try:
            data = asyncio.run(_status_data(sid, with_backups=with_backups))
        except Exception as exc:  # noqa: BLE001 - 面板不可达时给一句话，不糊一屏堆栈
            print(f"✗ 查询失败：{type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
            return 4   # 面板不可达 / 凭据被拒：不是「服务器异常」

        if as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(_format_status(data))

    if with_backups and not as_json:
        _warn_stale_backups(data, _int_opt("--backup-days", 7, 1, 3650))

    if data.get("errors"):
        # 有服务器查不到：这是「查询侧」的问题，不当成「有服务器异常」
        return 4
    # 有异常状态的服务器也算非零，方便脚本/cron 判断
    return 0 if all(r["health"] == "healthy" for r in data["servers"]) else 1


def _record_players(sid: str | None, *, as_json: bool) -> int:
    """采一条玩家在线样并写入本地历史；``sid=None`` 表示全部可访问服务器。

    这个功能需要面板（要读控制台推断谁在线），所以和 ``--players`` 的其余用法
    不同：缺配置时返回 3，面板出错返回 4，与 ``--status`` 的语义保持一致。
    """
    try:
        config.validate()
    except ValueError as exc:
        print(f"pteropilot --players --record 需要面板配置：{exc}", file=sys.stderr)
        return 3

    async def _collect() -> tuple[list[dict], list[str]]:
        ids = await _resolve_target_ids(["*"] if not sid else [sid])
        rows: list[dict] = []
        errors: list[str] = []
        client = PanelClient(config, timeout=20.0)
        try:
            async def _one(ident: str) -> None:
                try:
                    players = await client.online_players(ident)
                except Exception as exc:  # noqa: BLE001 - 单台失败不阻断其余
                    errors.append(f"{ident}: {type(exc).__name__}")
                    return
                written = playerlog.record(ident, players)
                rows.append({"server_id": ident, "players": players, "written": written})

            await _gather_bounded(_one, ids)
        finally:
            await client.aclose()
        rows.sort(key=lambda r: r["server_id"])
        return rows, errors

    try:
        rows, errors = asyncio.run(_collect())
    except Exception as exc:  # noqa: BLE001 - 面板不可达时给一句话，不糊堆栈
        print(f"✗ 采样失败：{type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return 4

    if as_json:
        print(json.dumps({"recorded": rows, "errors": errors}, ensure_ascii=False, indent=2))
    elif not rows:
        print("（没有可访问的服务器）" if not errors else "（全部服务器都采样失败）")
    else:
        for r in rows:
            n = len(r["players"])
            if r["written"]:
                print(f"{r['server_id']}: 记下一条采样（{n} 人在线）")
            else:
                print(f"{r['server_id']}: 与上一条相同（{n} 人），跳过")
    for e in errors:
        print(f"  ⚠ {e}", file=sys.stderr)

    return 0 if not errors else 4


def _run_players(argv: list[str]) -> int:
    """``pteropilot --players [标识] [--days N] [--top N] [--json]``。

    读的是**本机**采样文件（``~/.pteropilot/playerlog/``），所以不需要面板配置、
    不需要联网——面板挂了也能看历史趋势。

    不给标识时列出本地有记录的服务器；给标识时输出该服务器的活跃统计。
    """
    sid: str | None = None
    if "--players" in argv:
        i = argv.index("--players")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            sid = argv[i + 1]

    as_json = "--json" in argv

    def _int_opt(flag: str, default: int, lo: int, hi: int) -> int:
        if flag not in argv:
            return default
        i = argv.index(flag)
        if i + 1 >= len(argv):
            raise ValueError(f"{flag} 后面要跟一个数字")
        raw = argv[i + 1]
        try:
            v = int(raw)
        except ValueError:
            raise ValueError(f"{flag} 需要整数，收到 {raw!r}") from None
        return max(lo, min(hi, v))

    try:
        days = _int_opt("--days", 7, 1, 365)
        top = _int_opt("--top", 10, 1, 100)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    # --record：现在就去面板采一条样写进本地历史
    if "--record" in argv:
        return _record_players(sid, as_json=as_json)

    # 不给标识：列出本地有采样记录的服务器
    if sid is None:
        servers = playerlog.list_servers()
        if as_json:
            print(json.dumps(
                {"servers": [{"server_id": s, "bytes": n} for s, n in servers]},
                ensure_ascii=False, indent=2,
            ))
        elif not servers:
            print("本机还没有任何玩家采样记录。")
            print("补一条：pteropilot --players --record      # 或挂 cron 定期采")
        else:
            print(f"本机有玩家采样记录的服务器（{len(servers)} 台）:")
            for s, n in servers:
                print(f"  {s}   {n / 1024:.1f} KB")
            print("\n看某一台：pteropilot --players <标识> [--days 7] [--json]")
        return 0 if servers else 1

    data = playerlog.stats(sid, days=days)
    if as_json:
        print(json.dumps({"server_id": sid, "days": days, **data}, ensure_ascii=False, indent=2))
    else:
        print(playerlog.format_stats(sid, data, days=days, top=top))

    # 没有采样时返回非零，方便 cron 判断「还没积累够数据」
    return 0 if data.get("samples") else 1


def _run_log(argv: list[str]) -> int:
    """``pteropilot --log <标识> [--lines N] [--grep 关键字] [--json]``。

    服务器崩了、指令没反应时，最想看的就是控制台最后几十行——原来只能开
    AI 客户端问一句。这条命令直接把它打到终端，还能按关键字过滤。

    ``标识`` 可以是单台、``"*"``（全部可访问服务器）或 ``a,b`` 逗号列表。
    ``--lines`` 取末尾 N 行；``--grep`` 不区分大小写，命中 0 行时退出码为 1
    （方便 `until pteropilot --log smp --grep Started | grep -q .` 这类等待脚本）。
    """
    sid: str | None = None
    if "--log" in argv:
        i = argv.index("--log")
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            sid = argv[i + 1]
    if not sid:
        print(
        '用法：pteropilot --log <标识|"*"|a,b> [--lines 80] [--grep 关键字] [--json]',
        file=sys.stderr,
    )
        return 2

    # 一台 / 多台都走同一条路径：identifiers 是待查列表。
    # "*" 交给 _resolve_target_ids 解析成全部可访问服务器；
    # "a,b" 按逗号拆。单台时下面的输出格式与之前完全一致。
    if sid == "*" or "," in sid:
        wanted = ["*"] if sid == "*" else [x.strip() for x in sid.split(",") if x.strip()]
        if not wanted:
            print("✗ --log 的服务器列表是空的", file=sys.stderr)
            return 2
        multi = True
    else:
        wanted = [sid]
        multi = False

    as_json = "--json" in argv

    def _opt(flag: str) -> str | None:
        if flag not in argv:
            return None
        i = argv.index(flag)
        return argv[i + 1] if i + 1 < len(argv) else None

    try:
        lines = int(_opt("--lines") or 80)
    except ValueError:
        print("✗ --lines 需要整数", file=sys.stderr)
        return 2
    lines = max(1, min(2000, lines))
    pattern = _opt("--grep")

    try:
        config.validate()
    except ValueError as exc:
        print(f"pteropilot --log 需要面板配置：{exc}", file=sys.stderr)
        return 1

    def _split(raw: str) -> tuple[list[str], list[str]]:
        """把面板返回的原始日志切成行：清 ANSI、统一换行、去掉末尾空行。"""
        clean = re.sub(r"\x1b\[[0-9;]*m", "", raw).replace("\r\n", "\n").replace("\r", "\n")
        out = clean.split("\n")
        if out and out[-1] == "":
            out.pop()
        return out

    def _filter(all_lines: list[str]) -> list[str]:
        if not pattern:
            return all_lines
        low = pattern.lower()
        return [ln for ln in all_lines if low in ln.lower()]

    if not multi:
        async def _fetch() -> str:
            client = PanelClient(config, timeout=20.0)
            try:
                return await client.console_log(sid)
            finally:
                await client.aclose()

        try:
            raw = asyncio.run(_fetch())
        except Exception as exc:  # noqa: BLE001 - 面板不可达时给一句话，不糊堆栈
            print(f"✗ 读取失败：{type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
            return 1

        all_lines = _split(raw)
        matched = _filter(all_lines)
        tail = matched[-lines:]

        if as_json:
            print(json.dumps({
                "server_id": sid,
                "total_lines": len(all_lines),
                "matched_lines": len(matched),
                "shown_lines": len(tail),
                "grep": pattern,
                "lines": tail,
            }, ensure_ascii=False, indent=2))
        elif not tail:
            if pattern:
                print(f"（{len(all_lines)} 行日志里没有匹配 {pattern!r} 的内容）")
            else:
                print("（控制台目前没有输出）")
        else:
            header = f"### {sid} 控制台"
            if pattern:
                header += f"（匹配 {pattern!r}：{len(matched)}/{len(all_lines)} 行）"
            else:
                header += f"（末 {len(tail)}/{len(all_lines)} 行）"
            print(header)
            for ln in tail:
                print(ln)

        return 0 if tail else 1

    # ---- 多台：并发取，单台失败单独标注，不阻断其余 ----
    async def _fetch_many() -> list[tuple[str, str | BaseException]]:
        ids = await _resolve_target_ids(wanted)
        if not ids:
            return []
        client = PanelClient(config, timeout=20.0)
        try:
            async def _one(i: str):
                try:
                    return i, await client.console_log(i)
                except Exception as exc:  # noqa: BLE001 - 单台失败要如实带回来
                    return i, exc
            return list(await _gather_bounded(_one, ids))
        finally:
            await client.aclose()

    try:
        results = asyncio.run(_fetch_many())
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 读取失败：{type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return 1

    if not results:
        print("（没有可访问的服务器）")
        return 1

    entries: list[dict] = []
    any_output = False
    for ident, payload in results:
        if isinstance(payload, BaseException):
            entries.append({"server_id": ident, "error": type(payload).__name__})
            continue
        all_lines = _split(payload)
        matched = _filter(all_lines)
        tail = matched[-lines:]
        if tail:
            any_output = True
        entries.append({
            "server_id": ident,
            "total_lines": len(all_lines),
            "matched_lines": len(matched),
            "shown_lines": len(tail),
            "lines": tail,
        })

    if as_json:
        print(json.dumps({"servers": entries, "grep": pattern}, ensure_ascii=False, indent=2))
    else:
        for e in entries:
            if "error" in e:
                print(f"### {e['server_id']} 控制台\n❌ 读取失败（{e['error']}）")
                continue
            if not e["lines"]:
                reason = (f"没有匹配 {pattern!r} 的内容" if pattern else "控制台目前没有输出")
                print(f"### {e['server_id']} 控制台\n（{e['total_lines']} 行日志：{reason}）")
                continue
            head = f"### {e['server_id']} 控制台"
            head += (f"（匹配 {pattern!r}：{e['matched_lines']}/{e['total_lines']} 行）"
                     if pattern else f"（末 {e['shown_lines']}/{e['total_lines']} 行）")
            print(head)
            for ln in e["lines"]:
                print(ln)

    return 0 if any_output else 1


def main() -> None:
    argv = sys.argv[1:]
    # --help / -h：必须在所有校验之前。用户拿到命令第一个敲的就是它，
    # 让它报「需要设置 PANEL_URL」是最糟糕的第一印象。
    if "--help" in argv or "-h" in argv or "help" in argv:
        _print_help()
        return
    # --version / -V：只打印版本与工具数，不校验面板配置、不启动 server
    if "--version" in argv or "-V" in argv or "-v" in argv:
        _print_version()
        return
    # --doctor：一键环境自检（不启动 server）
    if "--doctor" in argv:
        sys.exit(_run_doctor(skip_network="--no-network" in argv))
    # --broadcast：广播一条指令后退出（不启动 server）
    if "--broadcast" in argv:
        try:
            config.validate()
        except ValueError as exc:
            print(f"pteropilot --broadcast 需要面板配置：{exc}", file=sys.stderr)
            return sys.exit(1)
        sys.exit(_run_broadcast(argv))
    # --power：批量电源操作后退出（不启动 server）
    if "--power" in argv:
        try:
            config.validate()
        except ValueError as exc:
            print(f"pteropilot --power 需要面板配置：{exc}", file=sys.stderr)
            return sys.exit(1)
        sys.exit(_run_power(argv))
    # --status：命令行看状态后退出（不启动 server）
    if "--status" in argv:
        sys.exit(_run_status(argv))
    # --players：命令行看玩家活跃趋势后退出（只读本机采样，不需要面板配置）
    if "--players" in argv:
        sys.exit(_run_players(argv))
    # --log：命令行看控制台日志后退出（不启动 server）
    if "--log" in argv:
        sys.exit(_run_log(argv))
    try:
        config.validate()
    except ValueError as exc:
        print(f"PteroPilot 启动失败：{exc}", file=sys.stderr)
        print(
            "\n请在 MCP 客户端的配置里补上这两个环境变量：\n"
            '    "env": {\n'
            '      "PANEL_URL": "https://panel.example.com",\n'
            '      "PANEL_API_KEY": "ptlc_xxxxxxxx"\n'
            "    }\n"
            "面板地址从浏览器地址栏复制；API Key 在面板「账户 → API 凭证」里生成。",
            file=sys.stderr,
        )
        sys.exit(1)
    for warn in key_role_warnings(config):
        print(warn, file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
