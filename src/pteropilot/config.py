"""从环境变量读取配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# 两套面板的密钥前缀（实测：Pterodactyl ptla_/ptlc_，Pelican papp_/pacc_）
CLIENT_KEY_PREFIXES = ("ptlc_", "pacc_")
APP_KEY_PREFIXES = ("ptla_", "papp_")


@dataclass
class Config:
    """PteroPilot 运行配置。"""

    panel_url: str = field(default_factory=lambda: os.environ.get("PANEL_URL", ""))
    panel_api_key: str = field(
        default_factory=lambda: os.environ.get("PANEL_API_KEY", ""), repr=False
    )
    panel_app_key: str = field(
        default_factory=lambda: os.environ.get("PANEL_APP_KEY", ""), repr=False
    )
    readonly: bool = field(default_factory=lambda: os.environ.get("PANEL_READONLY", "0").strip().lower() in ("1", "true", "yes", "on"))

    def validate(self) -> None:
        if not self.panel_url or not self.panel_api_key:
            raise ValueError("需要设置 PANEL_URL 和 PANEL_API_KEY 环境变量")


def key_role_warnings(cfg: Config) -> list[str]:
    """密钥角色诊断：两套面板客户端/应用密钥分开，单 key 会在部分工具上失败。

    返回非致命提示列表（不阻断启动）。
    """
    warnings: list[str] = []
    api = cfg.panel_api_key or ""
    app = cfg.panel_app_key or ""

    if api.startswith(APP_KEY_PREFIXES):
        warnings.append(
            "提示：PANEL_API_KEY 是应用密钥（前缀 %s），而核心工具（电源/控制台/文件/备份）"
            "走客户端 API，两套面板上都会 403。请改用客户端密钥（ptlc_ 或 pacc_）作为 PANEL_API_KEY。"
            % api[:5]
        )
    if not app and api.startswith(CLIENT_KEY_PREFIXES):
        warnings.append(
            "提示：PANEL_API_KEY 是客户端密钥（前缀 %s），未设置 PANEL_APP_KEY。"
            "list_nodes / list_all_servers / list_users 走应用 API：Pterodactyl 上会 403，"
            "Pelican 上会返回错误结果。建议在面板生成应用密钥并设 PANEL_APP_KEY。"
            % api[:5]
        )
    if app and app.startswith(CLIENT_KEY_PREFIXES):
        warnings.append(
            "提示：PANEL_APP_KEY 看起来是客户端密钥（前缀 %s），应用 API 调用可能失败，"
            "请使用应用密钥（ptla_ 或 papp_）。" % app[:5]
        )
    if api and not (api.startswith(CLIENT_KEY_PREFIXES) or api.startswith(APP_KEY_PREFIXES)):
        warnings.append(
            "提示：PANEL_API_KEY 前缀无法识别（应为 ptlc_/ptla_ 或 pacc_/papp_），"
            "请检查密钥是否复制完整。"
        )
    return warnings
