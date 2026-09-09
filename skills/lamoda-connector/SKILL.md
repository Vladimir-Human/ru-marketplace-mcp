---
name: lamoda-connector
description: Use this skill when the operator needs Lamoda fashion data — search or a product card with sizes. Trigger on "ламода", "lamoda", "кроссовки lamoda". Cards work anonymously (GraphQL); search needs the operator's Chrome over CDP. Skip for non-Lamoda tasks.
---

# Lamoda Connector

Two tiers by endpoint. The GraphQL product endpoint answers plain anonymous
HTTPS — it enriches a SKU you already have with real prices, brand, sizes and
availability. Everything that would find a SKU (search, catalog, HTML) sits
behind the same 307 redirect loop as Ozon, so discovery runs in the operator's
Chrome over CDP.

## When to use
- Search Lamoda by text (CDP tier)
- One product's price, old price, per-size availability (GraphQL, anonymous)

## Tools available
- `lamoda_search(query)` — tiles from the rendered search page (CDP)
- `lamoda_card(sku_or_url)` — GraphQL enrichment. SKU looks like MP002XM1RMM3;
  URLs carry it lowercased, the connector normalises.

**Not an MCP tool:** `lamoda_selfcheck()` probes both tiers. It is CLI-only —
`marketplace-mcp doctor` runs every connector's canary at once.

## Gotchas
- Lamoda exposes NO ratings anywhere — `rating` is not in the GraphQL schema.
  No review tools exist here by design.
- A search page yielding zero SKUs is drift, not "no results" — verify manually.
- A missing price is `null`, never `0`: `price_rub: null` means Lamoda had no
  usable price — treat it as no data, never as a free item.
## DSH activation

In DeepSeek Harness, the default profile exposes only `compare_prices` and
`compare_sources` through the cheap compare mount. Per-marketplace tools and
`marketplace_sources` require `RU_MARKETPLACE_MCP_FULL=1` and a profile restart;
do not call them in the default mode.
