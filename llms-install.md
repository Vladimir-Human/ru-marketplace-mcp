# Install guide for AI agents (Cline, Claude Code, Cursor, OpenCode, Windsurf)

This file provides the shortest deterministic setup path for AI agents. Full documentation lives in `README.md` and `docs/QUICKSTART.md`.

## What it is

`ru-marketplace-mcp` exposes 12 Russian and Chinese marketplaces and classifieds (Wildberries, Ozon, Yandex Market, Detsky Mir, Avito, AliExpress, Taobao, Megamarket, Lamoda, DNS, Citilink, and Cian real estate) as read-only MCP servers, plus cross-marketplace price comparison in a single call.

No API keys, seller accounts, or tokens are required. Wildberries, Yandex Market, and Detsky Mir work over anonymous HTTP out of the box; anti-bot-gated marketplaces read through the operator's local Chrome over CDP (`uvx ru-marketplace-mcp browser start`).

## Recommended MCP configuration (no clone required)

Requires **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/).

Add the compact `compare-mcp` profile (4 cross-marketplace comparison tools, lowest token cost per request) to your `mcpServers` JSON:

```json
{
  "mcpServers": {
    "marketplace": {
      "command": "uvx",
      "args": [
        "--from",
        "ru-marketplace-mcp",
        "compare-mcp"
      ]
    }
  }
}
```

### Profiles

- `compare-mcp` (default, 4 tools, ≈1.7k tokens): cross-marketplace price comparison (`compare_prices`, `compare_verify_offer`, `compare_sources`, `compare_browser_snapshot`).
- `decision-mcp` (5 tools, ≈2.0k tokens): `compare-mcp` plus `decision_inspect` to inspect a shortlisted offer card on any source.
- `marketplace-mcp` (39 tools, ≈13.4k tokens): mounts every per-marketplace search, card, review, seller, and Cian real-estate tool under a single server.

### Automatic client setup

If the operator uses Claude Desktop, Claude Code, or Cursor, you can wire the config automatically:

```bash
uvx ru-marketplace-mcp setup
# or for a specific client and profile:
uvx ru-marketplace-mcp install cursor --write --profile compare
```

### Docker alternative (stdio)

The published GHCR image (`ghcr.io/vladimir-human/ru-marketplace-mcp:latest`) is built from `Dockerfile.stdio` (`MCP_TRANSPORT=stdio`, default entry point `marketplace-mcp`). Note that the root `Dockerfile` is HTTP-first (`wb-mcp` on port 8000); for local stdio builds use `docker build -f Dockerfile.stdio .` or pass `-e MCP_TRANSPORT=stdio`.

```json
{
  "mcpServers": {
    "marketplace": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e",
        "MCP_TRANSPORT=stdio",
        "ghcr.io/vladimir-human/ru-marketplace-mcp:latest",
        "compare-mcp"
      ]
    }
  }
}
```

## Verify health

```bash
uvx ru-marketplace-mcp doctor
```

- Exit `0`: all checked sources are healthy.
- Exit `2`: a browser-backed source needs Chrome started (`uvx ru-marketplace-mcp browser start`) or a one-time human check/login in Chrome (`uvx ru-marketplace-mcp unblock <source>`).
