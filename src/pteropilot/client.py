"""Pterodactyl / Pelican 面板 API 客户端。

实现 Pterodactyl Client/Application API 的常用端点；
Pelican 与 Pterodactyl API 同源，绝大多数调用可直接复用。

list 端点统一返回 data 数组；httpx 在 4xx/5xx 时抛出 HTTPStatusError。
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import Config

POWER_SIGNALS = ("start", "stop", "restart", "kill")


class PanelError(RuntimeError):
    """可预期的面板调用错误。"""


class PanelClient:
    """面板 API 异步客户端。"""

    def __init__(self, config: Config, timeout: float = 30.0) -> None:
        base = config.panel_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {config.panel_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.app = httpx.AsyncClient(
            base_url=f"{base}/api/application", headers=headers, timeout=timeout
        )
        self.client = httpx.AsyncClient(
            base_url=f"{base}/api/client", headers=headers, timeout=timeout
        )

    async def aclose(self) -> None:
        await self.app.aclose()
        await self.client.aclose()

    # ---- 分页 ----

    @staticmethod
    async def _paginated(get: httpx.AsyncClient, path: str) -> list[dict]:
        """遍历分页端点，聚合全部 data。"""
        data: list[dict] = []
        params: dict[str, Any] = {"per_page": 100, "page": 1}
        while True:
            r = await get.get(path, params=params)
            r.raise_for_status()
            payload = r.json()
            data.extend(payload.get("data", []))
            pagination = (payload.get("meta") or {}).get("pagination") or {}
            total_pages = pagination.get("total_pages", 1)
            if params["page"] >= total_pages:
                break
            params["page"] += 1
        return data

    # ================= Application API（管理员） =================

    async def list_servers(self) -> list[dict]:
        return await self._paginated(self.app, "/servers")

    async def list_nodes(self) -> list[dict]:
        return await self._paginated(self.app, "/nodes")

    async def list_users(self) -> list[dict]:
        return await self._paginated(self.app, "/users")

    # ================= Client API：服务器与状态 =================

    async def client_servers(self) -> list[dict]:
        r = await self.client.get("")
        r.raise_for_status()
        payload = r.json()
        if isinstance(payload, dict):
            return payload.get("data", [])
        return payload

    async def resources(self, server_id: str) -> dict:
        r = await self.client.get(f"/servers/{server_id}/resources")
        r.raise_for_status()
        payload = r.json()
        return payload.get("attributes", payload)

    # ================= 电源 / 控制台 =================

    async def power(self, server_id: str, signal: str) -> None:
        if signal not in POWER_SIGNALS:
            raise PanelError(f"非法电源信号: {signal}")
        r = await self.client.post(
            f"/servers/{server_id}/power", json={"signal": signal}
        )
        r.raise_for_status()

    async def send_command(self, server_id: str, command: str) -> None:
        r = await self.client.post(
            f"/servers/{server_id}/command", json={"command": command}
        )
        r.raise_for_status()

    # ================= 文件管理 =================

    async def list_files(self, server_id: str, directory: str = "/") -> list[dict]:
        r = await self.client.get(
            f"/servers/{server_id}/files/list", params={"directory": directory}
        )
        r.raise_for_status()
        payload = r.json()
        return payload.get("attributes", {}).get("files") or payload.get("data", [])

    async def read_file(self, server_id: str, path: str) -> str:
        r = await self.client.get(
            f"/servers/{server_id}/files/contents", params={"file": path}
        )
        r.raise_for_status()
        return r.text

    async def write_file(self, server_id: str, path: str, content: str) -> None:
        r = await self.client.post(
            f"/servers/{server_id}/files/write",
            params={"file": path},
            content=content,
            headers={"Content-Type": "text/plain"},
        )
        r.raise_for_status()

    async def rename_file(
        self, server_id: str, directory: str, old_name: str, new_name: str
    ) -> None:
        r = await self.client.put(
            f"/servers/{server_id}/files/rename",
            json={"directory": directory, "from": old_name, "to": new_name},
        )
        r.raise_for_status()

    async def delete_files(self, server_id: str, root: str, paths: list[str]) -> None:
        r = await self.client.post(
            f"/servers/{server_id}/files/delete",
            json={"root": root, "files": paths},
        )
        r.raise_for_status()

    async def download_url(self, server_id: str, path: str) -> str:
        r = await self.client.get(
            f"/servers/{server_id}/files/download", params={"file": path}
        )
        r.raise_for_status()
        return r.json().get("attributes", {}).get("url", "")

    # ================= 备份 =================

    async def list_backups(self, server_id: str) -> list[dict]:
        r = await self.client.get(f"/servers/{server_id}/backups")
        r.raise_for_status()
        return r.json().get("data", [])

    async def create_backup(self, server_id: str) -> dict:
        r = await self.client.post(f"/servers/{server_id}/backups")
        r.raise_for_status()
        return r.json().get("attributes", r.json())

    async def restore_backup(self, server_id: str, backup_id: str) -> None:
        r = await self.client.post(
            f"/servers/{server_id}/backups/{backup_id}/restore"
        )
        r.raise_for_status()

    async def delete_backup(self, server_id: str, backup_id: str) -> None:
        r = await self.client.delete(f"/servers/{server_id}/backups/{backup_id}")
        r.raise_for_status()
