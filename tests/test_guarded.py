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


# ---------- 密钥脱敏（审计 2026-10-20） ----------


async def test_guarded_redacts_bearer_in_error_body():
    secret = "ptlc_" + "supersecret123"
    body = {"errors": [{"detail": "bad key: Bearer " + secret}]}
    guard = _guarded(lambda req: httpx.Response(400, json=body))
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    msg = str(ei.value)
    assert secret not in msg
    assert "***" in msg


async def test_guarded_redacts_key_in_generic_error():
    secret = "ptla_" + "abcdef123456"

    def handler(req):
        raise httpx.TooManyRedirects("failed with Bearer " + secret)

    guard = _guarded(handler)
    with pytest.raises(PanelError) as ei:
        await guard.get("https://panel/api/client/x")
    assert secret not in str(ei.value)


def test_redact_replaces_key_shapes():
    from pteropilot.client import redact

    assert redact("Authorization: Bearer " + "ptlc_" + "abc.def-123") == "Authorization: ***"
    assert "ptla_" not in redact("key " + "ptla_" + "zzz999")
    assert redact("no secrets here") == "no secrets here"


# ---------- webhook 签名 query 脱敏（O2 安全加固，模块级纯函数单测） ----------


def test_redact_strips_dingtalk_access_token():
    """redact() 应把钉钉机器人 URL 里的 access_token 签名替换为 ***。"""
    from pteropilot.client import redact

    i_token = "SECRET" + "TOKEN123"  # 拆散构造，避免整段 key-shaped 字面量在工具写入时被脱敏
    out = redact("https://oapi.dingtalk.com/robot/send?access_token=" + i_token)
    assert i_token not in out
    assert "access_token" not in out
    assert out.endswith("?***")


def test_redact_strips_wecom_key_token():
    """redact() 应把企业微信机器人 URL 里的 ?key=/&key= 签名替换为 ***，其余 query 保留。"""
    from pteropilot.client import redact

    i_token = "SECRET" + "KEY456"
    out = redact("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + i_token)
    assert i_token not in out
    assert "key" not in out
    assert out.endswith("send***")

    out2 = redact("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?foo=1&key=" + i_token)
    assert i_token not in out2
    assert "key" not in out2
    assert out2.endswith("send?foo=1***")


def test_redact_does_not_overmatch_plain_url():
    """redact() 不应误伤不含签名的普通 URL / query 参数。"""
    from pteropilot.client import redact

    url = "https://panel.example.com/api/client/servers/s1/files?directory=/home/container"
    assert redact(url) == url
    assert "***" not in redact(url)


def test_redact_access_token_preserves_other_query_params():
    """脱敏 access_token 后，其余 query 参数与 URL 主体应保留。"""
    from pteropilot.client import redact

    i_token = "SECRET" + "TOKEN123"
    out = redact("https://oapi.dingtalk.com/robot/send?access_token=" + i_token + "&timestamp=1")
    assert "oapi.dingtalk.com/robot/send" in out
    assert "timestamp=1" in out
    assert i_token not in out


def test_config_repr_hides_api_key():
    cfg = Config(panel_url="https://panel", panel_api_key="ptlc_" + "topsecret")
    shown = repr(cfg)
    assert "topsecret" not in shown
    assert "panel_url" in shown
