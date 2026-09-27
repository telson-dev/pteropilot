"""PteroPilot MCP server。

工具分两类：
- 只读：列表 / 状态 / 读取，随时可用。
- 写入/危险：电源、指令、写文件、删除、恢复等——
  * PANEL_READONLY=1 时一律拒绝；
  * 高危操作（restart/kill、覆盖、删除、恢复备份）必须显式 confirm=True。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import POWER_SIGNALS, PanelClient
from .config import Config

mcp = FastMCP("pteropilot")
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
    r = res.get("resources", res)
    state = r.get("current_state", "?")

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
    """控制服务器电源：start/stop/restart/kill。restart 与 kill 需 confirm=True。"""
    denied = _denied_if_readonly()
    if denied:
        return denied
    if signal not in POWER_SIGNALS:
        return f"非法信号 {signal}，可选: {', '.join(POWER_SIGNALS)}"
    if signal in ("restart", "kill"):
        blocked = _need_confirm(confirm, f"{signal} 电源操作")
        if blocked:
            return blocked
    await panel.power(server_id, signal)
    return f"✅ 已向 {server_id} 发送电源信号: {signal}"


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
    config.validate()
    mcp.run()


if __name__ == "__main__":
    main()
