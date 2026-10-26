"""PteroPilot 告警：把「崩服检测」变成可配置的通知（Roadmap「崩服告警」阶段二/三）。

用法（配合 cron / 定时任务，每几分钟跑一次）：

    PANEL_URL=... PANEL_API_KEY=... \
    pteropilot-alerts --webhook https://oapi.dingtalk.com/robot/send?access_token=xxx

    # 可选：--min-level abnormal（默认，只报异常态）| error（仅错误态）
    #       --token xxx（企业微信等需要在 URL 上拼 key 的场景）
    #       --timeout 30（单台服务器查询超时，秒）
    #       --cooldown 30（分钟：同一服务器同状态下限时间内不重复推送，防告警风暴）

只读：本模块绝不发电源/指令等写操作，只查询状态与日志、推送通知。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from .client import PanelClient
from .config import Config
from .server import classify_state, diagnose_crash

DEFAULT_WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")


def default_state_file() -> Path:
    """冷却状态文件默认位置：~/.pteropilot/alerts_state.json"""
    return Path.home() / ".pteropilot" / "alerts_state.json"


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # 状态文件写不了就退化为每次扫描都通知（宁可重复，不可漏报）
        pass


def build_alert_message(server_id: str, state: str, details: list[str]) -> str:
    """构造告警文本（钉钉/企业微信 text 消息兼容）。"""
    lines = [
        f"⚠️ PteroPilot 告警：服务器 {server_id} 状态异常（{state}）",
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    lines.extend(details)
    lines.append("— 来自 pteropilot-alerts（只读检测，未执行任何操作）")
    return "\n".join(lines)


async def send_webhook(url: str, content: str, token: str | None = None) -> None:
    """向 webhook 推送一条文本消息（钉钉/企业微信机器人兼容格式）。"""
    if token:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}key={token}"
    payload = {"msgtype": "text", "text": {"content": content}}
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.post(url, json=payload)
        r.raise_for_status()


async def scan_and_notify(
    webhook: str,
    min_level: str = "abnormal",
    timeout: float = 30.0,
    include_log_signals: bool = True,
    cooldown_minutes: float = 0.0,
    state_file: Path | None = None,
) -> dict:
    """扫描全部可访问服务器，异常者逐台推送告警。

    min_level: abnormal = 离线/错误/挂起等；error = 仅错误态。
    cooldown_minutes: >0 时启用冷却去重——同一服务器「状态没变 + 冷却期内」不重复推送；
                      状态发生变化则立即推送（新故障值得立刻知道）。
    state_file: 冷却状态持久化位置（默认 ~/.pteropilot/alerts_state.json，仅冷却开启时使用）。
    返回 {checked, abnormal, notified, suppressed, errors}。
    """
    cfg = Config()
    cfg.validate()
    client = PanelClient(cfg, timeout=timeout)
    abnormal_states = {"error", "suspended"} if min_level == "error" else {"offline", "error", "suspended"}

    if cooldown_minutes > 0:
        path = state_file or default_state_file()
        state = load_state(path)
    else:
        path = None
        state = None

    checked = 0
    abnormal = 0
    notified = 0
    suppressed = 0
    errors: list[str] = []

    try:
        servers = await client.client_servers()
        for s in servers:
            ident = (s.get("attributes") or {}).get("identifier") or (s.get("attributes") or {}).get("uuid") or "?"
            checked += 1
            try:
                res = await client.resources(ident)
                state_now = res.get("current_state", "?")
                level, _hint = classify_state(state_now)
                if level != "abnormal" or state_now not in abnormal_states:
                    continue
                abnormal += 1

                # 冷却去重：状态未变且仍在冷却期内 → 跳过
                prev = (state or {}).get(ident, {}) if state else {}
                changed = prev.get("state") != state_now
                if state is not None and not changed:
                    elapsed = time.time() - float(prev.get("ts") or 0)
                    if elapsed < cooldown_minutes * 60:
                        suppressed += 1
                        continue

                details = [f"健康度: {state_now}"]
                if include_log_signals:
                    try:
                        diag = await diagnose_crash_for(ident, client, timeout)
                        if diag:
                            details.append(diag)
                    except Exception:
                        details.append("（日志读取失败，跳过日志级信号）")
                await send_webhook(webhook, build_alert_message(ident, state_now, details))
                notified += 1
                if state is not None:
                    state[ident] = {"state": state_now, "ts": time.time()}
            except Exception as exc:  # noqa: BLE001 - 单台失败不阻断整体
                errors.append(f"{ident}: {type(exc).__name__}: {exc}")
    finally:
        await client.aclose()
        if state is not None and path is not None:
            save_state(path, state)

    return {
        "checked": checked,
        "abnormal": abnormal,
        "notified": notified,
        "suppressed": suppressed,
        "errors": errors,
    }


async def diagnose_crash_for(server_id: str, client: PanelClient, timeout: float) -> str | None:
    """只取 diagnose_crash 的「崩溃信号」摘要段，避免整段日志刷屏。"""
    # 复用 server 模块的诊断逻辑：临时把模块级 panel 指向本客户端再调用
    import pteropilot.server as srv

    old_panel = srv.panel
    srv.panel = client
    try:
        out = await diagnose_crash(server_id)
    finally:
        srv.panel = old_panel
    lines = out.splitlines()
    for ln in lines:
        if ln.startswith("崩溃信号"):
            return ln
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pteropilot-alerts",
        description="PteroPilot 只读告警扫描：检测异常/崩溃服务器并推送 webhook",
    )
    parser.add_argument("--webhook", default=DEFAULT_WEBHOOK_URL, help="钉钉/企业微信机器人 webhook URL（或用 WEBHOOK_URL 环境变量）")
    parser.add_argument("--min-level", choices=["abnormal", "error"], default="abnormal", help="最低告警级别")
    parser.add_argument("--token", default=None, help="附加到 webhook URL 的 key（企业微信等）")
    parser.add_argument("--timeout", type=float, default=30.0, help="单台服务器查询超时（秒）")
    parser.add_argument("--no-log-signals", action="store_true", help="不读取日志级崩溃信号（更快，只按状态判断）")
    parser.add_argument("--cooldown", type=float, default=0.0, help="告警冷却（分钟）：同一服务器同状态下限时间内不重复推送，防告警风暴")
    parser.add_argument("--state-file", default=None, help="冷却状态文件路径（默认 ~/.pteropilot/alerts_state.json）")
    args = parser.parse_args()

    if not args.webhook:
        parser.error("需要 --webhook 或环境变量 WEBHOOK_URL")

    result = asyncio.run(
        scan_and_notify(
            webhook=args.webhook,
            min_level=args.min_level,
            timeout=args.timeout,
            include_log_signals=not args.no_log_signals,
            cooldown_minutes=args.cooldown,
            state_file=Path(args.state_file) if args.state_file else None,
        )
    )
    print(
        f"扫描完成：检查 {result['checked']} 台，异常 {result['abnormal']} 台，"
        f"已推送 {result['notified']} 台" + (f"，冷却跳过 {result['suppressed']} 台" if result["suppressed"] else "")
    )
    for e in result["errors"]:
        print(f"  查询失败: {e}")


if __name__ == "__main__":
    main()
