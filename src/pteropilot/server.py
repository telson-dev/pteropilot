"""PteroPilot MCP server。

工具分两类：
- 只读：列表 / 状态 / 读取，随时可用。
- 写入/危险：电源、指令、写文件、删除、恢复等——
  * PANEL_READONLY=1 时一律拒绝；
  * 高危操作（restart/kill、覆盖、删除、恢复备份）必须显式 confirm=True。
"""

from __future__ import annotations

import base64
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .client import POWER_SIGNALS, PanelClient
from .config import Config

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
async def list_my_servers() -> str:
    """列出当前 API Key 可访问的服务器：标识、名称、UUID。"""
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"
    lines = []
    for s in servers:
        a = _attrs(s)
        lines.append(f"- {a.get('identifier', '?')} | {_name(a)} | {a.get('uuid', '?')}")
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


@mcp.tool()
async def get_server_status(server_id: str) -> str:
    """查看单个服务器实时状态与资源占用：状态、CPU、内存、磁盘、网络、在线时长。"""
    res = await panel.resources(server_id)
    state = res.get("current_state", "?")
    r = res.get("resources", res)

    def mb(x: Any) -> str:
        return f"{x / 1024 / 1024:.1f} MB" if isinstance(x, (int, float)) else str(x)

    return (
        f"服务器: {server_id}\n"
        f"状态: {state}\n"
        f"CPU: {r.get('cpu_absolute', '?')}%\n"
        f"内存: {mb(r.get('memory_bytes'))}\n"
        f"磁盘: {mb(r.get('disk_bytes'))}\n"
        f"网络 rx/tx: {r.get('network_rx_bytes', '?')} / {r.get('network_tx_bytes', '?')}\n"
        f"在线时长: {r.get('uptime', 0)} ms"
    )


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
}


@mcp.tool()
async def diagnose_crash(server_id: str, tail: int = 60) -> str:
    """诊断服务器是否崩溃/异常（Roadmap「崩服告警」检测点，只读）。

    结合当前状态与最近控制台日志：扫描日志中的崩溃信号（内存不足 / JVM 崩溃 /
    进程异常退出 / 致命错误），输出检测结果、命中行数与建议动作。
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
async def list_files(server_id: str, directory: str = "/") -> str:
    """列出服务器某目录下的文件与子目录（含大小、类型）。"""
    files = await panel.list_files(server_id, directory)
    if not files:
        return f"(目录 {directory} 为空)"
    lines = []
    for f in files:
        a = _attrs(f)
        kind = "DIR " if a.get("is_file") is False else "FILE"
        lines.append(f"- [{kind}] {a.get('name', '?')} ({a.get('size', '?')} bytes)")
    return "\n".join(lines)


@mcp.tool()
async def read_file(server_id: str, path: str, max_chars: int = 20000) -> str:
    """读取服务器上的文本文件内容（默认最多返回 20000 字符）。"""
    content = await panel.read_file(server_id, path)
    if len(content) > max_chars:
        return content[:max_chars] + f"\n...[已截断，文件共 {len(content)} 字符]"
    return content


@mcp.tool()
async def download_file(server_id: str, path: str) -> str:
    """获取服务器上某文件的一次性下载链接（约 15 分钟有效）。"""
    url = await panel.download_url(server_id, path)
    if not url:
        return "(未获取到下载链接)"
    return f"🔗 下载链接（约 15 分钟有效）:\n{url}"


@mcp.tool()
async def list_backups(server_id: str) -> str:
    """列出服务器备份：名称、UUID、大小、完成状态、是否锁定。"""
    backups = await panel.list_backups(server_id)
    if not backups:
        return "(暂无备份)"
    lines = []
    for b in backups:
        a = _attrs(b)
        done = "完成" if a.get("completed_at") else "进行中"
        locked = "已锁定" if a.get("is_locked") else "未锁定"
        lines.append(
            f"- {a.get('uuid', '?')} | {_name(a)} | {a.get('bytes', '?')} bytes | {done} | {locked}"
        )
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
    for sid in server_ids:
        try:
            await panel.power(sid, signal)
            lines.append(f"- {sid} | ✅ 已发送 {signal}")
            ok += 1
        except Exception:
            # 单台失败不阻断整体，标注后继续。
            lines.append(f"- {sid} | ❌ 失败（无权限/超时/面板报错）")
            failed += 1

    header = f"批量 {signal}：共 {len(server_ids)} 台，成功 {ok} 台、失败 {failed} 台："
    return header + "\n" + "\n".join(lines)


@mcp.tool()
async def batch_backup_status() -> str:
    """批量备份概览（只读）：遍历我可访问的服务器，逐台列出备份数量与最近备份时间。

    适合快速回答「哪些服务器没有最近备份」。单台查询失败单独标注、不阻断整体。
    """
    servers = await panel.client_servers()
    if not servers:
        return "(没有可访问的服务器)"
    lines: list[str] = []
    failed = 0
    for s in servers:
        attrs = s.get("attributes") or {}
        sid = attrs.get("identifier") or attrs.get("id") or "?"
        name = attrs.get("name") or sid
        try:
            backups = await panel.list_backups(sid)
            count = len(backups)
            newest = ""
            for b in backups:
                created = (b.get("attributes") or {}).get("created_at") or ""
                if created and (not newest or created > newest):
                    newest = created
            newest_txt = newest.split("T")[0] if newest else "无"
            lines.append(f"- {sid} | {name} | {count} 个备份 | 最近: {newest_txt}")
        except Exception:
            failed += 1
            lines.append(f"- {sid} | {name} | ❌ 查询失败")
    header = f"批量备份概览：共 {len(servers)} 台，失败 {failed} 台："
    return header + "\n" + "\n".join(lines)


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
    for sid in server_ids:
        try:
            backup = await panel.create_backup(sid)
            lines.append(f"- {sid} | ✅ 备份任务已创建（{backup.get('uuid', '?')}）")
            ok += 1
        except Exception:
            lines.append(f"- {sid} | ❌ 创建失败（无权限/超时/面板报错）")
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
async def send_command(server_id: str, command: str) -> str:
    """向服务器控制台发送一条指令。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    await panel.send_command(server_id, command)
    return f"✅ 指令已发送: {command}"


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
    mcp.run()


if __name__ == "__main__":
    main()
