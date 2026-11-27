from urllib.parse import quote

import pytest
from aiohttp import web
from yarl import URL

import folder_paths
from app.user_manager import UserManager
from comfy.cli_args import args

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(folder_paths, "user_directory", str(tmp_path / "user"))
    monkeypatch.setattr(args, "multi_user", False)
    routes = web.RouteTableDef()
    UserManager().add_routes(routes)
    app = web.Application()
    app.add_routes(routes)
    return app


@pytest.mark.parametrize("encoded", [True, False])
async def test_nested_userdata_round_trip(aiohttp_client, app, encoded):
    client = await aiohttp_client(app)
    source = "workflows/scenes/shot one.json"
    destination = "workflows/final/shot two.json"
    source_url = quote(source, safe="" if encoded else "/")
    destination_url = quote(destination, safe="" if encoded else "/")
    content = {"version": 0.4, "nodes": []}

    response = await client.post("/userdata/" + source_url, json=content)
    assert response.status == 200
    response = await client.get("/userdata/" + source_url)
    assert response.status == 200
    assert await response.json() == content
    assert response.headers["Content-Disposition"] == "attachment"
    assert response.headers["X-Content-Type-Options"] == "nosniff"

    response = await client.post("/userdata/" + source_url + "/move/" + destination_url)
    assert response.status == 200
    assert await response.json() == destination
    assert (await client.get("/userdata/" + source_url)).status == 404
    response = await client.get("/userdata/" + destination_url)
    assert await response.json() == content
    assert (await client.delete("/userdata/" + destination_url)).status == 204
    assert (await client.get("/userdata/" + destination_url)).status == 404


@pytest.mark.parametrize("method", ["get", "post", "delete"])
async def test_decoded_userdata_cannot_escape_user_root(aiohttp_client, app, tmp_path, method):
    outside = tmp_path / "user" / "outside.txt"
    outside.write_text("private")
    client = await aiohttp_client(app)
    url = URL("/userdata/workflows/%2e%2e/%2e%2e/outside.txt", encoded=True)
    response = await client.request(method, url, data=b"replacement" if method == "post" else None)
    assert response.status == 403
    assert outside.read_text() == "private"


async def test_decoded_move_cannot_escape_user_root(aiohttp_client, app):
    client = await aiohttp_client(app)
    assert (await client.post("/userdata/workflows/test.json", json={})).status == 200
    url = URL("/userdata/workflows/test.json/move/workflows/%2e%2e/%2e%2e/outside.json", encoded=True)
    assert (await client.post(url)).status == 403
    assert (await client.get("/userdata/workflows/test.json")).status == 200
