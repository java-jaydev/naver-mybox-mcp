"""MCP server exposing NAVER MYBOX as tools.

Run it over stdio::

    MYBOX_PAT=mbx_pat_... python -m mybox_mcp

Every tool needs a personal access token, taken from the ``MYBOX_PAT`` environment
variable or ``~/.mybox/token``. Nothing here deletes anything: delete, trash listing,
restore and trash-emptying exist in the API but are left out on purpose, so an agent
holding this token cannot destroy a user's files.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .client import CATEGORIES, MyboxClient, MyboxError

server: MCPServer = MCPServer(
    name="mybox",
    title="NAVER MYBOX",
    version="0.1.0",
    website_url="https://github.com/java-jaydev/mybox-mcp",
    instructions=(
        "Read and write files in a NAVER MYBOX drive. Search accepts a keyword, a category "
        "or a date range (at least one is required). Downloads and uploads move real bytes "
        "on the user's machine, so confirm the destination before large transfers. Daily "
        "download quotas are small on entry plans (500/day), so prefer one search that "
        "returns many files over many single lookups. Encrypted folders and folders shared "
        "with the user are not visible through this API."
    ),
)

_client: MyboxClient | None = None


def _get_client() -> MyboxClient:
    global _client
    if _client is None:
        _client = MyboxClient()
    return _client


def _fail(error: MyboxError) -> dict[str, Any]:
    return {"error": str(error), "code": error.code, "requestId": error.request_id}


def _slim(resource: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields an agent actually reasons about; drop the rest."""
    return {
        "resourceId": resource.get("resourceId"),
        "name": resource.get("name"),
        "type": resource.get("type"),
        "category": resource.get("category"),
        "size": resource.get("size"),
        "modifiedAt": resource.get("modifiedAt"),
        "createdAt": resource.get("createdAt"),
        "parentId": resource.get("parentId"),
    }


@server.tool()
async def mybox_storage() -> dict[str, Any]:
    """Report MYBOX quota, used bytes, per-category file counts and max upload size."""
    try:
        return await _get_client().storage()
    except MyboxError as error:
        return _fail(error)


@server.tool()
async def mybox_list(folder_id: str | None = None, count: int = 100,
                     sort: str = "modifiedAt,desc") -> dict[str, Any]:
    """List files and folders directly inside a folder, or the drive root.

    Args:
        folder_id: Folder to list. Omit for the drive root.
        count: Maximum entries to return (the API pages internally, 1000 per page).
        sort: "field,direction" where field is name, createdAt, modifiedAt or accessedAt.
    """
    try:
        items = await _get_client().list_resources(folder_id, count=count, sort=sort,
                                                   max_items=count)
        return {"count": len(items), "resources": [_slim(i) for i in items]}
    except MyboxError as error:
        return _fail(error)


@server.tool()
async def mybox_search(query: str | None = None, category: str | None = None,
                       start_date: str | None = None, end_date: str | None = None,
                       date_field: str = "created", parent_path: str | None = None,
                       max_items: int = 200) -> dict[str, Any]:
    """Search files by keyword, category and/or date range. At least one is required.

    Args:
        query: Keyword. Spaces and extensions are combined with AND.
        category: One of image, video, audio, document, archive, executable, etc.
        start_date: Inclusive start, YYYY-MM-DD, interpreted in KST.
        end_date: Inclusive end, YYYY-MM-DD, interpreted in KST.
        date_field: Which date the range applies to: "created" or "modified".
        parent_path: Restrict the search to this folder path and below.
        max_items: Stop after this many results.
    """
    try:
        items = await _get_client().search(
            query=query, category=category, start_date=start_date, end_date=end_date,
            date_field=date_field, parent_path=parent_path, max_items=max_items)
        return {"count": len(items), "resources": [_slim(i) for i in items]}
    except MyboxError as error:
        return _fail(error)


@server.tool()
async def mybox_file_info(resource_id: str) -> dict[str, Any]:
    """Fetch the attributes of one file or folder by id."""
    try:
        return await _get_client().info(resource_id)
    except MyboxError as error:
        return _fail(error)


@server.tool()
async def mybox_download(resource_id: str, dest_dir: str) -> dict[str, Any]:
    """Download one file into a local directory and return the path written.

    A local file with the same name and size is left in place and reported as skipped.
    Download quotas are per day and small on entry plans, so avoid re-downloading.
    """
    try:
        return await _get_client().download(resource_id, dest_dir)
    except MyboxError as error:
        return _fail(error)


@server.tool()
async def mybox_upload(path: str, parent_id: str | None = None,
                       overwrite: bool = False) -> dict[str, Any]:
    """Upload one local file to MYBOX.

    Args:
        path: Local file to upload.
        parent_id: Destination folder id. Omit to upload to the drive root.
        overwrite: Replace an existing file with the same name instead of keeping both.
    """
    try:
        return await _get_client().upload(path, parent_id=parent_id, overwrite=overwrite)
    except MyboxError as error:
        return _fail(error)


@server.tool()
async def mybox_create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
    """Create a folder and return its id.

    Args:
        name: Folder name.
        parent_id: Parent folder id. Omit to create it at the drive root.
    """
    try:
        return await _get_client().create_folder(name, parent_id)
    except MyboxError as error:
        return _fail(error)


def main() -> None:
    """Entry point for the ``mybox-mcp`` script."""
    server.run()


__all__ = ["server", "main", "CATEGORIES"]
