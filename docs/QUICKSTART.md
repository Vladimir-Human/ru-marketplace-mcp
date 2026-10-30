# First successful marketplace query

Use this guide to connect an MCP client, check your environment, and verify one
offer. Source availability depends on your network and browser session; the
offline test suite checks software behavior, not today's marketplace access.

## 1. Install the server

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run:

```console
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --frozen --all-packages
```

Node and jsdom are needed for contributor DOM tests only. You can use the MCP
servers without running the test suite or installing Node.

The next step is to wire a client, and the guided setup does it for you (see
step 2).

## 2. Choose one server for your task

| Task | Command | Available tools |
| --- | --- | --- |
| Compare product prices and verify an offer | `compare-mcp` | Comparison, source inventory, card verification, browser handoff snapshot |
| Inspect a shortlist with optional reviews | `decision-mcp` | Comparison tools plus `decision_inspect` |
| Search a specific marketplace, read reviews, or use Cian | `marketplace-mcp` | All installed connectors |

Start with the guided setup:

```console
uv run marketplace-mcp setup
```

It detects Claude Desktop, Cursor and Claude Code, writes the config with the
compare profile by default, starts the scraping Chrome, runs doctor's live
checks and offers `unblock` for a source the site walls off. `--dry-run` prints
the steps and files without writing, starting or requesting anything;
`--profile decision` or `--profile full` chooses another tool set.

To write one named client's config instead:

```console
uv run marketplace-mcp install claude --write
```

It adds one `marketplace` entry to the client's own config file, fills in the
absolute path to `uv` and to your checkout, keeps other servers and settings as
they are, and saves the old file as `<file>.bak-<timestamp>`. Restart the client
afterwards. The default `--profile compare` writes `compare-mcp`: 4
price-comparison tools, about 0.9k tokens per request. `--profile decision`
adds shortlist card inspection (5 tools), and `--profile full` writes
`marketplace-mcp` with every per-source tool (about 13.6k tokens). The entry
name stays `marketplace`, and a config that already has it keeps its profile
unless you pass `--profile`. `--dry-run` shows what would be written and writes
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
cover Claude Desktop, Claude Code, and Cursor. DeepSeek Harness has a different
format: follow its [bundle guide](../dsh/README.md).

To generate individual source entries with your checkout path already filled in:

```console
uv run marketplace-mcp install claude --per-source
```

The command prints the per-source block for manual insertion. Copy only the
sources you want; without `--per-source` it prints the single `marketplace`
entry for the selected profile described above. MPStats is optional and needs a
paid token; ordinary marketplace queries do not need it. `install cursor`,
`install claude-code`, and `install dsh` are also supported.

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

| Result | Meaning | Next step |
| --- | --- | --- |
| `success`, exit 0 | Selected selfchecks passed | Try the request below |
| `drift_detected`, exit 1 | A response no longer matched the parser checks | Report the affected source and diagnostic output |
| `inconclusive` or `error`, exit 2 | Access or execution prevented a verdict | Follow the next step printed under the row |
| Argument error, exit 2 | Unknown source, option, or missing argument | Correct the command; no source was queried |
| Status-file write failure, exit 3 | The report could not be saved | Check the output path and permissions |

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

- Run `uv run marketplace-mcp --help` in the clone to confirm installation.
- Check that `uv` is available to the desktop client, not just your terminal;
  an absolute path to `uv` can resolve a PATH mismatch.
- Check the clone path in the configuration and read the client's MCP stderr log.
- Run `uv run python scripts/e2e_stdio_check.py` to test local protocol startup
  across all installed servers. This makes no marketplace requests.

For development checks, see [CONTRIBUTING](../CONTRIBUTING.md). For an endpoint
failure, use the [bug report template](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/new/choose)
and include the source, version, command, and diagnostic result.
