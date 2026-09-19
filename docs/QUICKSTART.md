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

## 2. Choose one server for your task

| Task | Command | Available tools |
| --- | --- | --- |
| Compare product prices and verify an offer | `compare-mcp` | Comparison, source inventory, card verification, browser handoff snapshot |
| Inspect a shortlist with optional reviews | `decision-mcp` | Comparison tools plus `decision_inspect` |
| Search a specific marketplace, read reviews, or use Cian | `marketplace-mcp` | All installed connectors |

Start with one entry in your client's MCP configuration. Replace the absolute
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
uv run marketplace-mcp install claude
```

The command prints configuration for manual insertion. Copy only the sources
you want. MPStats is optional and needs a paid token; ordinary marketplace
queries do not need it. `install cursor`, `install claude-code`, and
`install dsh` are also supported.

## 3. Check two sources before adding a browser

```console
uv run marketplace-mcp doctor wildberries yandex_market
```

These checks use anonymous HTTP. A blocked endpoint can still require a different
network. Doctor's Chrome status is separate: a missing Chrome does not prevent
anonymous HTTP checks from working.

| Result | Meaning | Next step |
| --- | --- | --- |
| `success`, exit 0 | Selected selfchecks passed | Try the request below |
| `drift_detected`, exit 1 | A response no longer matched the parser checks | Report the affected source and diagnostic output |
| `inconclusive` or `error`, exit 2 | Access or execution prevented a verdict | Read the reason; check network, browser, or required login |
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

Follow [Chrome setup](CDP_SETUP.md) to start the dedicated CDP browser. Sign in
to Taobao or Megamarket if using those sources, then run a targeted check:

```console
uv run marketplace-mcp doctor ozon aliexpress cian
```

Keep the same MCP session when using browser challenge handoff. Configuration,
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
