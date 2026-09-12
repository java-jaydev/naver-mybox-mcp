"""Async client for the NAVER MYBOX Open API.

Reference: https://developers.mybox.naver.com/ (read 2026-09-12)

Shape of the API, in short:
  * Base URL    https://open-api.mybox.naver.com/v1
  * Auth        personal access token, ``Authorization: Bearer mbx_pat_...`` (not OAuth)
  * Upload      two steps - ``POST /drive/files`` returns an ``uploadUrl`` (valid 48h,
                single use); the bytes go to that URL as multipart field ``Filedata``
  * Download    two steps - ``GET /drive/files/{id}/download`` returns a ``downloadUrl``
                (valid 10 minutes, single use)
  * Paging      ``responseMetaData.nextCursor`` is fed back as ``cursor``

Quotas are per plan and the docs warn that abuse may be blocked without notice, so this
client paces itself below the lowest documented per-minute limits.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://open-api.mybox.naver.com/v1"

#: Calls per minute this client allows itself, per API family. The documented limits are
#: 10/min for search and 60/min for everything else on the smallest plan.
DEFAULT_RATES: dict[str, float] = {"search": 9.0, "default": 55.0}

CATEGORIES = ("image", "video", "audio", "document", "archive", "executable", "etc")

TOKEN_ENV = "MYBOX_PAT"
TOKEN_FILE = Path.home() / ".mybox" / "token"


class MyboxError(RuntimeError):
    """An API call failed. Carries the MYBOX error code when there was one."""

    def __init__(self, message: str, *, status: int | None = None,
                 code: str | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id


def read_token(explicit: str | None = None) -> str:
    """Token from the argument, then ``MYBOX_PAT``, then ``~/.mybox/token``.

    A token is created in the MYBOX web UI (Settings > account and personal access token
    management). It is shown once, lasts 30/60/90/180 days, and five may exist per account.
    """
    token = (explicit or os.environ.get(TOKEN_ENV, "")).strip()
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise MyboxError(
            "No MYBOX personal access token. Create one in the MYBOX web UI under "
            "Settings > account and personal access token management, then set the "
            f"{TOKEN_ENV} environment variable or write it to {TOKEN_FILE}."
        )
    return token


@dataclass
class Pacer:
    """Smallest useful rate limiter: one timestamp per API family."""

    rates: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RATES))
    _last: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def wait(self, kind: str) -> None:
        async with self._lock:
            gap = 60.0 / self.rates.get(kind, self.rates["default"])
            previous = self._last.get(kind)
            if previous is not None:
                remaining = gap - (time.monotonic() - previous)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last[kind] = time.monotonic()


class MyboxClient:
    """Thin, typed wrapper over the endpoints this package exposes.

    Deliberately missing: delete, trash and trash-emptying. Those are destructive and are
    left to the MYBOX web UI. See README.
    """

    def __init__(self, token: str | None = None, *, base_url: str = BASE_URL,
                 client: httpx.AsyncClient | None = None,
                 rates: dict[str, float] | None = None, timeout: float = 60.0) -> None:
        self._token = read_token(token)
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self.pacer = Pacer(dict(rates or DEFAULT_RATES))

    async def __aenter__(self) -> "MyboxClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- plumbing ---------------------------------------------------------------
    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "User-Agent": "mybox-mcp"}

    async def request(self, method: str, path: str, *, kind: str = "default",
                      **kwargs: Any) -> dict[str, Any]:
        await self.pacer.wait(kind)
        response = await self._client.request(
            method, self.base_url + path, headers=self._auth_headers, **kwargs)
        if response.status_code == 429:
            raise MyboxError(
                "429 - MYBOX rate limit reached. Limits reset per minute and per day; "
                "retrying immediately will not help.", status=429, code="PLAT-429")
        if response.is_error:
            code = message = request_id = None
            try:
                body = response.json()
                code, message = body.get("code"), body.get("message")
                request_id = body.get("requestId")
            except ValueError:
                message = response.text[:300]
            raise MyboxError(f"{response.status_code} {code or ''} {message or ''}".strip(),
                             status=response.status_code, code=code, request_id=request_id)
        return response.json() if response.content else {}

    async def _paged(self, path: str, params: dict[str, Any], *, kind: str = "default",
                     max_items: int | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            payload = await self.request("GET", path, kind=kind, params=query)
            items.extend(payload.get("resources", []))
            cursor = (payload.get("responseMetaData") or {}).get("nextCursor")
            if not cursor or (max_items is not None and len(items) >= max_items):
                break
        return items[:max_items] if max_items is not None else items

    # -- endpoints --------------------------------------------------------------
    async def storage(self) -> dict[str, Any]:
        """Quota, used bytes, per-category file counts, max upload size."""
        return await self.request("GET", "/drive/storage")

    async def list_resources(self, folder_id: str | None = None, *, count: int = 100,
                             sort: str = "modifiedAt,desc",
                             max_items: int | None = None) -> list[dict[str, Any]]:
        """Entries directly inside a folder, or the drive root when no folder is given."""
        path = "/drive/resources" if not folder_id else f"/drive/folders/{folder_id}/resources"
        return await self._paged(path, {"count": min(count, 1000), "sort": sort},
                                 max_items=max_items)

    async def search(self, *, query: str | None = None, category: str | None = None,
                     start_date: str | None = None, end_date: str | None = None,
                     date_field: str = "created", parent_path: str | None = None,
                     max_items: int | None = 200) -> list[dict[str, Any]]:
        """Search files. The API requires at least one of query, category or a date bound.

        Dates are ``YYYY-MM-DD`` and are widened to a full KST day.
        """
        if not (query or category or start_date or end_date):
            raise MyboxError("Provide at least one of query, category, start_date or end_date.")
        if category and category not in CATEGORIES:
            raise MyboxError(f"category must be one of {', '.join(CATEGORIES)}")
        params: dict[str, Any] = {"count": 200, "dateField": date_field}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if start_date:
            params["startDate"] = f"{start_date}T00:00:00+09:00"
        if end_date:
            params["endDate"] = f"{end_date}T23:59:59+09:00"
        if parent_path:
            params["parentPath"] = parent_path
        return await self._paged("/search/resources/files", params, kind="search",
                                 max_items=max_items)

    async def info(self, resource_id: str) -> dict[str, Any]:
        """Attributes of one file or folder."""
        return await self.request("GET", f"/drive/resources/{resource_id}")

    async def create_folder(self, name: str, parent_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"folderName": name}
        if parent_id:
            body["parentId"] = parent_id
        return await self.request("POST", "/drive/folders", json=body)

    async def download(self, resource_id: str, dest_dir: str | os.PathLike[str], *,
                       file_name: str | None = None, skip_if_same_size: bool = True) -> dict[str, Any]:
        """Fetch one file into ``dest_dir``.

        The download URL is single use and valid for ten minutes, so it is requested and
        consumed immediately. A local file of the same name and size is left alone.
        """
        meta = await self.info(resource_id)
        name = file_name or meta.get("name") or resource_id
        destination = Path(dest_dir)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / name
        size = meta.get("size")
        if skip_if_same_size and target.exists() and size and target.stat().st_size == size:
            return {"path": str(target), "skipped": True, "bytes": target.stat().st_size}

        ticket = await self.request("GET", f"/drive/files/{resource_id}/download")
        partial = target.with_suffix(target.suffix + ".part")
        written = 0
        # The signed URL carries its own credentials; the bearer token is not sent there.
        async with self._client.stream("GET", ticket["downloadUrl"], timeout=300.0) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes(262144):
                    handle.write(chunk)
                    written += len(chunk)
        partial.replace(target)
        return {"path": str(target), "skipped": False, "bytes": written}

    async def upload(self, path: str | os.PathLike[str], *, parent_id: str | None = None,
                     overwrite: bool = False) -> dict[str, Any]:
        """Upload one local file. Returns the ticket the API answered with."""
        source = Path(path)
        if not source.is_file():
            raise MyboxError(f"Not a file: {source}")
        size = source.stat().st_size
        body: dict[str, Any] = {"fileName": source.name, "fileSize": size,
                                "isOverwrite": bool(overwrite)}
        if parent_id:
            body["parentId"] = parent_id
        ticket = await self.request("POST", "/drive/files", json=body)
        with source.open("rb") as handle:
            response = await self._client.post(
                ticket["uploadUrl"], files={"Filedata": (source.name, handle)}, timeout=600.0)
        if response.is_error:
            raise MyboxError(f"Upload failed for {source.name}: {response.status_code} "
                             f"{response.text[:200]}", status=response.status_code)
        return {"name": source.name, "bytes": size, "parentId": parent_id,
                "offset": ticket.get("offset", 0)}
