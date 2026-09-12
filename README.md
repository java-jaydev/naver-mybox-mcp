# mybox-mcp

An MCP server for the **NAVER MYBOX Open API** — list, search, download and upload files in
a MYBOX drive from any MCP client (Claude Code, Claude Desktop, and others).

MYBOX opened a public API in 2026 ([developers.mybox.naver.com](https://developers.mybox.naver.com/)).
This wraps it as tools. Unofficial, not affiliated with NAVER.

## Why

Phone photos land in MYBOX automatically. Getting them onto a machine meant clicking through
the web UI. With this, an agent can fetch "every photo taken on 10–11 September" into a
working folder in one step.

## Install

Requires Python 3.10+.

```bash
pip install mybox-mcp
```

## Get a token

Authentication is a **personal access token**, not OAuth.

1. Sign in at MYBOX on the web.
2. Settings → *account and personal access token management* → **create token**.
3. Copy it immediately — it is shown once.

Up to five tokens per account; each lasts 30, 60, 90 or 180 days. Anyone holding the token
can reach the whole drive, so treat it as a password and never commit it.

Provide it as the `MYBOX_PAT` environment variable, or put it on one line in `~/.mybox/token`.

## Configure your MCP client

```json
{
  "mcpServers": {
    "mybox": {
      "command": "mybox-mcp",
      "env": { "MYBOX_PAT": "mbx_pat_..." }
    }
  }
}
```

In Claude Code: `claude mcp add mybox --env MYBOX_PAT=mbx_pat_... -- mybox-mcp`

## Tools

| Tool | What it does |
|---|---|
| `mybox_storage` | Quota, used bytes, per-category file counts, max upload size |
| `mybox_list` | Entries directly inside a folder, or the drive root |
| `mybox_search` | Search by keyword, category and/or date range (KST) |
| `mybox_file_info` | Attributes of one file or folder |
| `mybox_download` | Download one file into a local directory |
| `mybox_upload` | Upload one local file |
| `mybox_create_folder` | Create a folder |

### Deliberately missing: delete

The API can delete files, empty the trash and set its auto-delete period. Those tools are not
exposed here. An agent that can read and write files is useful; one that can destroy them is a
different risk, and the web UI is right there. Open an issue if you need them behind a flag.

## Quotas

The docs publish per-plan limits and warn that bursts or abuse may be blocked **without prior
notice**, so the client paces itself below the lowest documented per-minute limits (9/min for
search, 55/min otherwise).

| Plan | Downloads | Search | Other APIs |
|---|---|---|---|
| 30GB | 500/day | 10/min | 60/min each |
| 80GB | 1,000/day | 10/min | 60/min each |
| 180GB–330GB | 1,000/day | 30/min | 240/min each |
| 2TB | 2,000/day | 30/min | 240/min each |

**Download limits are daily and the client cannot pace around them.** Search first, count the
results, and fetch in batches if the set is large.

## Notes on the API

- Base URL `https://open-api.mybox.naver.com/v1`
- Upload is two steps: `POST /drive/files` returns an `uploadUrl` (48h, single use); the bytes
  go there as multipart field `Filedata`.
- Download is two steps: `GET /drive/files/{id}/download` returns a `downloadUrl`
  (10 minutes, single use).
- Paging: `responseMetaData.nextCursor` goes back as `cursor`.
- Search needs at least one of keyword, category or a date bound.
- **Encrypted folders and folders shared with you are not exposed by the API** — they exist
  only in the web and mobile apps. An empty listing does not mean an empty drive.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run against `httpx.MockTransport`; no token or network needed.

## License

MIT
