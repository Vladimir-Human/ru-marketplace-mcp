---
name: avito-connector
description: Use this skill when the operator needs Avito classifieds data — search listings, item cards, or seller reputation. Trigger on Russian queries like "найди на авито", "цена avito", "объявления авито", "продавец на авито", or English mentions of Avito. Needs the operator's Chrome over CDP from a datacenter IP (IP firewall). Skip for non-Avito tasks.
---

# Avito Connector

Reads Avito via the internal `js/items` JSON API. Two tiers: impersonated HTTPS
(curl_cffi) works from a residential Russian IP; from a datacenter the firewall
answers 403 + captcha, so the fetch falls back to the operator's logged-in
Chrome over CDP — same pattern as Ozon.

## When to use
- Search classifieds by text, with optional category/location filters
- One listing's price, description, views
- Seller reputation — Avito is classifieds: no per-item reviews, the seller's
  rating and active-listing count ARE the review signal

## Tools available
- `avito_search(query, page, location_id, category_id)` — listings via js/items.
  price_rub is None for ads with no price (free/exchange) — never 0.
- `avito_card(item_id_or_url)` — one listing: price, description, views, seller
- `avito_seller(seller_id_or_url)` — seller rating, review count, active listings
- `avito_selfcheck()` — tri-state drift canary

## What a search row does and does not carry

Confirmed against a real `js/items` response captured 2026-07-28 from a
residential session:

- **`location` is an object upstream**, not a string:
  `{"id": 637640, "name": "Москва", ...}`. The connector reduces it to the place
  name. When Avito itself has no place name for an ad, the field is `None` — the
  numeric `locationId` is deliberately NOT resolved to a city, because that would
  need a lookup table this connector does not have and the result would be a
  guess that reads like data.
- **`price` does not exist as a top-level key.** The amount lives in
  `priceDetailed.value`; the connector reads it and leaves `None` for priceless ads.
- **`posted_at` is ISO-8601 UTC**, converted from Avito's `sortTimeStamp` (epoch
  milliseconds). There is no date string in the payload to pass through.
- **`seller_name` is `None` on search rows.** The payload has no seller object at
  all — only a logo link. Use `avito_card` or `avito_seller` when you need the
  seller; do not infer a name from the search row.

## Gotchas
- A 403 that sets cookies and returns plausible HTML is the firewall, not data;
  only JSON-shaped 200s count. The connector handles this — trust tier_used.
- location_id defaults to Moscow (637640); pass another Avito location id per call.
- **Deep pagination is unreliable, and not because of this connector.** Avito
  escalates 429 then 403 once a session walks past the first page or two; the
  maintainers of the widely used open-source Avito parsers report the same and
  advise staying on page 1. Take the first page, narrow the query instead of
  paging, and treat a `rate_limited` or `transport_down` on `page > 1` as
  expected rather than as drift.
- From a datacenter IP expect the CDP tier; keep the scraping-profile Chrome
  logged into avito.ru.
- The JSON API and the HTML pages are gated differently: `js/items` can answer
  from a residential session while `avito.ru/<region>/<category>/<slug>` still
  shows the security wall. A blocked listing page does not mean search is down.
