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
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import POWER_SIGNALS, PanelClient
from .config import Config, key_role_warnings

mcp = FastMCP("pteropilot")
# mcp 1.30 的 FastMCP 不接 version 参数，握手时会 fallback 成 mcp 框架自己的
# 版本号（1.30.0）。只能直接改 low-level server，客户端才能看到真实版本。
mcp._mcp_server.version = __version__
config = Config()
panel = PanelClient(config)


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

    states = await asyncio.gather(*(_state(a.get("identifier") or a.get("id") or "?") for a, _ in rows))
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

    遍历当前 API Key 可访问的每台服务器，逐台返回一行「标识 | 名称 | 状态 | 健康等级」。
    只读、纯摘要，不触碰任何写入/危险操作（批量操作暂不扩展，逐台 confirm 语义不变）。
    单台调用超时或失败不会阻断整体，会单独标注为「查询失败」。
    """
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"

    lines: list[str] = []
    ok = 0
    failed = 0
    for s in servers:
        a = _attrs(s)
        ident = a.get("identifier") or a.get("uuid") or "?"
        name = _name(a)
        try:
            res = await panel.resources(ident)
            state = res.get("current_state", "?")
            level, _hint = classify_state(state)
            level_cn = {"healthy": "🟢", "transitional": "🟡", "abnormal": "🔴", "unknown": "⚪"}[level]
            lines.append(f"- {ident} | {name} | {state} | {level_cn}")
            ok += 1
        except Exception:
            # 单台失败（超时/无权限/面板报错）不阻断整体，标注后继续。
            lines.append(f"- {ident} | {name} | 查询失败（超时或面板报错）")
            failed += 1

    header = f"共 {len(servers)} 台服务器，成功 {ok} 台、失败 {failed} 台："
    return header + "\n" + "\n".join(lines)


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
async def list_players(server_id: str) -> str:
    """查当前在线的玩家：从最近控制台日志解析玩家加入/退出，列出在线名单。
    
    注意：此名单基于最近控制台日志推断，**非权威**。控制台读取端点通常只返回最近约 1000 行，
    更早的玩家加入/退出或服务器重启可能已被截断，名单可能不完整或包含已离线的玩家。
    需要权威在线人数时，请结合服务端权威来源（如 RCON / 面板资源接口）另行核实。
    """
    players = await panel.online_players(server_id)
    if not players:
        return "(当前没有检测到在线玩家，或日志里没有玩家进出记录)"
    return "当前在线玩家 (" + str(len(players)) + " 人):\n" + "\n".join(f"- {p}" for p in players)


@mcp.tool()
async def batch_list_players(server_ids: list[str]) -> str:
    """批量查看多台服务器当前在线玩家（只读，并发执行）。

    每台基于最近控制台日志推断（**非权威**，口径同 list_players）；
    单台查询失败单独标注、不阻断其余；结果按 server_ids 顺序排列。
    """
    if not server_ids:
        return "(未指定任何服务器)"

    async def _one(sid: str) -> str:
        try:
            players = await panel.online_players(sid)
            if not players:
                return f"### {sid}\n(无在线玩家记录)"
            return f"### {sid}（{len(players)} 人）\n" + "\n".join(f"- {p}" for p in players)
        except Exception as e:
            return f"### {sid}\n❌ 查询失败: {e}"

    results = await asyncio.gather(*(_one(sid) for sid in server_ids))
    return "\n\n".join(results)


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


@mcp.tool()
async def batch_power(server_ids: list[str], signal: str, confirm: bool = False) -> str:
    """批量控制一组服务器的电源（Roadmap「批量开服」写路径收尾）。

    面板 API 无原生批量电源端点（Pterodactyl/Pelican 都只有单台
    `/api/client/servers/{id}/power`），故退化为「逐台循环调用单台 power + 整体二次确认」：

    - 只读模式下整体拒绝，不发出任何请求；
    - signal 非法则整体拒绝；
    - stop/restart/kill 需整体 confirm=True（一次确认覆盖整批，start 免确认）；
    - 逐台 apply，单台失败（超时/无权限/面板报错）单独标注、不阻断其余服务器。
    """
    denied = _denied_if_readonly()
    if denied:
        return denied
    if not server_ids:
        return "(未指定任何服务器)"
    if signal not in POWER_SIGNALS:
        return f"非法信号 {signal}，可选: {', '.join(POWER_SIGNALS)}"
    if signal in ("stop", "restart", "kill"):
        blocked = _need_confirm(confirm, f"批量 {signal}（{len(server_ids)} 台服务器）")
        if blocked:
            return blocked

    ok = 0
    failed = 0
    lines: list[str] = []

    async def _apply(sid: str) -> str:
        try:
            await panel.power(sid, signal)
            return f"- {sid} | ✅ 已发送 {signal}"
        except Exception:
            # 单台失败不阻断整体，标注后继续。
            return f"- {sid} | ❌ 失败（无权限/超时/面板报错）"

    # 并发执行（gather 保序）：面板无批量端点，但并发调用显著缩短整批耗时。
    for line in await asyncio.gather(*(_apply(sid) for sid in server_ids)):
        lines.append(line)
        if "✅" in line:
            ok += 1
        else:
            failed += 1

    header = f"批量 {signal}：共 {len(server_ids)} 台，成功 {ok} 台、失败 {failed} 台："
    return header + "\n" + "\n".join(lines)


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

    results = await asyncio.gather(*(_one(s) for s in servers))
    failed = sum(1 for line in results if "查询失败" in line)
    header = f"批量备份概览：共 {len(servers)} 台，失败 {failed} 台："
    return header + "\n" + "\n".join(results)


@mcp.tool()
async def batch_backup(server_ids: list[str], confirm: bool = False) -> str:
    """批量创建备份（写操作，需 confirm=True）。

    面板 API 无原生批量备份端点，退化为「逐台循环调用单台 create_backup + 整体二次确认」：
    - 只读模式下整体拒绝；
    - 未指定服务器或 confirm=False 时整体拒绝；
    - 逐台创建，单台失败（超时/无权限/面板报错）单独标注、不阻断其余服务器。
    """
    denied = _denied_if_readonly()
    if denied:
        return denied
    if not server_ids:
        return "(未指定任何服务器)"
    blocked = _need_confirm(confirm, f"批量创建备份（{len(server_ids)} 台服务器）")
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
    for line in await asyncio.gather(*(_create(sid) for sid in server_ids)):
        lines.append(line)
        if "✅" in line:
            ok += 1
        else:
            failed += 1

    header = f"批量创建备份：共 {len(server_ids)} 台，成功 {ok} 台、失败 {failed} 台："
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

    适合「多台服务器同时出问题」的场景：一次拿多台的控制台输出对比。
    单台读取失败单独标注、不阻断其余；每台输出按 server_ids 顺序排列。
    """
    if not server_ids:
        return "(未指定任何服务器)"

    async def _one(sid: str) -> str:
        try:
            log = await panel.console_log(sid)
            if len(log) > max_chars:
                log = log[-max_chars:] + f"\n...[已截断，仅显示末尾 {max_chars} 字符]"
            return f"### {sid}\n{log}"
        except Exception as e:
            return f"### {sid}\n❌ 读取失败: {e}"

    results = await asyncio.gather(*(_one(sid) for sid in server_ids))
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

    # 并发广播（gather 保序）：多台服务器同时发公告，显著缩短整批耗时。
    for done_name, done_fail in await asyncio.gather(
        *(_bcast(sid, name) for sid, name in targets)
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


def main() -> None:
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
