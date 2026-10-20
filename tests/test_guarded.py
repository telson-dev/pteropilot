"""_Guarded 翻译层、Config 校验与其余未覆盖端点的测试。

_Guarded 是本项目把 httpx 网络异常 / HTTP 状态码翻译成中文提示的核心层，
conftest 里 fake_init 用裸 AsyncClient 绕过了它，故这里单独用 MockTransport
直接测 _Guarded 自身。
"""

from __future__ import annotations

import httpx
import pytest

from pteropilot.client import PanelClient, PanelError, _Guarded
from pteropilot.config import Config


def _guarded(handler) -> _Guarded:
    return _Guarded(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


# ---------- _Guarded 错误翻译 ----------


@pytest.mark.parametrize(
    "status, keyword",
    [
        (401, "API Key"),
        (403, "权限"),
        (404, "找不到"),
        (405, "不支持"),
        (422, "不合法"),
        (429, "限流"),
        (502, "面板自身出错"),
    ],
)
async def test_guarded_status_translates(status, keyword):
    guard = _guarded(lambda req: httpx.Response(status, json={"errors": []}))
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert keyword in str(ei.value)


async def test_guarded_unexpected_4xx_with_detail():
    guard = _guarded(lambda req: httpx.Response(418, json={"errors": [{"detail": "teapot"}]}))
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert "HTTP 418" in str(ei.value) and "teapot" in str(ei.value)


async def test_guarded_4xx_empty_body():
    guard = _guarded(lambda req: httpx.Response(418))
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert "HTTP 418" in str(ei.value) and "原文" not in str(ei.value)


async def test_guarded_connect_error():
    def handler(req):
        raise httpx.ConnectError("boom")

    guard = _guarded(handler)
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert "连不上面板" in str(ei.value)


async def test_guarded_timeout():
    def handler(req):
        raise httpx.ReadTimeout("slow")

    guard = _guarded(handler)
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert "超时" in str(ei.value)


async def test_guarded_generic_http_error():
    def handler(req):
        raise httpx.TooManyRedirects("loop")

    guard = _guarded(handler)
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert "通信失败" in str(ei.value)


async def test_guarded_success_passthrough():
    guard = _guarded(lambda req: httpx.Response(200, json={"ok": True}))
    r = await guard.get("https://panel/api/client/x")
    assert r.json() == {"ok": True}


@pytest.mark.parametrize("method", ["post", "put", "delete"])
async def test_guarded_wraps_all_verbs(method):
    guard = _guarded(lambda req: httpx.Response(200, json={"ok": True}))
    r = await getattr(guard, method)("https://panel/api/client/x")
    assert r.status_code == 200


async def test_guarded_aclose():
    guard = _guarded(lambda req: httpx.Response(200, json={"ok": True}))
    await guard.aclose()


# ---------- 其余端点（走 conftest fake 的裸客户端） ----------


async def test_list_nodes(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route(
        "GET",
        "/nodes",
        {
            "data": [{"attributes": {"id": 1, "name": "n1", "fqdn": "n1.example.com"}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
    )
    nodes = await client.list_nodes()
    assert nodes[0]["attributes"]["name"] == "n1"


async def test_list_users(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route(
        "GET",
        "/users",
        {
            "data": [{"attributes": {"id": 1, "username": "alice"}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
    )
    users = await client.list_users()
    assert users[0]["attributes"]["username"] == "alice"


async def test_create_backup(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("POST", "/backups", {"attributes": {"uuid": "bk9"}})
    b = await client.create_backup("s1")
    assert b["uuid"] == "bk9"


async def test_create_backup_no_attributes(fake):
    # 面板偶尔把属性直接平铺返回，此时退回整个 json
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("POST", "/backups", {"uuid": "bk-plain"})
    b = await client.create_backup("s1")
    assert b["uuid"] == "bk-plain"


async def test_restore_backup(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("POST", "/restore", {})
    await client.restore_backup("s1", "bk1")
    assert any(r.method == "POST" and "/restore" in str(r.url) for r in fake.requests)


async def test_delete_backup(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("DELETE", "/backups", {})
    await client.delete_backup("s1", "bk1")
    assert any(r.method == "DELETE" and "/backups" in str(r.url) for r in fake.requests)


async def test_upload_file_no_url(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("GET", "/files/upload", {"object": "signed_url", "attributes": {"url": ""}})
    with pytest.raises(PanelError):
        await client.upload_file("s1", "/", "a.jar", b"x")


async def test_list_files_empty_attributes_shape(fake):
    client = PanelClient(Config(panel_url="https://panel", panel_api_key="k"))
    fake.route("GET", "/files/list", {"attributes": {"files": [{"attributes": {"name": "a.txt"}}]}})
    files = await client.list_files("s1", "/")
    assert files[0]["attributes"]["name"] == "a.txt"


# ---------- Config ----------


def test_config_validate_ok():
    Config(panel_url="https://panel", panel_api_key="k").validate()


def test_config_validate_missing():
    with pytest.raises(ValueError):
        Config(panel_url="", panel_api_key="").validate()


def test_config_validate_missing_key():
    with pytest.raises(ValueError):
        Config(panel_url="https://panel", panel_api_key="").validate()