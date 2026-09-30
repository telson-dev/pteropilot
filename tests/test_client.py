"""PanelClient 端点与分页测试。"""

from __future__ import annotations

import json

import httpx
import pytest

from pteropilot.client import PanelClient, PanelError
from pteropilot.config import Config


@pytest.fixture
def client(fake) -> PanelClient:
    return PanelClient(Config(panel_url="https://panel", panel_api_key="k"))


async def test_list_servers_pagination(client, fake):
    page1 = {
        "data": [{"attributes": {"id": 1, "identifier": "a1"}}],
        "meta": {"pagination": {"current_page": 1, "total_pages": 2}},
    }
    page2 = {
        "data": [{"attributes": {"id": 2, "identifier": "a2"}}],
        "meta": {"pagination": {"current_page": 2, "total_pages": 2}},
    }
    fake.route("GET", "/servers", [page1, page2])
    servers = await client.list_servers()
    assert len(servers) == 2
    assert servers[0]["attributes"]["identifier"] == "a1"
    assert servers[1]["attributes"]["identifier"] == "a2"


async def test_resources(client, fake):
    fake.route(
        "GET",
        "/resources",
        {"attributes": {"current_state": "running", "resources": {"cpu_absolute": 12.5}}},
    )
    res = await client.resources("s1")
    # current_state 在 attributes 顶层，resources 是它的子对象（Pelican/Pterodactyl 均如此）
    assert res["current_state"] == "running"
    assert res["resources"]["cpu_absolute"] == 12.5


async def test_list_files_pelican_shape(client, fake):
    fake.route(
        "GET",
        "/files/list",
        {
            "object": "list",
            "data": [
                {"object": "file_object", "attributes": {"name": "config.yml", "size": 12, "is_file": True, "mimetype": "text/plain"}},
                {"object": "file_object", "attributes": {"name": "plugins", "size": None, "is_file": False, "mimetype": "inode/directory"}},
            ],
        },
    )
    files = await client.list_files("s1", "/")
    assert [f["attributes"]["name"] for f in files] == ["config.yml", "plugins"]
    assert files[1]["attributes"]["is_file"] is False


async def test_rename_file_payload(client, fake):
    fake.route("PUT", "/files/rename", {})
    await client.rename_file("s1", "/", "old.txt", "new.txt")
    put = [r for r in fake.requests if r.method == "PUT" and "/files/rename" in str(r.url)]
    assert len(put) == 1
    assert json.loads(put[0].content) == {"root": "/", "files": [{"from": "old.txt", "to": "new.txt"}]}


async def test_delete_files_payload(client, fake):
    fake.route("POST", "/files/delete", {})
    await client.delete_files("s1", "/", ["/a.txt", "/b.txt"])
    post = [r for r in fake.requests if r.method == "POST" and "/files/delete" in str(r.url)]
    assert len(post) == 1
    assert json.loads(post[0].content) == {"root": "/", "files": ["/a.txt", "/b.txt"]}


async def test_client_servers_pagination(client, fake):
    p1 = {
        "object": "list",
        "data": [{"attributes": {"identifier": "s1"}}],
        "meta": {"pagination": {"current_page": 1, "total_pages": 2}},
    }
    p2 = {
        "object": "list",
        "data": [{"attributes": {"identifier": "s2"}}],
        "meta": {"pagination": {"current_page": 2, "total_pages": 2}},
    }
    fake.route("GET", "/api/client", [p1, p2])
    servers = await client.client_servers()
    assert [s["attributes"]["identifier"] for s in servers] == ["s1", "s2"]


async def test_power_invalid_signal(client, fake):
    with pytest.raises(PanelError):
        await client.power("s1", "explode")


async def test_power_posts(client, fake):
    fake.route("POST", "/power", {})
    await client.power("s1", "restart")
    post = [r for r in fake.requests if r.method == "POST" and "/power" in str(r.url)]
    assert len(post) == 1


async def test_read_file_text(client, fake):
    fake.route("GET", "/files/contents", httpx.Response(200, text="hello world"))
    assert await client.read_file("s1", "/a.txt") == "hello world"


async def test_http_error_propagates(client, fake):
    fake.route("GET", "/backups", httpx.Response(500, json={"errors": []}))
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_backups("s1")
