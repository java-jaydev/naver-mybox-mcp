"""Tests for the MYBOX client, driven by a mock transport.

These never touch the network. They pin the parts of the protocol that are easy to get
wrong: cursor paging, the two-step upload and download, KST date widening, and the
error surface.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mybox_mcp.client import MyboxClient, MyboxError

TOKEN = "mbx_pat_test"


def make_client(handler, **kwargs) -> MyboxClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    # Pace fast so the suite does not sleep for real.
    return MyboxClient(TOKEN, client=http, rates={"search": 6000.0, "default": 6000.0}, **kwargs)


async def test_list_follows_cursor_and_sends_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json={
                "resources": [{"resourceId": "A", "name": "one.jpg", "type": "file"}],
                "responseMetaData": {"nextCursor": "page2"}})
        return httpx.Response(200, json={
            "resources": [{"resourceId": "B", "name": "two.jpg", "type": "file"}],
            "responseMetaData": {}})

    async with make_client(handler) as client:
        items = await client.list_resources()

    assert [i["resourceId"] for i in items] == ["A", "B"]
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert seen[0].url.path == "/v1/drive/resources"
    assert seen[1].url.params["cursor"] == "page2"


async def test_list_of_folder_uses_folder_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/drive/folders/F1/resources"
        return httpx.Response(200, json={"resources": [], "responseMetaData": {}})

    async with make_client(handler) as client:
        assert await client.list_resources("F1") == []


async def test_search_widens_dates_to_kst_day():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(200, json={"resources": [], "responseMetaData": {}})

    async with make_client(handler) as client:
        await client.search(category="image", start_date="2026-09-10", end_date="2026-09-11")

    assert captured["startDate"] == "2026-09-10T00:00:00+09:00"
    assert captured["endDate"] == "2026-09-11T23:59:59+09:00"
    assert captured["dateField"] == "created"


async def test_search_requires_a_filter():
    async with make_client(lambda r: httpx.Response(200, json={})) as client:
        with pytest.raises(MyboxError, match="at least one"):
            await client.search()


async def test_search_rejects_unknown_category():
    async with make_client(lambda r: httpx.Response(200, json={})) as client:
        with pytest.raises(MyboxError, match="category must be"):
            await client.search(category="photos")


async def test_download_is_two_steps_and_writes_file(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/drive/resources/R1":
            return httpx.Response(200, json={"resourceId": "R1", "name": "a.jpg", "size": 5})
        if request.url.path == "/v1/drive/files/R1/download":
            return httpx.Response(200, json={"downloadUrl": "https://storage.example/x",
                                             "expiresIn": 600})
        assert str(request.url) == "https://storage.example/x"
        # The signed URL must not carry the bearer token.
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=b"hello")

    async with make_client(handler) as client:
        result = await client.download("R1", tmp_path)

    assert result["skipped"] is False and result["bytes"] == 5
    written = tmp_path / "a.jpg"
    assert written.read_bytes() == b"hello"
    assert not list(tmp_path.glob("*.part"))


async def test_download_skips_when_size_matches(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"hello")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"resourceId": "R1", "name": "a.jpg", "size": 5})

    async with make_client(handler) as client:
        result = await client.download("R1", tmp_path)

    assert result["skipped"] is True
    assert calls == ["/v1/drive/resources/R1"]  # no download ticket was spent


async def test_upload_posts_ticket_then_filedata(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"1234567890")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/drive/files":
            bodies.append(json.loads(request.content))
            return httpx.Response(201, json={"offset": 0,
                                             "uploadUrl": "https://storage.example/up"})
        assert str(request.url) == "https://storage.example/up"
        assert b'name="Filedata"' in request.content
        assert b"1234567890" in request.content
        return httpx.Response(200, json={})

    async with make_client(handler) as client:
        result = await client.upload(source, parent_id="F9", overwrite=True)

    assert bodies[0] == {"fileName": "photo.jpg", "fileSize": 10,
                         "isOverwrite": True, "parentId": "F9"}
    assert result["bytes"] == 10


async def test_rate_limit_error_is_explicit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "PLAT-429", "message": "TOO_MANY_REQUESTS"})

    async with make_client(handler) as client:
        with pytest.raises(MyboxError, match="rate limit") as caught:
            await client.storage()
    assert caught.value.status == 429


async def test_api_error_carries_code_and_request_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "PLAT-404", "message": "NOT_FOUND",
                                         "requestId": "abc-123"})

    async with make_client(handler) as client:
        with pytest.raises(MyboxError) as caught:
            await client.info("missing")
    assert caught.value.code == "PLAT-404"
    assert caught.value.request_id == "abc-123"


async def test_missing_token_is_explained(monkeypatch, tmp_path):
    monkeypatch.delenv("MYBOX_PAT", raising=False)
    monkeypatch.setattr("mybox_mcp.client.TOKEN_FILE", tmp_path / "nope")
    with pytest.raises(MyboxError, match="personal access token"):
        MyboxClient()
