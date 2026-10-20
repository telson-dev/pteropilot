"""Pterodactyl / Pelican 面板 API 客户端。

实现 Pterodactyl Client/Application API 的常用端点；
Pelican 与 Pterodactyl API 同源，本文档所用的全部端点与 payload
已对照 Pelican 源码（routes/ 与 Http/Controllers/Api/）逐一核实，可直接复用。

list 端点统一返回 data 数组；httpx 在 4xx/5xx 时抛出 HTTPStatusError。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .config import Config

POWER_SIGNALS = ("start", "stop", "restart", "kill")

# 可能被写进错误信息/日志的密钥形态，统一脱敏：
# Bearer 头，以及 Pterodactyl / Pelican 的 API Key 前缀。
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]+"),
    re.compile(r"\b(?:ptlc|ptla|peli)_[A-Za-z0-9_\-]+"),
)


def redact(text: str) -> str:
    """把文本里的 API Key / Bearer 令牌替换成 ***，供错误信息与日志复用。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


class PanelError(RuntimeError):
    """可预期的面板调用错误。"""


class _Guarded:
    """httpx 客户端的薄包装：把网络异常和 HTTP 状态码翻译成中文提示。

    18 个工具因此不必各自 try/except，AI 拿到的都是能直接读懂（也能直接转述
    给用户）的中文说明，而不是 httpx 的英文原文。

    HTTP_STATUS：常见状态码 → 人类可读的原因 + 排查方向。
    """

    HTTP_STATUS = {
        401: "API Key 无效或已被撤销（401）。请检查 PANEL_API_KEY 是否填对、是否已被删除。",
        403: "这个 API Key 没有该项权限（403）。可能是面板管理员 Key 与用户 Key 用混了。",
        404: "面板上找不到这个目标（404）。请检查 PANEL_URL 或服务器标识是否正确。",
        405: "面板不支持这个操作（405）。可能是面板版本较旧或端点被禁用。",
        422: "面板认为请求参数不合法（422）。请检查传入的路径、名称或信号值。",
        429: "请求过于频繁被限流（429）。稍等一会儿再试。",
    }

    def __init__(self, inner: httpx.AsyncClient) -> None:
        self._inner = inner

    def _speak(self, r: httpx.Response) -> httpx.Response:
        """只放行成功响应，其余一律转成中文 PanelError。"""
        if r.is_success:
            return r
        known = self.HTTP_STATUS.get(r.status_code)
        if known:
            raise PanelError(f"面板拒绝了请求：{known}")
        if r.status_code >= 500:
            raise PanelError(
                f"面板自身出错（HTTP {r.status_code}），通常是面板服务暂时异常，"
                "稍后重试即可。"
            )
        detail = redact((r.text or "").strip())
        raise PanelError(
            f"面板返回 HTTP {r.status_code}"
            + (f"。原文：{detail[:160]}" if detail else "。")
        )

    async def _ask(self, method: str, *args, **kwargs) -> httpx.Response:
        try:
            r = await getattr(self._inner, method)(*args, **kwargs)
        except httpx.ConnectError as exc:
            raise PanelError(
                "连不上面板。请检查 PANEL_URL 是否正确（要带 https://）、"
                "面板是否在线、网络是否可达。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise PanelError(
                "面板响应超时（30 秒）。面板可能负载过高或网络不稳，稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise PanelError(f"与面板通信失败：{redact(str(exc))}") from exc
        return self._speak(r)

    async def get(self, *args, **kwargs) -> httpx.Response:
        return await self._ask("get", *args, **kwargs)

    async def post(self, *args, **kwargs) -> httpx.Response:
        return await self._ask("post", *args, **kwargs)

    async def put(self, *args, **kwargs) -> httpx.Response:
        return await self._ask("put", *args, **kwargs)

    async def delete(self, *args, **kwargs) -> httpx.Response:
        return await self._ask("delete", *args, **kwargs)

    async def aclose(self) -> None:
        await self._inner.aclose()


class PanelClient:
    """面板 API 异步客户端。"""

    def __init__(self, config: Config, timeout: float = 30.0) -> None:
        base = config.panel_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {config.panel_api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = timeout
        self.app = _Guarded(
            httpx.AsyncClient(
                base_url=f"{base}/api/application", headers=headers, timeout=timeout
            )
        )
        self.client = _Guarded(
            httpx.AsyncClient(
                base_url=f"{base}/api/client", headers=headers, timeout=timeout
            )
        )
        # daemon（wings）直连客户端：上传/下载文件打签名 URL，无 Bearer 认证。
        self.daemon = _Guarded(httpx.AsyncClient(timeout=timeout))

    async def aclose(self) -> None:
        await self.app.aclose()
        await self.client.aclose()
        await self.daemon.aclose()

    # ---- 分页 ----

    @staticmethod
    async def _paginated(get: _Guarded, path: str) -> list[dict]:
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
        return await self._paginated(self.client, "")

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

    # ---- 控制台 ----

    async def console_log(self, server_id: str) -> str:
        """读取服务器控制台最近一段日志（Pelican/Pterodactyl 均返回纯文本）。"""
        r = await self.client.get(f"/servers/{server_id}/console")
        r.raise_for_status()
        return r.text

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
        # 两个面板的约定一致：{root, files: [{from, to}]}，支持批量重命名/移动。
        r = await self.client.put(
            f"/servers/{server_id}/files/rename",
            json={"root": directory, "files": [{"from": old_name, "to": new_name}]},
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

    async def create_folder(self, server_id: str, directory: str, name: str) -> None:
        r = await self.client.post(
            f"/servers/{server_id}/files/create-folder",
            json={"root": directory, "name": name},
        )
        r.raise_for_status()

    async def upload_url(self, server_id: str) -> str:
        r = await self.client.get(f"/servers/{server_id}/files/upload")
        r.raise_for_status()
        return r.json().get("attributes", {}).get("url", "")

    async def upload_file(
        self, server_id: str, directory: str, filename: str, content: bytes
    ) -> None:
        """两步上传：面板签发签名 URL，再直连 daemon multipart 上传。"""
        url = await self.upload_url(server_id)
        if not url:
            raise PanelError("面板未返回上传 URL")
        r = await self.daemon.post(
            url, params={"directory": directory}, files={"files": (filename, content)}
        )
        r.raise_for_status()

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
