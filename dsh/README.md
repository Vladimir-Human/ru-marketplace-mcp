# ru-marketplace-mcp for DeepSeek Harness

Read-only MCP servers for Russian marketplaces: prices, stock, ratings, reviews
and cross-marketplace price comparison. This bundle ships 15 Agent Skills plus
three mutually exclusive MCP server rows that are **off by default**.

## Why off by default

An MCP row that is always mounted is paid on **every request**, because the
harness registers the schema of every tool in the session context. Measured
over the stdio MCP wire with `scripts/mcp_wire.py`:

| Mode | Cost while mounted | Model-facing tools |
|---|---|---|
| Skills only (default) | 15 skill catalog entries; no MCP schemas | 0 |
| `compare-mcp` (recommended) | ~1.8k tokens per request | 4 |
| `decision-mcp` (shortlist inspection) | ~2.1k tokens per request | 5 |
| `marketplace-mcp` (full) | ~16.3k tokens per request | 40 |

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

   The 15 skills appear immediately. No MCP server starts yet.

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
   activates: `compare_prices`, `compare_sources`, `compare_verify_offer`, and
   `compare_browser_snapshot` (under the client's `rumarket` namespace).

## Middle profile and native vision

Set `RU_MARKETPLACE_MCP_DECISION=1` to add `decision_inspect` without mounting
every source tool. If both decision and full flags are set, full wins. Only one
MCP row is active: full, otherwise decision, otherwise compare.

With `CHROME_CHALLENGE_HANDOFF_S=120`, supported DOM challenges can retain their
owned browser tab. A client/model that accepts MCP images can call
`compare_browser_snapshot(handoff_id)` using the handle in the error or comparison
outcome. It receives a bounded JPEG viewport and capture metadata directly;
no separate OCR model or service is invoked. The handle only works in the same
MCP session and does not extend expiry. Text-only clients should skip this tool.

## Enabling the full server

Set one more variable before starting dsh:

```powershell
$env:RU_MARKETPLACE_MCP_FULL = "1"   # PowerShell
```

```console
export RU_MARKETPLACE_MCP_FULL=1     # POSIX shell
```

The enabled row then changes from `compare-mcp` (4 tools) to `marketplace-mcp`
(40 tools). All three rows share `serverName: rumarket`, and their `disabled`
conditions are mutually exclusive, so exactly one server instance runs at a
time.

## Uninstall

Remove the bundle from the profile and restart it:

```console
dsh plugin --profile web remove ru-marketplace-mcp-dsh
```

No MCP process survives profile restart without `RU_MARKETPLACE_MCP_DIR`.

## Docker alternative (published and CI-verified)

Since v1.8.0 every release tag builds a stdio image and proves it with a real
MCP session over `docker run --rm -i` before publishing to the MCP Registry:
initialize, `tools/list` and a `marketplace_sources` call. Use the
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
      - ghcr.io/vladimir-human/ru-marketplace-mcp:2.3.0
    failOnStartupError: false
```

The image defaults to the unified server. Its tool set matches that release tag;
unreleased tools in this source checkout are not present in older images.

## Source

Main repository: <https://github.com/Vladimir-Human/ru-marketplace-mcp>
