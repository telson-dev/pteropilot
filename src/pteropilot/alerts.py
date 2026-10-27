"""PteroPilot 告警：把「崩服检测」变成可配置的通知（Roadmap「崩服告警」阶段二/三/四）。

用法（配合 cron / 定时任务，每几分钟跑一次）：

    PANEL_URL=... PANEL_API_KEY=... \
    pteropilot-alerts --webhook https://oapi.dingtalk.com/robot/send?access_token=xxx

    # 邮件渠道（webhook 之外的第二通道，可与 webhook 同时用）：
    #   --smtp-host smtp.example.com --smtp-user you@x.com --smtp-pass xxx --mail-to ops@x.com
    #   --smtp-port 465（SSL，默认）| 587（STARTTLS）

    # 可选：--min-level abnormal（默认，只报异常态）| error（仅错误态）
    #       --token xxx（企业微信等需要在 URL 上拼 key 的场景）
    #       --timeout 30（单台服务器查询超时，秒）
    #       --cooldown 30（分钟：同一服务器同状态下限时间内不重复推送，防告警风暴）
    #       --confirm-count 3（连续确认阈值，过滤瞬时抖动）
    #       --config ~/.pteropilot/alerts.json（按服务器级别覆盖灵敏度，见下方 JSON 示例）

# 按服务器级别配置示例（~/.pteropilot/alerts.json，可选；优先级：服务器 > default > 命令行参数）：
# {
#   "default":      { "confirm_count": 2, "cooldown_minutes": 15 },
#   "servers": {
#     "smp":        { "confirm_count": 3, "cooldown_minutes": 60 },
#     "creative":   { "min_level": "error", "email": false },          # 只走 webhook
#     "auth":       { "webhook": false, "email": true,                 # 只走邮件，且发给专线
#                     "mail_to": "oncall@x.com" },
#     "preview":    { "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=bbb" }
#   }
# }

只读：本模块绝不发电源/指令等写操作，只查询状态与日志、推送通知。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import httpx

from .client import PanelClient
from .config import Config
from .server import classify_state, diagnose_crash

DEFAULT_WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")


def default_state_file() -> Path:
    """冷却状态文件默认位置：~/.pteropilot/alerts_state.json"""
    return Path.home() / ".pteropilot" / "alerts_state.json"


def default_config_file() -> Path:
    """按服务器级别配置默认位置：~/.pteropilot/alerts.json"""
    return Path.home() / ".pteropilot" / "alerts.json"


def load_alerts_config(path: Path | None) -> dict:
    """读取按服务器级别配置；文件不存在或损坏时返回空配置。"""
    if path is None:
        path = default_config_file()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cfg, dict):
            return cfg
        return {}
    except Exception:
        return {}


def server_effective(
    cfg: dict,
    server_id: str,
    confirm_count: int,
    cooldown_minutes: float,
    min_level: str,
    webhook: str = "",
    email_enabled: bool = False,
    smtp_to: str = "",
) -> dict:
    """合并服务器配置：服务器条目 > default 段 > 命令行/函数参数。

    返回键：confirm_count / cooldown_minutes / min_level /
            webhook（是否启用 webhook）/ email（是否启用邮件）/
            webhook_url（每服务器 webhook 目标，默认全局）/ mail_to（每服务器收件人）。
    """
    merged = {
        "confirm_count": confirm_count,
        "cooldown_minutes": cooldown_minutes,
        "min_level": min_level,
        "webhook": bool(webhook),
        "email": email_enabled,
        "webhook_url": webhook,
        "mail_to": smtp_to,
    }
    dft = cfg.get("default") or {}
    srv = (cfg.get("servers") or {}).get(server_id) or {}
    for key in ("confirm_count", "cooldown_minutes", "min_level", "webhook", "email", "webhook_url", "mail_to"):
        if key in dft:
            merged[key] = dft[key]
        if key in srv:
            merged[key] = srv[key]
    merged["confirm_count"] = int(merged["confirm_count"])
    merged["cooldown_minutes"] = float(merged["cooldown_minutes"])
    merged["webhook"] = bool(merged["webhook"])
    merged["email"] = bool(merged["email"])
    return merged


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


def send_email(
    to: str,
    subject: str,
    content: str,
    host: str,
    user: str,
    password: str,
    port: int = 465,
    frm: str | None = None,
) -> None:
    """发送一封告警邮件（465 = SSL，587 = STARTTLS）。"""
    frm = frm or user
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to
    msg.set_content(content)
    if port == 587:
        with smtplib.SMTP(host, port, timeout=15.0) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=15.0) as s:
            s.login(user, password)
            s.send_message(msg)


async def scan_and_notify(
    webhook: str = "",
    min_level: str = "abnormal",
    timeout: float = 30.0,
    include_log_signals: bool = True,
    cooldown_minutes: float = 0.0,
    state_file: Path | None = None,
    smtp_host: str = "",
    smtp_port: int = 465,
    smtp_user: str = "",
    smtp_password: str = "",
    smtp_to: str = "",
    smtp_from: str = "",
    confirm_count: int = 1,
    config_file: Path | None = None,
) -> dict:
    """扫描全部可访问服务器，异常者逐台推送告警（webhook 和/或邮件）。

    min_level: abnormal = 离线/错误/挂起等；error = 仅错误态。
    cooldown_minutes: >0 时启用冷却去重——同一服务器「状态没变 + 冷却期内」不重复推送；
                      状态发生变化则立即推送（新故障值得立刻知道）。
    confirm_count: >1 时启用连续确认——同一服务器连续 N 次检测到异常才推送，
                   避免瞬时抖动误报；恢复健康会清零计数。
    state_file: 冷却/确认状态持久化位置（默认 ~/.pteropilot/alerts_state.json）。
    smtp_*: 邮件通道（host+user+password+to 齐了才会发邮件；可与 webhook 同时使用）。
    返回 {checked, abnormal, notified, suppressed, pending, errors}。
    """
    cfg = Config()
    cfg.validate()
    client = PanelClient(cfg, timeout=timeout)
    alerts_cfg = load_alerts_config(config_file)

    use_state = cooldown_minutes > 0 or confirm_count > 1 or bool(alerts_cfg)
    if use_state:
        path = state_file or default_state_file()
        state = load_state(path)
    else:
        path = None
        state = None

    email_enabled = bool(smtp_host and smtp_user and smtp_to)

    checked = 0
    abnormal = 0
    notified = 0
    suppressed = 0
    pending = 0
    errors: list[str] = []

    try:
        servers = await client.client_servers()
        for s in servers:
            ident = (s.get("attributes") or {}).get("identifier") or (s.get("attributes") or {}).get("uuid") or "?"
            checked += 1
            try:
                res = await client.resources(ident)
                state_now = res.get("current_state", "?")
                eff = server_effective(
                    alerts_cfg, ident, confirm_count, cooldown_minutes, min_level,
                    webhook=webhook, email_enabled=email_enabled, smtp_to=smtp_to,
                )
                abnormal_states = {"error", "suspended"} if eff["min_level"] == "error" else {"offline", "error", "suspended"}
                cc = eff["confirm_count"]
                cd = eff["cooldown_minutes"]
                wh_url = eff["webhook_url"] if eff["webhook"] else ""
                mail_to = eff["mail_to"] if eff["email"] else ""
                level, _hint = classify_state(state_now)
                if level != "abnormal" or state_now not in abnormal_states:
                    # 健康：清零连续确认计数
                    if state is not None and ident in state:
                        state[ident]["hits"] = 0
                    continue
                abnormal += 1

                prev = (state or {}).get(ident, {}) if state else {}
                hits = int(prev.get("hits") or 0) + 1

                # 连续确认：未达到阈值 → 记录计数、暂不推送（不推进通知时钟 ts）
                if cc > 1 and hits < cc:
                    if state is not None:
                        state[ident] = {"state": state_now, "hits": hits}
                    pending += 1
                    continue

                # 冷却去重：状态未变且仍在冷却期内 → 跳过
                changed = prev.get("state") != state_now
                if state is not None and not changed:
                    elapsed = time.time() - float(prev.get("ts") or 0)
                    if elapsed < cd * 60:
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
                message = build_alert_message(ident, state_now, details)
                if wh_url:
                    await send_webhook(wh_url, message)
                if mail_to:
                    await send_email(
                        to=mail_to,
                        subject=f"PteroPilot 告警：服务器 {ident} 状态异常（{state_now}）",
                        content=message,
                        host=smtp_host,
                        user=smtp_user,
                        password=smtp_password,
                        port=smtp_port,
                        frm=smtp_from or None,
                    )
                if wh_url or mail_to:
                    notified += 1
                if state is not None:
                    state[ident] = {"state": state_now, "hits": hits, "ts": time.time()}
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
        "pending": pending,
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
    parser.add_argument("--smtp-host", default=os.environ.get("SMTP_HOST", ""), help="SMTP 服务器（邮件通道）")
    parser.add_argument("--smtp-port", type=int, default=int(os.environ.get("SMTP_PORT", "465")), help="SMTP 端口（465=SSL 默认，587=STARTTLS）")
    parser.add_argument("--smtp-user", default=os.environ.get("SMTP_USER", ""), help="SMTP 用户名")
    parser.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS", ""), help="SMTP 密码/授权码")
    parser.add_argument("--mail-to", default=os.environ.get("MAIL_TO", ""), help="收件人邮箱")
    parser.add_argument("--mail-from", default=os.environ.get("MAIL_FROM", ""), help="发件人邮箱（默认同 smtp-user）")
    parser.add_argument("--confirm-count", type=int, default=int(os.environ.get("CONFIRM_COUNT", "1")), help="连续确认阈值：同一服务器连续 N 次检测异常才推送（默认 1 = 立即推送；设 2+ 防瞬时抖动误报）")
    parser.add_argument("--config", default=None, help="按服务器级别配置路径（默认 ~/.pteropilot/alerts.json，存在即生效）")
    args = parser.parse_args()

    if not args.webhook and not (args.smtp_host and args.smtp_user and args.mail_to):
        parser.error("至少需要一种通知渠道：--webhook，或完整的 SMTP 邮件配置（--smtp-host/--smtp-user/--smtp-pass/--mail-to）")

    result = asyncio.run(
        scan_and_notify(
            webhook=args.webhook,
            min_level=args.min_level,
            timeout=args.timeout,
            include_log_signals=not args.no_log_signals,
            cooldown_minutes=args.cooldown,
            state_file=Path(args.state_file) if args.state_file else None,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_password=args.smtp_pass,
            smtp_to=args.mail_to,
            smtp_from=args.mail_from,
            confirm_count=args.confirm_count,
            config_file=Path(args.config) if args.config else None,
        )
    )
    channels = []
    if args.webhook:
        channels.append("webhook")
    if args.smtp_host and args.smtp_user and args.mail_to:
        channels.append("邮件")
    print(
        f"扫描完成（通知渠道: {'+'.join(channels) or '无'}）：检查 {result['checked']} 台，"
        f"异常 {result['abnormal']} 台，已推送 {result['notified']} 台"
        + (f"，冷却跳过 {result['suppressed']} 台" if result["suppressed"] else "")
        + (f"，确认中 {result['pending']} 台" if result["pending"] else "")
    )
    for e in result["errors"]:
        print(f"  查询失败: {e}")


if __name__ == "__main__":
    main()
