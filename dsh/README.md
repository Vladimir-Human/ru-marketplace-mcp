# ru-marketplace-mcp for DeepSeek Harness

Read-only MCP servers for Russian marketplaces: prices, stock, ratings, reviews
and cross-marketplace price comparison. This bundle ships 14 Agent Skills plus
two MCP server rows that are **off by default**.

## Why off by default

An MCP row that is always mounted is paid on **every request**, because the
harness registers the schema of every tool in the session context. Measured
over the stdio MCP wire with `scripts/mcp_wire.py`:

| Mode | Cost while mounted | Model-facing tools |
|---|---|---|
| Skills only (default) | ~390 tokens for 14 catalog rows | 0 |
| `compare-mcp` (recommended) | ~0.9k tokens per request | 2 |
| `marketplace-mcp` (full) | ~13.6k tokens per request | 36 |

The 11 `*_selfcheck` tools that previously inflated the full server to 45 tools
are now CLI-only (`marketplace-mcp doctor`); only model-facing tools are
published over MCP.

## Requirements

- DeepSeek Harness (`dsh`)
- Python **≥ 3.12**
- [`uv`](https://docs.astral.sh/uv/)
- A local clone of this repository (the MCP rows launch it with
  `uv run --directory <clone>`)

## Install (three steps)

1. Add the bundle to a profile (`web` is the profile dsh ships with a UI; use
   whichever profile you actually run — a bare profile that dsh auto-creates on
   first use carries no app and cannot be launched):

   ```console
   dsh plugin --profile web add github:Vladimir-Human/ru-marketplace-mcp#path:/dsh
   ```

   The 14 skills appear immediately. No MCP server starts yet.

2. Clone the server and install its locked environment once:

   ```console
   git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
   cd ru-marketplace-mcp
   uv sync --frozen
   ```

3. Set the gate/path variable so the profile can find the clone and restart the
   profile. On Windows PowerShell:

   ```powershell
   $env:RU_MARKETPLACE_MCP_DIR = "C:\path\to\ru-marketplace-mcp"
   ```

   On POSIX shells:

   ```console
   export RU_MARKETPLACE_MCP_DIR=/path/to/ru-marketplace-mcp
   ```

   With only `RU_MARKETPLACE_MCP_DIR` set, the recommended compare mode
   activates: `mcp__rumarket__compare_prices` and `mcp__rumarket__compare_sources`.

## Enabling the full server

Set one more variable before starting dsh:

```powershell
$env:RU_MARKETPLACE_MCP_FULL = "1"   # PowerShell
```

```console
export RU_MARKETPLACE_MCP_FULL=1     # POSIX shell
```

The enabled row then changes from `compare-mcp` (2 tools) to `marketplace-mcp`
(36 tools). Both rows share `serverName: rumarket`, and their `disabled`
conditions are mutually exclusive, so exactly one server instance runs at a
time.

## Uninstall

Remove the bundle from the profile and restart it:

```console
dsh plugin --profile web remove ru-marketplace-mcp-dsh
```

No MCP process survives profile restart without `RU_MARKETPLACE_MCP_DIR`.

## Docker alternative (published and CI-verified)

Since v1.6.0 every release tag builds a stdio image and proves it with a real
MCP session over `docker run --rm -i` before publishing to the MCP Registry:
initialize, `tools/list` (36 tools) and a `marketplace_sources` call. Use the
published GHCR image instead of a local clone:

```yaml
- id: ru-marketplace-docker
  name: '@deepseek-ai/dsh-mcp-client'
  disabled: false
  config:
    serverName: rumarket
    transport: stdio
    command: docker
    args:
      - run
      - --rm
      - -i
      - ghcr.io/vladimir-human/ru-marketplace-mcp:1.6.0
    failOnStartupError: false
```

The image defaults to the unified server; full-mode wire cost applies
(~13.6k tokens per request), so opt in deliberately.

## Source

Main repository: <https://github.com/Vladimir-Human/ru-marketplace-mcp>