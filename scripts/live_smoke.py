"""live_smoke.py — 对真实面板做只读端到端实测并输出报告。

Roadmap「双面板兼容深化」的落地工具：在你自己的 Pterodactyl / Pelican 面板上
跑一遍全部只读端点，把结果贴进 docs/compatibility.md 作为「已实测」标记。

用法：
    PANEL_URL=https://panel.example.com \
    PANEL_API_KEY=ptlc_xxx \
    PANEL_APP_KEY=ptla_xxx \
    python scripts/live_smoke.py

只读：本脚本只发 GET，绝不触发电源/指令/写文件/备份等任何写操作。
"""

from __future__ import annotations

import asyncio
import sys

from pteropilot.client import PanelClient
from pteropilot.config import (
    APP_KEY_PREFIXES,
    CLIENT_KEY_PREFIXES,
    Config,
)


def panel_type_hint(cfg: Config) -> str:
    key = cfg.panel_api_key or ""
    if key.startswith("ptlc_"):
        return "Pterodactyl（客户端密钥）"
    if key.startswith("pacc_"):
        return "Pelican（客户端密钥）"
    if key.startswith("ptla_") or key.startswith("papp_"):
        return "疑似应用密钥——客户端 API 工具会 403！"
    return "未知面板/密钥前缀"


def _summarize(value) -> str:
    if isinstance(value, list):
        if not value:
            return "空列表"
        first = value[0]
        attrs = first.get("attributes") or first if isinstance(first, dict) else first
        if isinstance(attrs, dict):
            return f"{len(value)} 项，首项字段: {', '.join(list(attrs.keys())[:6])}"
        return f"{len(value)} 项"
    if isinstance(value, str):
        return f"{len(value)} 字符" if len(value) > 40 else repr(value)
    if isinstance(value, dict):
        return f"字段: {', '.join(list(value.keys())[:6])}"
    return str(value)


async def _probe(name: str, coro) -> tuple[bool, str]:
    try:
        value = await coro
        return True, _summarize(value)
    except Exception as exc:  # noqa: BLE001 - 报告里需要原始错误信息
        return False, f"{type(exc).__name__}: {exc}"


async def run(cfg: Config | None = None) -> dict:
    """执行全部只读端点探测，返回 {端点: (ok, 摘要)} 与面板提示。"""
    cfg = cfg or Config()
    cfg.validate()
    pc = PanelClient(cfg)
    results: dict = {}
    try:
        servers_ok, servers_info = await _probe("client_servers", pc.client_servers())
        results["client_servers (GET /api/client)"] = (servers_ok, servers_info)
        sid = ""
        if servers_ok:
            servers = await pc.client_servers()
            for s in servers:
                attrs = s.get("attributes") or {}
                if attrs.get("identifier") or attrs.get("id"):
                    sid = attrs.get("identifier") or attrs.get("id")
                    break

        if sid:
            results["resources (GET /servers/{id}/resources)"] = await _probe("resources", pc.resources(sid))
            results["console_log (GET /servers/{id}/console)"] = await _probe("console_log", pc.console_log(sid))
            results["list_files (GET /servers/{id}/files/list)"] = await _probe("list_files", pc.list_files(sid))
            results["list_backups (GET /servers/{id}/backups)"] = await _probe("list_backups", pc.list_backups(sid))
        else:
            results["（客户端端点）"] = (False, "client_servers 返回空或缺少 identifier，跳过服务器级端点")

        results["list_nodes (GET /api/application/nodes)"] = await _probe("list_nodes", pc.list_nodes())
        results["list_all_servers (GET /api/application/servers)"] = await _probe("list_all_servers", pc.list_servers())
        results["list_users (GET /api/application/users)"] = await _probe("list_users", pc.list_users())
    finally:
        await pc.aclose()
    return {"panel_hint": panel_type_hint(cfg), "results": results}


def _print_report(report: dict) -> None:
    print(f"面板识别: {report['panel_hint']}")
    ok = 0
    for name, (is_ok, summary) in report["results"].items():
        mark = "✅" if is_ok else "❌"
        if is_ok:
            ok += 1
        print(f"  {mark} {name}: {summary}")
    total = len(report["results"])
    print(f"\n通过 {ok}/{total} 个端点。" + ("全部通过——可把本报告贴进 docs/compatibility.md 的「已实测」栏。" if ok == total else "有失败端点，请按错误信息排查（权限/密钥角色/面板版本）。"))


def main() -> None:
    try:
        report = asyncio.run(run())
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        sys.exit(1)
    _print_report(report)


if __name__ == "__main__":
    main()
