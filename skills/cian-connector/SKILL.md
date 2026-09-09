---
name: cian-connector
description: Use this skill when the operator needs Russian real-estate data from Cian (cian.ru) — flats, rooms, houses or commercial property for sale or long-term rent, or one offer's card with price history and publisher. Trigger on Russian queries like "найди на циане", "квартира на циан", "снять квартиру", "купить однушку в Москве", "сколько стоит квартира на", "объявление циан", or English mentions of Cian. Needs the operator's Chrome over CDP (Cian's WAF blocks plain HTTP by IP). Skip for goods, marketplaces, and non-Cian real estate.
---

# Cian Connector

Reads Cian through the site's own JSON API, called from inside the operator's
Chrome over CDP: the search endpoint answers an in-page POST, and the offer
card embeds its whole state in `window._cianConfig`. No HTML is parsed. Plain
HTTP is blocked by Cian's WAF on IP reputation (a 403 «Обнаружен подозрительный
трафик» page, no captcha), so there is no anonymous tier.

## When to use
- Find offers by filters: deal (sale / rent), property type, region, rooms,
  price range, total area
- One offer's price, its price history, layout, building, address, metro,
  description and publisher
- Real estate only. For goods use the marketplace connectors; for price
  comparison across shops use `compare_prices` — Cian takes no part in it

## Tools available
- `cian_search(deal, offer_type="flat", region=None, rooms=None, price_min=None, price_max=None, area_min=None, area_max=None, page=1)`
  — offers via `search-offers-desktop`, 28 per page. `deal` is `sale` or
  `rent` (long-term); `offer_type` is `flat`, `room`, `house` or `commercial`.
  `price_rub` is None for an offer without a stated price — never 0.
- `cian_card(offer_id_or_url)` — one offer: price, `price_history`, rooms,
  areas, floor, building, address, `metro[]`, description, views, `agent`.

**Not an MCP tool:** `cian_selfcheck()` is a tri-state drift canary. It is
CLI-only — `marketplace-mcp doctor` runs every connector's canary at once.

## Region is an id, not a name

Cian addresses regions by its own numeric ids and the connector has no
lookup table. Known and verified live:

| id   | region                |
|------|-----------------------|
| 1    | Москва                |
| 2    | Санкт-Петербург       |
| 4593 | Московская область    |
| 4588 | Ленинградская область |

Anything else needs the Cian id (visible as `region=` in a cian.ru search
URL). Do not guess an id: a wrong one returns another region's offers with
correct-looking prices. Default is `CIAN_REGION` (1, Moscow).

## Filters: what the search does and does not do

- **No text search.** There is no query string; "двушка у метро Сокол" has to
  become `rooms=[2]` plus a region and a price range. Metro and street filters
  are not exposed; filter the returned rows by `address` / `metro` yourself.
- **`rooms`** applies to flats only: any of 1–6, plus Cian's codes 7 (свободная
  планировка) and 9 (студия) — verified live, and easy to get backwards.
  Rooms (`offer_type="room"`) are searched as flats with Cian's room code 0 —
  the caller's `rooms` is ignored there.
- **Rent is long-term** (`for_day: "!1"`); daily rent is not exposed.
- **`price_min` / `price_max`** are rubles: total for sale, per month for rent.
- **`total_count`** is Cian's `aggregatedCount` — the de-duplicated figure the
  site shows, usually below the raw `offerCount`.

## What a row carries and how to read it

Confirmed against live payloads captured 2026-09-09 (fixtures in the package):

- **Price** comes from `bargainTerms.priceRur`, then `bargainTerms.price`, then
  `priceTotalRur`. A new-building card has only the latter two — the parser
  reads all three, so a `null` price means Cian shows none, not a missed key.
- **`title` is Cian's own only on some offers**; otherwise the short info line
  ("1-комн.кв. · 12/22 этаж") or a composed "rooms, area, floor" stands in.
  Do not treat the title as a marketing name.
- **`metro`** on a search row is the station Cian marks as the tile's own
  (`isDefault`), not the shortest travel time — a 7-minute bus ride does not
  outrank a 10-minute walk. The card returns every station in that order, with
  `mode` `walk` or `transport`.
- **`category`** tells the market: `newBuildingFlatSale` is a developer's
  primary offer (`agent.user_type = developer`, `saleType fz214`), `flatSale`
  is resale, `roomSale`, `houseSale`, `officeSale` and friends for the rest.
- **Rent rows** add `price_period` (`monthly`), `lease_term` (`longTerm` /
  `fewMonths`) and `deposit_rub`. `is_by_homeowner` is True only when the
  owner publishes without an agent; None means Cian did not say.
- **`created_at`** is Cian's local ISO time without a zone; `updated_at` on the
  card is UTC. Do not compare them as if they were in one zone.
- **`views`** on the card is parsed from Cian's own text
  («12907 просмотров, 98 за сегодня»): the first number, total views.

## What the source cannot do (and the connector does not pretend)

- No agent/agency tool: `/agents/<id>/` renders profile facts only as page
  text, with no structured state, so no tool reads it. The publisher's name,
  type, id, offer count and account age ship inside the card's `agent` field.
- No reviews — real estate has no per-offer review pool.
- No geo-suggest: regions and metro are ids, not names (see above).
- No phone numbers: the card knows how many phones the offer has, not their
  values.

## Gotchas
- **403 inside the browser session** (`transport_down` with "WAF block") means
  Cian challenged the scraping profile: open cian.ru in that Chrome, pass the
  check if one is shown, retry. From a datacenter IP a cold session can start
  blocked; a warmed-up session answered 8 requests in a row with no 429.
- **Pace is deliberate**: 1.5 s between requests, one tab at a time. Do not
  hammer pages 1–10 in a loop; ask for what is needed.
- **`parser_drift`** means the envelope or `_cianConfig` shape changed — the
  connector will not hand back a half-parsed card as if it were data. Run the
  canary, then fix the parser.
- **A removed offer** may render as "объявление не найдено" → `not_found`.
- **Verification story:** a green selfcheck proves the transport answered and
  the parser found ids, prices and addresses. It does not prove a particular
  price is current — Cian's `price_history` on the card is the evidence to
  quote when it matters.

## DSH activation

In DeepSeek Harness, the default profile exposes only `compare_prices` and
`compare_sources` through the cheap compare mount. Per-marketplace tools and
`marketplace_sources` require `RU_MARKETPLACE_MCP_FULL=1` and a profile restart;
do not call them in the default mode. Cian is never part of the compare mount,
so it is only reachable in the full profile.
