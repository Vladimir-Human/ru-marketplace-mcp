---
name: marketplace
description: Use this skill when the operator wants every marketplace at once — compare prices across sources, or wire one MCP entry instead of ten. Trigger on "сравни цены", "где дешевле", "все маркетплейсы", "compare prices", or when setting up the client config. Skip for single-source tasks (use that source's skill).
---

# Unified Marketplace Server

One server mounting every installed connector as a namespaced toolset:
`wb_*`, `ozon_*`, `yandex_*`, `detmir_*`, `avito_*`, `taobao_*`, `megamarket_*`,
`lamoda_*`, `dns_*`, `citilink_*` plus `compare_prices` / `compare_sources` and its
own `marketplace_sources`. Tool names keep their prefixes, so habits and configs
carry over — but the operator wires a single `marketplace` entry instead of ten. The
server exposes 42 tools: 41 mounted plus `marketplace_sources`.

## When to use
- "Where is X cheapest" — compare_prices fans out across all searchable sources
- Client setup: one config entry, not ten
- Health overview: the CLI's `doctor` runs every selfcheck at once
- A source came back empty and you can't tell installed-but-quiet from never-loaded
  → `marketplace_sources`

## Tools
- `marketplace_sources()` — no arguments; returns `{mounted, skipped,
  mounted_count, skipped_count, server_version}`. `skipped` maps a source name to the
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
- A source whose optional deps are missing is simply absent from the set —
  `marketplace_sources` says which and why.
- compare_prices ranks on everyday ruble prices; Taobao (CNY) is reported in
  `price_native` but never ranked against rubles.
