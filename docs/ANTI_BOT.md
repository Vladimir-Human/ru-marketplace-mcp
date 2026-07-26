# Anti-bot reality, source by source

Every marketplace here was probed live from a datacenter IP in July 2026. This
document records what actually happened — including the four sources that did not
make the release — because that determines what is buildable and what is a trap.

The single most useful finding: **anti-bot posture, not API quality, decides
whether a marketplace is usable.** Wildberries has a messy API and works
perfectly. Megamarket has a clean one and is unreachable.

## Summary

| Marketplace | Datacenter IP | Anti-bot | In release |
|---|---|---|---|
| Wildberries | ✅ works | none observed | ✅ |
| Yandex Market | ✅ works | SmartCaptcha (dormant) | ✅ |
| Detsky Mir | ✅ works | none | ✅ |
| Ozon | ❌ 307 loop | Cloudflare | ✅ via your Chrome |
| Megamarket | ❌ blocked | ServicePipe | ❌ |
| Lamoda | ⚠️ partial | anti-bot redirect loop | ❌ |
| DNS | ❌ 401 | Qrator JS proof-of-work | ❌ |
| Citilink | ❌ 429 | Qrator rate block | ❌ |

## Shipped sources

### Wildberries — no resistance

All three endpoint families answer plain HTTPS with browser-like headers:
`card.wb.ru/cards/v4/detail`, `feedbacks2.wb.ru`, `search.wb.ru` v9.

Two quirks worth knowing:

- **`dest` is mandatory.** Without it you get empty stocks, wrong prices, or
  Cloudflare HTML. Default is `-1257786` (Moscow).
- **Search is 429-prone.** Repeated queries hit a rate limit within a handful of
  requests. It clears in a minute or two. The connector surfaces it as a retryable
  error rather than degrading silently.

The real trap here was not anti-bot but a **stale index**:
`search-goods.wildberries.ru` returns ids for delisted SKUs. See
[the search fix](#the-wildberries-search-trap).

### Yandex Market — captcha present but dormant

Server-rendered pages answer HTTP 200 from a datacenter IP. Search is ~2 MB, a
product page ~2.5 MB, because the whole widget state ships inside the HTML.

- **No JSON API is reachable.** `/api/resolve` → 403. `/api/products/{id}` → 404
  with `content-type: application/grpc+proto`. The old public Content API is dead
  (502). Partner API needs a seller account.
- **SmartCaptcha exists but did not trigger** across 14 rapid requests. It is
  preloaded as an empty widget on every healthy page — which is a trap: matching
  the substring `captcha` flags every successful response. Detect the real thing
  via `SmartCaptcha`, `/showcaptcha`, or `checkbox_captcha`.
- **Transient 302s with an empty body** hit roughly one request in ten. An
  immediate retry succeeds; the connector retries them.

Because extraction is coupled to a front-end, `yandex_selfcheck` matters more here
than for a JSON API. Drift is a question of when.

### Detsky Mir — genuinely open

`api.detmir.ru` answers with no User-Agent at all. No rate limit observed across
rapid sequential requests. Three quirks:

- **HTTP 200 can carry `{"status": 404}`.** Status codes alone cannot be trusted.
- **Listings are `/v4/` only.** The old `/v2/products?filter=` path is gone.
- **Sporadic 502s**, retried automatically.

And one thing that is not a quirk but a genuine absence — see
[the Detsky Mir search trap](#the-detsky-mir-search-trap).

### Ozon — needs your browser

`composer-api.bx` answers a datacenter IP with an endless self-referential 307
redirect loop (`?...&__rr=1`, `__rr=2`, …). curl follows it until it hits its
redirect ceiling. TLS impersonation alone does not clear it.

The working answer is not a better fingerprint but a different vantage point: run
the fetch **inside a browser the operator already logged into**, over the DevTools
Protocol. That is tier 2. Setup and threat model: [CDP_SETUP.md](CDP_SETUP.md).

## Rejected sources

### Megamarket — clean API, hard block

The internal mobile API is the nicest of the lot:

```
POST https://megamarket.ru/api/mobile/v1/catalogService/catalog/search
POST https://megamarket.ru/api/mobile/v1/catalogService/productCard/get
```

It accepts requests and returns valid JSON — but always this:

```json
{"error": "Произошла ошибка. Попробуйте отключить VPN…", "code": 7, "ip": "3.220.149.31"}
```

Echoing our own IP back is an unambiguous reputation block. ServicePipe
(`X-SP-CRID` header, JS challenge on the homepage) gates the whole site. The
best-known open-source parser for this API states plainly that it stopped working
without browser cookies in early 2025.

**Verdict:** a second Ozon, but stricter — needs residential IP *and* cookies from
a browser that has passed the challenge. Reachable in principle through the CDP
tier; not included because it could not be verified end to end.

### Lamoda — prices without discovery

One channel genuinely works anonymously:

```
POST https://www.lamoda.ru/goapi/v2/catalog/graphql/products/
Content-Type: application/json
{"query": "query { products(skus: [\"MP002XM1RMM3\"]) { sku name brand_name price_amount is_available sizes { size is_available } } }"}
```

That returns real prices, brands, availability and sizes. But:

- Catalog and search GET paths return the same self-referential 307 loop as Ozon.
- HTML pages return 403 even with a full browser header set.
- `rating` is not in the schema; introspection is disabled.
- The mobile API (`api.lamoda.ru`) returns 403.

**Verdict:** a connector could enrich a SKU you already have, but could never find
one. A price tool that cannot search is not a marketplace connector, so it was
left out rather than shipped half-working.

### DNS — proof-of-work challenge

Every dynamic page returns **HTTP 401** with a Qrator JS challenge
(`/__qrator/qauth_utm_v2d_v9118.js`, ~349 KB). The valid `qrator_jsr` cookie is
only issued after executing that proof-of-work in a real JS engine; loading the
script does not set it.

- `restapi.dns-shop.ru` (from the client config) returns **403 even from a clean
  residential IP** — it is an internal SSR address.
- `/ajax-state/product-buy/` is the one route *not* behind Qrator (stable 200), but
  returns `null` without a valid CSRF token, which can only be obtained from a page
  that is itself behind Qrator.
- Anonymously reachable: `robots.txt`, `sitemap.xml` (~600k product URLs).

**Verdict:** viable only through a real browser, and even then it is DOM/state
scraping plus CSRF management rather than an API. Not worth the fragility for v1.

### Citilink — Qrator plus gRPC-web

The entire domain returns **HTTP 429**, including `robots.txt` — stricter than DNS.
Subdomains too (`rpc.citilink.ru`, `api.citilink.ru`).

Its data transport is not REST or GraphQL but **gRPC-web**:

```
POST https://rpc.citilink.ru/catalog-site/<Service>/<Method>
metadata: x-citilink-anon-id: <hex>
```

Using it would require reversing the protobuf schema and method names. The public
`github.com/citilinkru` repos are internal infrastructure tooling, not a catalog
client; every working third-party parser drives a real browser.

Anonymously reachable: `sitemap/main/sitemap.xml` (product URL inventory only).

**Verdict:** two hard problems stacked — IP reputation and a binary protocol. Out
of scope.

## Two traps worth their own section

### The Wildberries search trap

`wb_search` originally resolved ids through `search-goods.wildberries.ru`, then
enriched them via `card/v4`. Both endpoints returned HTTP 200 and valid JSON, so
the pipeline looked healthy.

Live check on `"кроссовки мужские"`:

| Path | Products | With a price |
|---|---|---|
| `search-goods` → `card/v4` | 19 | **1** |
| `search.wb.ru` v9 direct | 100 | **100** |

Every id from `search-goods` was a delisted SKU: `totalQuantity: 0`,
`sizes[].price: null`, zero stock entries. The endpoint serves a stale index.

This is the worst failure mode in the project's problem space — **not an error, but
a confident answer with no prices in it.** An error is diagnosable; a plausible
empty result reads as "this product has no offers".

Fix: v9 is now the primary path (one request, prices inline), the old path survives
as a fallback that flags itself in `meta.warnings`, and a page with no prices at all
raises a `no_prices` warning rather than passing silently.

### The Detsky Mir search trap

Detsky Mir's API accepts text filters and ignores them. Every variant returns the
entire catalog:

| Filter | Reported total | First result for "лего" |
|---|---|---|
| `q:лего` | 301,420 | Трусики MANU |
| `phrase:лего` | 301,420 | Трусики MANU |
| `search:лего` | 301,411 | Трусики MANU |
| `text:лего` | 301,411 | Трусики MANU |

The website's `/catalog/search/?q=` route looks more promising — it returns 12
product ids — but it answers **HTTP 404** and those ids come from a promo carousel.
A search tool built on it was implemented, tested live, and produced this for
"лего": nappies, dishwashing liquid, and a collagen supplement. All with correct
prices and ratings, which is what makes it dangerous.

That tool was deleted. `detmir_categories` → `detmir_category` is the honest path,
and the absence is documented in the tool descriptions so an agent does not go
looking for a search tool that should not exist.

## Practical guidance

**Rate limits are real.** WB search rate-limits within a few requests; Yandex is
fine at a steady pace but bursts invite SmartCaptcha. Every connector enforces a
minimum gap by default. Do not disable it.

**A block is not an absence.** `transport_down` or `rate_limited` means "we were
refused", not "the product does not exist there". `compare_prices` keeps these
distinct via `source_outcomes` and `complete`.

**Residential IP changes the picture.** From a Russian residential address, Ozon
tier 1 often works, Lamoda's HTML likely opens, and Citilink's rate block may
clear. Set `*_PROXY` to route through one.

**Run the selfchecks.** `success` / `drift_detected` / `inconclusive` — and note
that `inconclusive` from a geo block says nothing about whether the parsers still
work.
