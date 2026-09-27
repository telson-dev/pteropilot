"""从环境变量读取配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    """PteroPilot 运行配置。"""

    panel_url: str = field(default_factory=lambda: os.environ.get("PANEL_URL", ""))
    panel_api_key: str = field(default_factory=lambda: os.environ.get("PANEL_API_KEY", ""))
    readonly: bool = field(default_factory=lambda: os.environ.get("PANEL_READONLY", "0") == "1")

    def validate(self) -> None:
        if not self.panel_url or not self.panel_api_key:
            raise ValueError("需要设置 PANEL_URL 和 PANEL_API_KEY 环境变量")
