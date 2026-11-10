# ru-marketplace-mcp

[Русский](README.md) · **English**

[![CI](https://github.com/Vladimir-Human/ru-marketplace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Vladimir-Human/ru-marketplace-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ru-marketplace-mcp.svg)](https://pypi.org/project/ru-marketplace-mcp/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20http-orange.svg)](https://modelcontextprotocol.io)
[![Glama MCP](https://glama.ai/mcp/servers/Vladimir-Human/ru-marketplace-mcp/badges/score.svg)](https://glama.ai/mcp/servers/Vladimir-Human/ru-marketplace-mcp)
[![npm](https://img.shields.io/npm/v/ru-marketplace-mcp-dsh.svg?label=npm%20%28dsh%29)](https://www.npmjs.com/package/ru-marketplace-mcp-dsh)

**MCP servers for Russian and Chinese marketplaces.** Read prices, stock, ratings,
reviews and seller identity from Wildberries, Ozon, Yandex Market, Detsky Mir, Avito,
AliExpress, Taobao, Megamarket, Lamoda, DNS and Citilink, then compare product prices
in one call. Cian adds real-estate search and listing cards separately.

Read-only. No credentials, no API keys, no account required — the marketplaces with
hard anti-bot are read through your own Chrome. One optional exception: MPStats
takes a paid account token (`MPSTATS_MP_AUTH`) if you want its analytics; without
it every other server is unaffected.

<p align="center">
  <img src="docs/assets/demo-compare.svg" width="760"
       alt="compare_prices(&quot;кроссовки мужские&quot;) (&quot;men's sneakers&quot;): Wildberries and Yandex Market offers sorted by price, cheapest wildberries 712 RUB">
</p>

<sub>Example call from the [cross-marketplace section](#cross-marketplace--compare_); product names are as the marketplaces list them.</sub>

```console
uvx ru-marketplace-mcp setup
```

Installs from PyPI and wires the detected client (`--dry-run` shows the plan and writes nothing). Or in one click: [![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_MCP-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=marketplace&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22ru-marketplace-mcp%22%2C%22compare-mcp%22%5D%7D) [![Install in Cursor](https://img.shields.io/badge/Cursor-Install_MCP-000000?logo=cursor&logoColor=white)](https://cursor.com/en/install-mcp?name=marketplace&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJydS1tYXJrZXRwbGFjZS1tY3AiLCJjb21wYXJlLW1jcCJdfQ==) · details in [Quickstart](#quickstart).

[First successful query](docs/QUICKSTART.md) walks through installation, choosing
a server, targeted health checks, and verifying an offer before recommending it.

**2.7.0 verification:** on 30 October, from one address, Wildberries fell back to its rendered search page after the API refused (HTTP 403) and returned 40 results, all priced. The other sources' code is unchanged since v2.6.0; they were checked in the [v2.6.0 run](docs/releases/RELEASE_NOTES_v2.6.0.md) the same day (12 of 13 sources; MPStats has no token). [v2.7.0 details](docs/releases/RELEASE_NOTES_v2.7.0.md).

## What you get

| Server            | Tools | What it takes to read                                                         | Notes                                                                                              |
| ----------------- | ----- | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **Wildberries**   | 8     | anonymous HTTP                                                                | Search, cards, reviews, buyer questions, seller legal identity, catalog tree and category listings |
| **Yandex Market** | 2     | anonymous HTTP                                                                | Multi-seller prices, star distribution, reviews                                                    |
| **Detsky Mir**    | 3     | anonymous HTTP                                                                | Kids' goods, offline store stock, category listings                                                |
| **Ozon**          | 3     | your Chrome; often no browser from a residential IP                           | Search, cards, reviews                                                                             |
| **Avito**         | 3     | your Chrome + a Russian residential IP and spaced requests — else an IP block | Classified search, cards, seller reputation                                                        |
| **Taobao**        | 2     | your Chrome with an active Taobao login                                       | Search and cards, prices in yuan                                                                   |
| **Megamarket**    | 2     | your Chrome with an active login — an anonymous session reads empty           | Search and cards via the mobile API                                                                |
| **Lamoda**        | 2     | cards: HTTP GraphQL → Chrome on refusal; search via Chrome                             | Search, cards with sizes                                                                           |
| **DNS**           | 2     | your Chrome (Qrator)                                                          | Electronics search and cards                                                                       |
| **Citilink**      | 2     | your Chrome (Qrator)                                                          | Electronics search and cards                                                                       |
| **AliExpress**    | 2     | your Chrome (x5sec)                                                           | Search and cards, ruble prices                            |
| **Cian**          | 2     | your Chrome (WAF by IP)                                                      | Real estate: filter search (sale, long-term rent, daily) and one offer's card                      |
| **Compare**       | 4     | aggregates the above                                                          | "Where is this cheapest?" in one call                                                              |
| **MPStats**       | 2     | paid MPStats account, `mp_auth` cookie (optional)                             | 30-day sales/stock graphs per Ozon/WB SKU, warehouse split (FBS/FBO)                               |

Anonymous, no browser: Wildberries, Yandex Market and Detsky Mir. Lamoda cards
try HTTP GraphQL first and need your Chrome (CDP) only for the fallback tier.
The rest need your logged-in Chrome (CDP). Taobao and Megamarket additionally need
you signed into the marketplace itself — without it Taobao hits a login wall and
Megamarket returns an empty result. Avito also blocks by IP: from a datacenter
address it is a flat refusal, from a Russian residential one it works as long as
you do not burst requests. Requests to the CDP sources are paced apart — a run of
back-to-back calls degrades them (DNS and Taobao both dropped that way in testing),
so the connectors hold a gap between calls themselves. Run `marketplace-mcp doctor`
from your own session for the current state.

MPStats stands apart as the only **paid** source: without `MPSTATS_MP_AUTH` the
unified `marketplace-mcp` does not mount its tools at all — they would only
answer `auth_missing` — so it is optional; the other thirteen servers never
notice. The standalone `mpstats-mcp` still boots and answers `auth_missing`.

39 tools across 14 stdio MCP servers, sharing one runtime (`mcp-core`), plus the
unified `marketplace-mcp` that mounts them all under one client entry. It adds two
tools of its own — `marketplace_sources` (which connectors mounted, and which dropped
out and why) and `marketplace_health` (is Chrome up, which hosts are cooling down,
which page unblocks a source) — so it exposes 41 tools with an MPStats token and 39
without one: the 37 mounted sources plus those two when the two `mpstats_*` tools are
left out. stdio is the default;
HTTP transport is opt-in for remote deployment — see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

Without a checkout, install from PyPI and wire the detected client in one step
(`--dry-run` shows the plan and writes nothing):

```console
uvx ru-marketplace-mcp setup
```

Publishing to PyPI (maintainer): [DEPLOYMENT.md — PyPI](docs/DEPLOYMENT.md#pypi-maintainer).

From source, for development:

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"    # 3442 offline tests, no network needed
```

Client configuration mirrors the [Russian section](README.md#подключение-к-mcp-клиенту). The shortest path is the
guided setup:

```console
marketplace-mcp setup
```

It detects Claude Desktop, Cursor and Claude Code, writes the config with the
compare profile by default, starts the scraping Chrome, runs doctor's checks and
offers `unblock` for a walled source. `--dry-run` prints the steps and files
without writing, starting or requesting anything.

To write one named client's config instead, let the CLI write the file:

```console
marketplace-mcp install claude --write
```

It adds one `marketplace` entry to the client's own config (Claude Desktop:
`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`~/.config/Claude/claude_desktop_config.json` on Linux; Cursor: `~/.cursor/mcp.json`).
By default that entry runs `compare-mcp` — the `compare` profile: 4 price-comparison
tools, the cheapest per request. `--profile decision` adds shortlist card
inspection (5 tools); `--profile full` runs `marketplace-mcp` with every per-source
tool, several times the cost (measured per profile in [docs/TOOLS.md](docs/TOOLS.md)).
The entry name stays `marketplace`. `uv` and your checkout
path are filled in as absolute paths, other servers and settings in the file stay
untouched, and the old file is kept as `<file>.bak-<timestamp>`. Restart the client
afterwards; `--dry-run` shows the result without writing, and `--per-source` writes
one entry per source instead (it cannot be combined with `--profile`). Each server is
also a console script (`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `aliexpress-mcp`,
`cian-mcp`, `compare-mcp`) launched through `uv run --directory /path/to/repo <script>`.
The optional `mpstats-mcp` runs the same way with `MPSTATS_MP_AUTH` in the entry's
`env` (paid MPStats account; without it the tools return `auth_missing`).
`marketplace-mcp install [claude|claude-code|cursor|dsh]` prints the selected profile's
`marketplace` entry with your checkout's real path filled in — no placeholder to
hand-edit — or the console-script path on PATH when installed as a wheel; an unknown
client name or profile is rejected. The `dsh` target prints a `cordis.patch.yml` row
instead of `mcpServers` JSON — see [dsh/README.md](dsh/README.md).

If you prefer to paste the config yourself, start with this one entry (replace the
path with your clone from above):

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

The browser tier has more verbs: `marketplace-mcp browser start` (launch the
scraping Chrome; on-screen by default, `--hidden` for off-screen),
`marketplace-mcp browser status` (is the CDP endpoint reachable, which Chrome
answers, how many page targets it holds and where its window is) and
`marketplace-mcp browser show` (bring the scraping Chrome window on-screen — the
connector's autostart parks it off-screen on Windows, and without that you can
neither log in by hand nor pass a site's check). When `doctor` reports a wall,
`marketplace-mcp unblock <source>` opens that page in a new tab, waits while you
pass the check or log in, and re-checks only that source. None of these sends a
request to a website on its own and none solves the check for you.

**DeepSeek Harness (dsh)** installs as a plugin bundle rather than an `mcpServers`
entry, from the [`dsh/`](dsh/README.md) subdirectory (`pnpm` must be on PATH):

```console
dsh plugin --profile web add ru-marketplace-mcp-dsh
# or directly from GitHub:
dsh plugin --profile web add github:Vladimir-Human/ru-marketplace-mcp#path:/dsh
```

That gives you 15 skills immediately and **no** MCP tools: all three MCP rows stay
disabled until `RU_MARKETPLACE_MCP_DIR` points at a clone.
<!-- tools-cost:start -->
A mounted server is paid on every request — ≈1.7k tokens for the
recommended price-comparison profile, ≈2.0k for the decision profile and
≈13.4k for the full set (estimated from `tools/list`; details in
[docs/TOOLS.md](docs/TOOLS.md)) — so opting in is left to you.
<!-- tools-cost:end -->
[dsh/README.md](dsh/README.md) covers enabling it and the full mode.

After connecting, run `marketplace-mcp doctor`. Its exits are `0` all selected
checks healthy, `1` parser drift, `2` acknowledged block or unverified source,
`3` status-file write failure, and `4` checker or doctor setup/usage failure
(including an unknown source). Nightly alarms on `1` and `4`, not `2`. A budget
refusal carries `budget_refused` and never counts as a healthy check.

## The tools

Generated per-tool reference with parameters and context cost:
[docs/TOOLS.md](docs/TOOLS.md).

The `*_selfcheck` canaries are deliberately absent from these tables: they are not
published over MCP, because operator diagnostics would cost the model ~7.5k tokens
on every request. `marketplace-mcp doctor` runs them all from the command line.

### Wildberries — `wb_*`

| Tool                                                   | What it does                                                |
| ------------------------------------------------------ | ----------------------------------------------------------- |
| `wb_search(query, page)`                               | Text search, up to 100 products/page with prices and stock  |
| `wb_card(nm_ids)`                                      | Batch lookup for up to 100 known SKUs                       |
| `wb_root_info(nm_id)`                                  | Resolves `imt_id` (needed for reviews) plus colour variants |
| `wb_reviews(imt_id, limit, sort)`                      | Review pool, keyed by `imt_id`, not `nm_id`                 |
| `wb_questions(imt_id, limit, skip, answered_only)`     | Buyer questions with seller answers, also keyed by `imt_id` |
| `wb_seller(supplier_id)`                               | Registered entity, INN, KPP, OGRN, legal address            |
| `wb_categories(root, max_depth)`                       | Catalog tree with WB's own shard/query selectors            |
| `wb_category_products(shard, query, page, sort, dest)` | Products in a category, using those selectors               |

`wb_seller` answers the question a listing hides: who actually ships this? It returns
the registered legal entity and tax ids, which is how you distinguish an official
brand store from a reseller trading under a lookalike name.

`wb_questions` covers a different gap. Reviews describe what owning the product is
like; questions clarify what it actually is — "10A or 16A?", "is the cable
included?" — and the seller's reply is often the only public statement of that fact.
One pool per `imt_id`, shared across every variant.

`wb_category_products` closes the loop `wb_categories` opens: that tool hands back
WB's `shard` and `query`, and this one fetches the products behind them. Items use
the same shape as `wb_search`, so a category walk and a text search are directly
comparable. Several of WB's largest sections carry the shard `blackhole` and have no
feed at all; the tool says so instead of returning an empty list.

If WB's API answers 403 from your address (the refusal depends on both address and
client), `wb_search` and `wb_card` re-read the same request through WB's rendered
page in your Chrome over CDP and add a `dom_fallback` warning to `meta.warnings`.
This is on by default; `WB_DOM_FALLBACK=0` restores the previous behaviour, while
`WB_TRANSPORT=dom` still reads only rendered pages, with no API calls. On search
tiles `price_rub` is the WB Wallet price (`price_kind: wallet`); the card reports
the regular, Wallet and struck prices separately, plus the store, warehouse,
delivery date, return policy, warranty, the «Оригинал» badge and «Все N
предложений от X ₽». In this mode `marketplace-mcp doctor` also opens two pages
in your Chrome — the baseline card and search grid — for `card` and `search_v9`.
A captcha is `inconclusive`, never solved or retried; reviews, basket and the legacy
probe still use HTTP. Details in
[ANTI_BOT.md](docs/ANTI_BOT.md#when-the-api-refuses-rendered-pages-wb_transportdom).

### Yandex Market — `yandex_*`

| Tool                                       | What it does                                |
| ------------------------------------------ | ------------------------------------------- |
| `yandex_search(query, page, limit)`        | Search with both prices, ratings, sellers   |
| `yandex_card(product_id, include_reviews)` | Full detail plus star breakdown and reviews |

**Two prices, always.** `price_rub` is what anyone pays. `price_with_plus` needs a
paid Yandex Plus subscription and runs 25–30% lower. Yandex leads with the subscriber
price, so quoting it uncritically misstates the real cost.

**A search row is the SERP's offer, not the card's default offer.** One `product_id`
covers a product family, and search may show one member while the card for the same id
defaults to another — reconcile rows with cards by `sku_id`, not by URL. `price_old_rub`
on search rows is the struck-through reference price, never a price to quote.

`rating_stars` gives the distribution, for example `{1: 10, 2: 3, 3: 10, 4: 19, 5: 502}`.
That reveals whether a 4.8 average is earned or hides a cluster of complaints.

### Detsky Mir — `detmir_*`

| Tool                                            | What it does                                  |
| ----------------------------------------------- | --------------------------------------------- |
| `detmir_categories(parent, limit, region)`      | Catalog tree, start here                      |
| `detmir_category(alias, limit, offset, region)` | Products in a category, with real totals      |
| `detmir_card(product_id, region)`               | Price, rating, online and offline store stock |

**Region is per call.** Prices and especially offline availability swing by city —
one item sat in 152 Moscow stores, 37 in St Petersburg, 2 in Khabarovsk. The
`region` parameter overrides `DETMIR_REGION`, so one session can compare cities.

**There is no text search, deliberately.** Detsky Mir's API silently ignores every
text filter and returns its entire 300k-item catalog; the website's search route
answers 404 and renders a promo carousel. A search tool would return confidently
wrong products, so discovery goes through categories instead. See
[docs/ANTI_BOT.md](docs/ANTI_BOT.md).

### Ozon — `ozon_*`

| Tool                                     | What it does   |
| ---------------------------------------- | -------------- |
| `ozon_search(query)`                     | Text search    |
| `ozon_card(sku_or_path)`                 | Product detail |
| `ozon_reviews(sku_or_path, limit, sort)` | Reviews        |

Ozon rejects datacenter traffic, so this connector is two-tier: TLS impersonation
first, then a fetch inside your own logged-in Chrome over the DevTools Protocol when
Cloudflare challenges. Nothing is stored; you log in yourself, in a browser you
control. Setup: [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

From a Russian residential IP the first tier usually works and no browser is needed.

**Ozon pools reviews per card family, and the neighbours are often a different
product from a different brand.** A live card for a 1500 W Huter oil heater (SKU
5264146973, rated 4.8 across 356 reviews) returned 100 reviews of which *zero* were
about the Huter: 38 about a 2000 W Resanta, 34 about a 1500 W Resanta, 5 about a
Eurolux — 12 products in that pool. So every review carries `item_id`, the SKU it is
actually about, and the response adds `requested_item_id`, `own_reviews` (how many
returned reviews really are about that SKU) and `pool_variants` (`SKU → name` for the
whole pool). `rating_score` and `distribution` are pool-wide, not per product: filter
by `item_id` before concluding anything, and when `own_reviews` is 0, say plainly
that the product has no reviews of its own.


### AliExpress — `aliexpress_*`

| Tool                                  | What it does                              |
| ------------------------------------- | ----------------------------------------- |
| `aliexpress_search(query)`            | Search: up to 48 tiles with ruble prices  |
| `aliexpress_card(item_id_or_url)`     | Card: title, price, rating, order count   |

Read through your Chrome (CDP): x5sec challenges anonymous clients, so the
connector lands on a search page (never challenged) and opens the card in a new
tab from it. Prices are rubles and rank in `compare_prices`. A card with a title
but no price is a known state — under load x5sec stops serving the price module
and the connector reports `price_missing` rather than inventing a number. A
"with coupon" price never lands in `price_rub`: the regular price does, and the
coupon is reported as a warning. Review texts are not exposed; rating and order
counts are. As with every CDP source, a green `aliexpress_selfcheck` proves the
transport answered — not that a given price is right.

### Cian — `cian_*`

| Tool                                                                                        | What it does                                                    |
| ------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `cian_search(deal, offer_type, region, rooms, price_min, price_max, area_min, area_max, page)` | Search by filters — sale, long-term rent, daily: 28 offers per page |
| `cian_card(offer_id_or_url)`                                                                | One offer: price and its history, layout, building, address, metro, publisher |

Real estate, not goods: flats, rooms, houses and commercial property for sale,
long-term rent or daily rent (`deal="daily"`; Cian has no daily commercial
market and that combination is refused). Long-term and daily are separate
markets that never share a result page: a daily price is per night and Cian
leaves `price_period` and `lease_term` null there, so every row carries
`price_unit` — `total`, `month` or `day`. Never compare prices across units.
Search is by filters only — Cian has no text search. The region
is a Cian id: 1 Moscow, 2 St. Petersburg, 4593 Moscow oblast, 4588 Leningrad
oblast (all four verified live); other regions need their own id. Read through
your Chrome (CDP): Cian's WAF blocks plain HTTP by IP, while inside the browser
session the site's own JSON API answers, so no HTML is parsed. A price Cian does
not state arrives as `null`, never `0`. There is no agent tool: the agent page
exposes no structured data, and the publisher ships inside the card. The source
takes no part in `compare_prices`.

### Avito — `avito_*`

| Tool                                              | What it does                                      |
| ------------------------------------------------- | ------------------------------------------------- |
| `avito_search(query, page, location_id, category_id, include_inactive)` | Classified search through the internal `js/items` API; `availability` from `isReserved`; reserved hits kept, `include_inactive=false` drops them (`excluded_inactive`) |
| `avito_card(item_id_or_url, photos)`              | CDP item page: title, price, description, image count, seller (rating, profile link), up to 3 photos of an active ad on request (`photos`, costly), reservation `availability` (active/reserved/unknown) from the page's service flags, never seller text, deal and delivery terms, seller checks, city and stations (street address for companies only), date, views |
| `avito_seller(seller_id_or_url)`                  | Seller rating, review count; active listing count unavailable (`null`)       |

Avito is classifieds, not a catalog: there is no per-product review pool, the
seller's reputation IS the trust signal. A free/swap listing arrives with
`price_rub: null` — never `0`, so it cannot win "cheapest". Search uses TLS
impersonation with Chrome fallback. Cards read the item page's schema.org Product
and seller block over CDP; `seller` carries reputation and the profile link when
that block is available. As of 2026-10-12 `/web/1/js/items` ignores both `itemId`
and `userId`: different sellers receive the same generic catalog with `totalCount: 1500`,
not their listings. Seller reads therefore use the rendered profile over CDP,
with the same pacing and allowed hosts as cards. Accepted paths are
`/brands/<hash>` and `/user/<hash>/profile`, or a bare id: 32 hex characters map
to `brands`, other ids to `user`. Unavailable `active_items` remains `null`.
Full card URLs lose query/fragment; ids use `/{id}`, which Avito redirects to the
item page. `location`, `posted_at` and `views` stay `null`, absent from Product
JSON-LD. Missing Product, or both name and price missing, raises `parser_drift`,
not empty success. The canary follows search → card → seller profile. Datacenter
IPs can still receive the 403 firewall.

### Taobao — `taobao_*`

| Tool                            | What it does             |
| ------------------------------- | ------------------------ |
| `taobao_search(query, page)`    | Catalog search           |
| `taobao_card(item_id_or_url)`   | Product card             |

Taobao search is a signed-mtop React app: every request needs a `sign` derived
from a cookie token, so there is no anonymous path. All reads run inside your
Chrome, where the site signs its own requests. **Prices stay in yuan (CNY)** and
are never converted — a baked-in rate quietly goes stale, so compare ruble and
yuan listings explicitly.

### Megamarket, Lamoda, DNS, Citilink

These four read through your Chrome (CDP). Megamarket (`megamarket_*`) goes
through the mobile JSON API behind ServicePipe and needs an active login — an
anonymous session reads empty. DNS (`dns_*`) and Citilink (`citilink_*`) render
DOM behind Qrator with no anonymous path at all. Lamoda (`lamoda_*`) is split:
search through Chrome, cards over HTTP GraphQL first (tier 1). A non-200 response
or `httpx.TransportError` triggers exactly one in-page GraphQL POST through your
Chrome over CDP (tier 2); successful fallback reads report `tier_used: graphql_cdp`.
HTTP 200 skips CDP. The reason is the 2026-10-12 307 loop from GraphQL over HTTPS.
All of them need Chrome with CDP (`scripts/start_chrome_cdp.sh`), but Lamoda cards
only need it for the fallback. Your everyday Chrome can stay open: the launcher
starts a second Chrome with its own `--user-data-dir`, on-screen. If the window
did go off-screen (the connector's autostart on Windows), bring it back (to log
in by hand or pass a site's check) with `marketplace-mcp browser show`. Nine
sources run through CDP in total: these plus
Taobao, AliExpress, Cian, Ozon and Avito; for Ozon and Avito, Chrome is also only the
fallback tier when the anonymous one is challenged.

### Cross-marketplace — `compare_*`

| Tool                                               | What it does                              |
| -------------------------------------------------- | ----------------------------------------- |
| `compare_prices(query, per_source_limit, sources)` | Every marketplace at once, ranked         |
| `compare_sources()`                                | Which marketplaces this install can query |

```
compare_prices("кроссовки мужские")

  wildberries      712 RUB   Кроссовки изи дышащие спортивные
  wildberries      814 RUB   Зимние кроссовки теплые с мехом
  yandex_market   2499 RUB   Кеды A-LOW
  yandex_market   3480 RUB   Кеды

  cheapest: wildberries 712 RUB, spread 2768 RUB, complete: true
```

Sources are queried concurrently and each reports its own outcome. One marketplace
being blocked never sinks the comparison: `complete: false` plus `source_outcomes`
tells you exactly what you are looking at. Subscription prices never win the ranking.
WB Wallet and other explicitly conditional prices cannot become `cheapest` or
`cheapest_comparable` either; when a regular price is available, that price ranks.
`price_kind` retains the kind (`regular`, `wallet`, `subscription`); `null` means
unreported. A `conditional_price: …` warning names the condition and excluded amount.
Repeats matching on (source, product id, variant id) are collapsed. Distinct Yandex
SKUs remain visible within one product family. Comparison stock is tri-state:
`true` means confirmed, `false` means unavailable, and `null` means unknown.
With `in_stock_only=true`, only confirmed stock can win; unreported WB quantities
and ambiguous Ozon labels remain visible at the end of the list.

Every offer carries `currency` (lowercase ISO code, default `rub`) and `price_native`,
the price in that currency as the marketplace quotes it. For Russian sources it mirrors
an available everyday `price_rub`; if only a conditional price is known, that amount
stays in `price_native` while `price_rub` is null. For Taobao it holds the yuan price that `price_rub` deliberately leaves
null. That yuan price used to be fetched and silently thrown away, so a Taobao row
showed a blank price with no sign a real one existed. Now the yuan is reported but
still never ranked against roubles: a `foreign_currency: …` warning lists how many
offers were excluded and why. Taobao's `source_outcomes[].detail` counts
`price_native`, for example `20 results, 20 priced in CNY (not ranked)`. Multiple
priced currencies are sorted and joined with `/`; no native prices gives
`N results, 0 priced (not ranked)`. Rouble sources keep `N results, M priced`.
Converting here would bake in an exchange rate that goes stale silently, so the
caller converts if they want to.

### MPStats — `mpstats_*`

Sales and stock analytics per Ozon or Wildberries SKU via the MPStats browser
plugin. Unlike every other connector, this one is **optional and needs a paid
MPStats account**: auth is a single `mp_auth` cookie (JWT from a logged-in plugin
session at mpstats.io), set via the `MPSTATS_MP_AUTH` env var. Without it the
standalone `mpstats-mcp` boots normally and answers `auth_missing`, while the
unified `marketplace-mcp` does not mount the two tools, so no request pays for
schemas that could only fail.

| Tool                                     | What it does                                                                            |
| ---------------------------------------- | --------------------------------------------------------------------------------------- |
| `mpstats_item(skus, place, oz_fbs=True)` | 30-day analytics for up to 100 SKUs: orders, price, stock, per-day graphs, seller/brand |
| `mpstats_warehouses(skus, place)`        | Warehouse split: FBS (seller's warehouse) vs FBO (marketplace warehouse), `last_update` |

`place` is `ozon` or `wildberries`. Graphs are length 30, oldest first: the last
non-zero cell is the current price or stock. The two differ on purpose when the
whole graph is zero: price becomes `None` (a false `0` would win any "cheapest"
comparison), while stock becomes `0`, because "none in stock" is a real reading
rather than an absence of data. An empty graph yields `None` for both. A zero
cell means "no data for that day", not "the value was zero", so sum the graph for
a window total. A missing token or a transport failure reports
as `inconclusive`, not `drift` — no chasing a schema drift that never happened.
The token is a secret on a paid, quota-billed account: never log or commit it.

## Agent skills

Every connector ships its own skill under `skills/` — fifteen of them — one per source plus a shared
`marketplace` overview. A skill is not a restatement of this README: it tells the agent when to
reach for that source at all, what the source does not have, and which of its
answers should not be trusted without a second look.

| Skill                         | Server            |
| ----------------------------- | ----------------- |
| `skills/wb-connector`         | `wb-mcp`          |
| `skills/ozon-connector`       | `ozon-mcp`        |
| `skills/yandex-connector`     | `yandex-mcp`      |
| `skills/detmir-connector`     | `detmir-mcp`      |
| `skills/avito-connector`      | `avito-mcp`       |
| `skills/taobao-connector`     | `taobao-mcp`      |
| `skills/megamarket-connector` | `megamarket-mcp`  |
| `skills/lamoda-connector`     | `lamoda-mcp`      |
| `skills/dns-connector`        | `dns-mcp`         |
| `skills/citilink-connector`   | `citilink-mcp`    |
| `skills/aliexpress-connector` | `aliexpress-mcp`  |
| `skills/cian-connector`       | `cian-mcp`        |
| `skills/compare-prices`       | `compare-mcp`     |
| `skills/mpstats-connector`    | `mpstats-mcp`     |
| `skills/marketplace`          | `marketplace-mcp` |

`mcp-core` is the shared runtime rather than a server, so it has no skill.

The mapping is enforced by a test
(`packages/marketplace-connector/tests/test_skills_parity.py`): a new connector
without a skill fails the run, and so does a skill that names a tool which does
not exist — or omits one that does. Before that test existed, the DNS skill spent
months telling operators to pass `/product/<24-hex>/`, the exact pattern a fix had
already removed.

Skills are copied into the Docker image (`/app/skills/`), but they are **not in
the wheels**: `skills/` lives at the repository root rather than inside the
packages. Release-wheel installs need the skills fetched from the repo separately;
the `ru-marketplace-mcp` package on PyPI does not include them either.

## Configuration

Every setting is an environment variable with a per-connector prefix. All optional.

| Prefix               | Common knobs                                                                                                               |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `WB_`                | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES`, `CACHE_TTL`, `PROXY`                                |
| `YANDEX_`            | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY`                                                                                 |
| `DETMIR_`            | `REGION` (`RU-MOW`, `RU-SPE`, and others), `CACHE_TTL`, `PROXY`                                                            |
| `OZON_`              | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY`                                                                  |
| `AVITO_`             | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY`, `LOCATION_ID`                                                   |
| `TAOBAO_`            | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`,                                                                                         |
| `ALI_`               | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`                                                                                          |
| `MEGAMARKET_`        | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `USE_PROFILE_ADDRESS` (0/1, default 0 — the profile's private address list is not read; 1 = the logged-in profile's default address, prices "as the operator sees them") |
| `LAMODA_`            | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY`                                                                                 |
| `DNS_` / `CITILINK_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`                                                                                          |
| `CHROME_`            | `CDP_HOST`, `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH`                                                |
| `COMPARE_`           | `SOURCE_TIMEOUT`                                                                                                           |
| `MPSTATS_`           | `MP_AUTH` (the only required one — without it the tools return `auth_missing`), `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `MCP_`               | `TRANSPORT` (`stdio` default, or `http`), `HTTP_HOST`, `HTTP_PORT`                                                         |

`*_CACHE_TTL=0` disables caching. `*_PROXY` overrides the standard
`HTTPS_PROXY`/`ALL_PROXY` — seven connectors carry one: `WB_`, `YANDEX_`, `DETMIR_`,
`OZON_`, `AVITO_`, `LAMODA_` and `MPSTATS_`. Taobao has none by design and
Megamarket, DNS and Citilink none either:
their traffic goes through your own Chrome, whose egress is that browser's
configuration. Only successful reads are cached: remembering a failure would stretch
a one-second blip across the whole TTL window.

Ozon's proxy applies to tier 1. Tier 2 runs inside your own Chrome, whose egress is
that browser's configuration, not ours.

**Containers.** `CHROME_CDP_HOST` points the CDP client at Chrome (default
`127.0.0.1`; use `chrome` or `host.docker.internal` inside Docker). That single
variable is what opens the tier-2 sources — Ozon, Avito, Taobao, Megamarket,
Lamoda, DNS, Citilink, AliExpress and Cian — from a container without host networking.
See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

**One secret, and it is optional.** Every server except MPStats needs nothing:
nothing to configure, nothing to leak. MPStats alone has `MPSTATS_MP_AUTH`, the JWT
of a paid account — it belongs only in the client entry's env, never in code or
commits.

**Only the sources you use: `MARKETPLACE_SOURCES`.** The unified server mounts every
source, and their tool schemas are sent to the client on every request. Set
`MARKETPLACE_SOURCES` in the `marketplace-mcp` entry's env to a comma-separated list
to mount only those, e.g. `wildberries,ozon,yandex_market,avito,aliexpress,dns,compare`.
Names are canonical (`wildberries`, `ozon`, `yandex_market`, `detsky_mir`, `avito`,
`taobao`, `megamarket`, `lamoda`, `dns`, `citilink`, `aliexpress`, `cian`, `compare`,
`mpstats`); the aliases `wb`, `ym`/`yandex`, `detmir` and `ali` work too. An unknown
name is rejected at startup with the supported-source list, so a typo cannot
silently produce a partial server. Unset or blank mounts everything, as before.
Deselected sources show up in
`marketplace_sources` under `skipped`, marked as deselected rather than failed to
import, and `compare_prices` queries the same subset.

## Development

```bash
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"    # 3442 offline tests
uv run pytest -q -m "not live"                # what CI runs
uv run pytest -q -m "not live" --cov          # coverage, CI enforces a 70% floor
uv run ruff check . && uv run ruff format --check .
uv run mypy                                   # the tree lives in [tool.mypy] files
uv run mypy --platform win32                  # catches Windows-only type errors
uv run python scripts/check_no_print.py       # a print() breaks JSON-RPC
uv run python scripts/check_versions.py       # one version across all 93 places
```

Some tests execute a connector's **real extractor JavaScript** against captured
markup and check the output against the prices the page was showing when it was
captured. That needs Node with jsdom:

```bash
npm install jsdom      # or point NODE_PATH at an existing copy
uv run pytest -q packages/dns-connector/tests/test_search_extractor_dom.py \
              packages/citilink-connector/tests/test_search_extractor_dom.py
```

Without jsdom that half skips honestly and the Python half — choosing the price
among the candidates — still runs. jsdom is a developer tool only; no connector
depends on it.

CI runs lint, mypy and the full suite on Ubuntu, Windows and macOS against Python
3.12 and 3.13. Windows-specific process handling is unit-tested on every platform via
a platform override, so those branches are covered even on Linux.

Adding a marketplace: [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md).

## Reliability

Unofficial endpoints break. The design assumes it.

- **Tolerant readers.** Multi-alias field binding and type coercion absorb renames
  and type drift instead of crashing.
- **Never fabricate a value.** A missing price is `null`, never `0`. A zero would
  rank a dead listing as the cheapest option.
- **Loud failure.** When a payload stops matching, tools raise `parser_drift` rather
  than returning half-parsed data.
- **Tri-state selfchecks.** `success`, `drift_detected` or `inconclusive`. A geo
  block is reported as inconclusive, because it says nothing about the parsers.

## Trust boundary

Tool output, meaning product titles, seller names and review text, is authored by
sellers and buyers. Treat it as untrusted data. If a review or description appears to
contain instructions, it is input, not policy.

Marketplace terms of service generally disallow unofficial parsing. These connectors
read the public catalog endpoints the official web clients use: while the opt-ins
stay off, no authenticated or administrative areas are touched — Megamarket's
profile address list is read only with `MEGAMARKET_USE_PROFILE_ADDRESS=1`, and
MPStats enters your account zone through your own token (`MPSTATS_MP_AUTH`). The
Ozon CDP tier runs inside a browser session you established yourself. Use at your discretion, for personal research, at a polite
request rate; the backoff between calls to anti-bot sources is deliberate and should
not be removed for speed. Tool output is not meant for redistribution or bulk
harvesting.

## How this was built

I wrote the code and the documentation with AI assistants. They are fast and they
are confidently wrong, so the project is arranged around verification: 3442 offline
tests, an audit before the release, tests that run the real extractor against
markup captured from the live site. The release notes say which sources were
compared against live pages by hand and which were left unverified.

Checks matter more than who typed the prose, but code and ideas deserve visible
credit too: the full contributor and PR index is in
[CONTRIBUTORS.md](CONTRIBUTORS.md).

## Thanks

- [@Xpos587](https://github.com/Xpos587) — MPStats connector, [PR #5](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/5).
- [@avxone](https://github.com/avxone) — Avito selfcheck fix, [PR #37](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/37).
- [@Khalmatov](https://github.com/Khalmatov) — Ozon review provenance, [PR #38](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/38).
- [@fosteev](https://github.com/fosteev) — macOS CDP stealth and the Cian connector, [PR #42](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/42), [PR #47](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/47).
- [@ilodezis](https://github.com/ilodezis) — unified-server source selection, [PR #48](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/48).
- [@HeckfyCU](https://github.com/HeckfyCU) — AliExpress navigation timing measured on a Russian residential IP, [issue #190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190): those measurements located the page-readiness deadline that has been fixed.
- [@XaruStar](https://github.com/XaruStar) — live Ozon `webListReviews` report on decimal-string `itemId`, [issue #214](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/214): fixed review SKU attribution (`own_reviews`) and `ozon_selfcheck` `item_id` drift detection.

## License

MIT, see [LICENSE](LICENSE).
