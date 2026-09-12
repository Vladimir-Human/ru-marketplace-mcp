---
name: marketplace
description: Use this skill when the operator wants the full marketplace mount, routing metadata, or client setup. Trigger on "один MCP", "все источники", "marketplace_sources", "настроить marketplace", or "full marketplace". Route price questions such as "где дешевле" and "compare prices" to compare-prices; skip single-source tasks.
---

# Unified Marketplace Server

One server mounting every installed connector as a namespaced toolset:
`wb_*`, `ozon_*`, `yandex_*`, `detmir_*`, `avito_*`, `taobao_*`, `megamarket_*`,
`lamoda_*`, `dns_*`, `citilink_*`, `aliexpress_*`, `cian_*`, `mpstats_*` plus
`compare_prices` / `compare_sources` and its own `marketplace_sources`. Tool
names keep their prefixes, so habits and configs carry over — but the operator
wires a single `marketplace` entry instead of fourteen. The server exposes 39
tools: 38 mounted plus `marketplace_sources`. `mpstats_*` is the optional paid
source: without `MPSTATS_MP_AUTH` its tools answer `auth_missing` while
everything else is unaffected.

## When to use
- "Where is X cheapest" — compare_prices fans out across all searchable sources
- Client setup: one config entry, not fourteen
- Health overview: the CLI's `doctor` runs every selfcheck at once
- A source came back empty and you can't tell installed-but-quiet from never-loaded
  → `marketplace_sources`

## Tools
- `marketplace_sources()` — no arguments; returns `{mounted, skipped,
  mounted_count, skipped_count, capabilities, server_version}`. `capabilities` is
  static routing metadata per source: access tier, CDP/login requirements, currency,
  text-search support, and whether the connector is mounted. `skipped` maps a source name to the
  import error that dropped it, usually a missing dependency. Connectors are imported
  defensively, so a missing dep removes a marketplace instead of killing the server —
  but from the client an absent source looks identical to one that found nothing.
  Call this to tell the two apart: a name in `skipped` was never queried at all.

## Operator CLI
- `marketplace-mcp install [client]` — print the exact mcpServers JSON to paste.
  From a source checkout it prints the real filesystem path of that checkout, so
  there's no `/path/to/ru-marketplace-mcp` placeholder to hand-edit; installed as a
  wheel, it prints the console-script paths on PATH instead. `client` must be one of
  `claude`, `claude-code`, `cursor` — an unknown name is rejected, not answered with
  a Claude block.
- `marketplace-mcp doctor [--status-file path]` — per-source health plus a CDP
  session probe, with an optional machine-readable JSON snapshot for monitoring. Exit
  codes are meaningful for cron and CI: `0` everything healthy, `1` a parser drifted
  (the alarm — someone has to look), `2` nothing drifted but at least one source
  couldn't be judged (blocked, no CDP, wrong region). "Couldn't check anything" is a
  `2`, never a `0`.

## Gotchas
- Recovery-aware clients should read `source_outcomes.requires_user_action`
  before scheduling retries. Keep answered sources visible, show the affected
  marketplace and `challenge_type`, and retry that source after the browser
  interaction completes. A challenge is not an empty product search. See the
  compare-prices skill for the retry contract and current tab-retention limits.
- A source whose optional deps are missing is simply absent from the set —
  `marketplace_sources` says which and why.
- Set `MARKETPLACE_SOURCES` on the unified server to mount only a comma-separated
  subset (`wildberries,ozon,compare`); unset or blank keeps every source. The
  aliases `wb`, `ym`/`yandex`, `detmir`, and `ali` work too. Unknown names fail
  at startup instead of silently producing a partial server, and deselected
  sources are reported with a `deselected` reason.
- compare_prices ranks on everyday ruble prices; Taobao (CNY) is reported in
  `price_native` but never ranked against rubles.
## DSH activation

In DeepSeek Harness, the default profile exposes only `compare_prices` and
`compare_sources` through the cheap compare mount. Per-marketplace tools and
`marketplace_sources` require `RU_MARKETPLACE_MCP_FULL=1` and a profile restart;
do not call them in the default mode.
