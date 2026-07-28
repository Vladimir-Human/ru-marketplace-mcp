---
name: megamarket-connector
description: Use this skill when the operator needs Megamarket data — search the catalog or read a product card. Trigger on "мегамаркет", "megamarket", "цена мегамаркет". Always needs the operator's Chrome over CDP (ServicePipe IP block). Skip for non-Megamarket tasks.
---

# Megamarket Connector

Reads Megamarket via its internal mobile JSON API, POSTed from inside the
operator's Chrome over CDP. ServicePipe gates the whole site by IP reputation:
the API returns valid JSON, but the JSON is always the code-7 "отключите VPN"
refusal with your own IP echoed back. Only a browser that has passed the
ServicePipe challenge gets real catalog data.

## When to use
- Search the Megamarket catalog by text
- One product's price, old price, availability, rating

## Tools available
- `megamarket_search(query)` — items + total count. price_rub None when absent.
- `megamarket_card(item_id_or_url)` — one product card
- `megamarket_selfcheck()` — tri-state canary; a code-7 refusal is
  inconclusive (transport), never drift

## Gotchas
- A code-7 / VPN-error body is an IP block, not data — the connector maps it to
  transport_down with the fix inline.
- Run megamarket_selfcheck from your Chrome first: these endpoints were probed
  anonymously, and only a challenge-passed session confirms the in-browser shape.
