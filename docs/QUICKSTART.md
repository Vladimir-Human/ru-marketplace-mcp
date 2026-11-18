# First successful marketplace query

Use this guide to connect an MCP client, check your environment, and verify one
offer. Source availability depends on your network and browser session; the
offline test suite checks software behavior, not today's marketplace access.

## 1. Install the server

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

Without a checkout, the `ru-marketplace-mcp` package on PyPI holds every server
and the CLI, and one command installs it and runs the guided setup (`--dry-run`
previews the plan and writes nothing):

```console
uvx ru-marketplace-mcp setup
```

The client entry it writes pins the release that ran. In the commands below,
replace `uv run marketplace-mcp` with `uvx ru-marketplace-mcp` when running
without a clone (AI agents: see [llms-install.md](../llms-install.md)).

If you prefer a local source checkout (for development, custom env pins, or
loading the 14 Agent Skills in `skills/`):

```console
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --frozen --all-packages
```

Node and jsdom are needed for contributor DOM tests only. You can use the MCP
servers without running the test suite or installing Node.

## 2. Choose one server for your task

| Profile (`--profile`) | Size | Best for |
| --- | --- | --- |
| **`compare`** *(default)*<br>`compare-mcp` | 4 · ≈1.7k | Compare prices and verify an offer |
| **`decision`**<br>`decision-mcp` | 5 · ≈2.0k | Compare + `decision_inspect` shortlist card/reviews |
| **`full`**<br>`marketplace-mcp` | 39 · ≈13.1k | Per-marketplace search, reviews, seller IDs, Cian (`MARKETPLACE_SOURCES` narrows it) |
| Single marketplace<br>`--per-source` | 2–8 · min. | Only one connector (`wb-mcp`, `ozon-mcp`, `cian-mcp`, …) |

Start with the guided setup:

```console
uv run marketplace-mcp setup
```

It detects installed clients (**Claude Desktop**, **Cursor**, **Claude Code**,
**VS Code**, **Windsurf**, **Antigravity**, **Gemini CLI**, and **Codex CLI**),
writes the config (or prints the `mcp add` command for `claude-code` and
`codex`) with the `compare` profile by default, starts the scraping Chrome, runs
doctor's live checks, and offers `unblock` for a source the site walls off.
`--dry-run` prints the steps and files without writing, starting, or requesting
anything; `--no-browser` and `--no-doctor` skip Chrome and live requests;
`--profile decision` or `--profile full` chooses another tool set.

For **Claude Desktop** without a terminal, download the one-click bundle
`ru-marketplace-mcp-compare-<version>.mcpb` from
[GitHub Releases](https://github.com/Vladimir-Human/ru-marketplace-mcp/releases)
and open it in Claude Desktop (checked on Windows, not checked on macOS).

To write or print one named client's config directly:

```console
uv run marketplace-mcp install claude --write
```

All 9 `install <client>` targets are supported: `claude`, `cursor`, `vscode`,
`windsurf`, `antigravity`, `gemini`, `claude-code`, `codex`, and `dsh`.
For the 6 JSON-file clients (`claude`, `cursor`, `vscode`, `windsurf`,
`antigravity`, `gemini`), `--write` atomically adds one `marketplace` entry to
the client's own config file (using `"servers"` with `"type": "stdio"` for
VS Code, and `"mcpServers"` for the rest), fills in the absolute path to `uv`
and your checkout, keeps other servers and settings intact, and saves the old
file as `<file>.bak-<timestamp>`. For `claude-code` and `codex`, `install`
prints the exact `claude mcp add` or `codex mcp add` command to run; for `dsh`,
it prints `cordis.patch.yml` rows. Restart the client afterwards.

The default `--profile compare` runs `compare-mcp`: 4 price-comparison tools,
the cheapest per request. `--profile decision` adds shortlist card inspection
(5 tools), and `--profile full` runs `marketplace-mcp` with every per-source
tool (measured per profile in [TOOLS.md](TOOLS.md)). The entry name stays
`marketplace`, and a config that already has it keeps its profile unless you
pass `--profile`. `--write --dry-run` shows what would be written and writes
nothing; `--per-source` writes one entry per source instead and cannot be
combined with `--profile`.

If you prefer to paste the configuration yourself, start with one entry in your
client's MCP configuration. Replace the absolute
path with the clone from step 1; use forward slashes on Windows as shown.

```json
{
  "mcpServers": {
    "marketplace": {
      "command": "uv",
      "args": ["run", "--frozen", "--directory", "C:/projects/ru-marketplace-mcp", "compare-mcp"]
    }
  }
}
```

Use the client's own configuration location and restart or reconnect its MCP
session. The [README client examples](../README.md#подключение-к-mcp-клиенту)
and [English README](../README.en.md) cover all supported clients. DeepSeek
Harness uses a bundle format: follow its [bundle guide](../dsh/README.md).

To generate individual source entries with your checkout path already filled in:

```console
uv run marketplace-mcp install claude --per-source
```

The command prints the per-source block for manual insertion. Copy only the
sources you want; without `--per-source` it prints the single `marketplace`
entry for the selected profile described above.

## 3. Check two sources before adding a browser

```console
uv run marketplace-mcp doctor wildberries yandex_market
```

With the default `WB_TRANSPORT=api`, these checks use anonymous HTTP. A blocked
endpoint can still require a different network. Doctor's Chrome status is separate:
a missing Chrome does not prevent anonymous HTTP checks from working. If you set
`WB_TRANSPORT=dom`, WB's `card` and `search_v9` checks instead open two pages in
the operator's Chrome (baseline card and search grid); a captcha is `inconclusive`
and is never solved or retried. WB's other probes still use HTTP; see
[the rendered-page notes](ANTI_BOT.md#when-the-api-refuses-rendered-pages-wb_transportdom).

| Verdict | Meaning | Next step |
| --- | --- | --- |
| `success` (`0`) | Selected selfchecks passed | Try the request below |
| `drift_detected` (`1`) | Response stopped matching parser checks | Report source and diagnostics |
| `inconclusive` (`2`) | Block, unavailable probe, or `budget_refused` | Follow `next:` hint (not health or alarm) |
| Write fail (`3`) | `--status-file` could not be saved | Check path and permissions |
| `error` (`4`) | Selfcheck raised or bad args/setup | Alarm / fix arguments |

A failed checker takes precedence over drift, and drift over an inconclusive
source. Alarm on `1` and `4`, not on `2`. A budget refusal never counts as a
healthy check. `unblock` discovery/re-check uses the same hard-failure code;
`setup` also returns 4 if its health check or unblock re-check fails, otherwise
its exit still describes whether a client was configured.

For machine-readable results:

```console
uv run marketplace-mcp doctor wildberries yandex_market --status-file status.json
```

## 4. Ask for a comparison, then verify

Example request to your MCP-enabled assistant:

> Compare prices for an iPhone 15 128 GB on Wildberries and Yandex Market only.
> Use `compare_prices` with `sources=["wildberries", "yandex_market"]`.
> Show source failures, stock uncertainty, and currency. Verify the selected
> listing with `compare_verify_offer` before recommending it. Do not treat a
> title match as proof of an identical variant or seller offer.

Read `source_outcomes` and `complete` before claiming the comparison covered all
requested sources. `cheapest` is a numeric minimum; product identity, condition,
subscription prices, and stock can change which offer is suitable. A verified
card is an observation at request time, not a price guarantee.
Read `source_outcomes[].warnings` too: a source can answer successfully while
reporting incomplete fields, fallback extraction, or a truncated grid. Those
limitations also appear in top-level warnings with the source name.

For items available to buy now, pass `in_stock_only=true`. A missing stock count
or an ambiguous label means unknown (`null`), not confirmed availability or a
sell-out. Excluded offers remain in the response so you can explain the limit.
For a Yandex search row, keep its `variant_id` and pass it as
`expected_variant_id` when verifying the card; one product family can contain
several differently priced SKUs.

Cian searches real estate through its own filters. It does not participate in
product price comparison. Taobao prices remain in yuan and do not compete with
ruble prices.

## 5. Add browser-backed sources when needed

Follow [Chrome setup](CDP_SETUP.md) to start the dedicated CDP browser. Your
everyday Chrome can stay open. Sign in to Taobao or Megamarket if using those
sources, then run a targeted check:

```console
uv run marketplace-mcp doctor ozon aliexpress cian
```

On Windows the connector's autostart parks the scraping window off-screen, so
you cannot log in or pass a site's check until it is on-screen. You can start a
visible Chrome yourself or move the existing window:

```console
uv run marketplace-mcp browser start    # launch the scraping Chrome on-screen (--hidden: off-screen)
uv run marketplace-mcp browser status   # is the endpoint reachable, where is the window
uv run marketplace-mcp browser show     # bring the window on-screen and activate its first tab
```

When `doctor` reports a wall, let `unblock` open the right page and wait while
you pass the check or log in:

```console
uv run marketplace-mcp unblock ozon
uv run marketplace-mcp unblock --dry-run ozon   # print the page, open nothing
```

Repeat the call from the same client (the same MCP session, or the same stdio
server process) when using browser challenge handoff. Configuration,
supported challenge types, and limits are documented in
[optional challenge handoff](CDP_SETUP.md#optional-challenge-handoff).

## If the client cannot connect

- Run `uv run marketplace-mcp --help` in the clone (or `uvx ru-marketplace-mcp --help` without a clone) to confirm installation.
- Check that `uv` is available to the desktop client, not just your terminal;
  an absolute path to `uv` can resolve a PATH mismatch.
- Check the clone path in the configuration (and remember VS Code uses `"servers"` with `"type": "stdio"`, not `"mcpServers"`) and read the client's MCP stderr log.
- In a source checkout, run `uv run python scripts/e2e_stdio_check.py` to test local protocol startup
  across all installed servers. This makes no marketplace requests.

For development checks, see [CONTRIBUTING](../CONTRIBUTING.md). For an endpoint
failure, use the [bug report template](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/new/choose)
and include the source, version, command, and diagnostic result.
