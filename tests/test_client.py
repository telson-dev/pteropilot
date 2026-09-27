"""PanelClient 端点与分页测试。"""

from __future__ import annotations

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
        {"attributes": {"resources": {"current_state": "running", "cpu_absolute": 12.5}}},
    )
    res = await client.resources("s1")
    assert res["resources"]["current_state"] == "running"


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
