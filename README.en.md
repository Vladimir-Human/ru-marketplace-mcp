# ru-marketplace-mcp — MCP Servers for Russian & Chinese Marketplaces

[Русский](README.md) · **English** · [AI Agent Install Guide](llms-install.md) · [llms.txt](llms.txt)

[![CI](https://github.com/Vladimir-Human/ru-marketplace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Vladimir-Human/ru-marketplace-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ru-marketplace-mcp.svg)](https://pypi.org/project/ru-marketplace-mcp/)
[![npm](https://img.shields.io/npm/v/ru-marketplace-mcp-dsh.svg?label=npm%20%28dsh%29)](https://www.npmjs.com/package/ru-marketplace-mcp-dsh)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20http-orange.svg)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Live prices, stock, ratings, reviews, and seller legal identity across 12 Russian
and Chinese platforms: **Wildberries**, **Ozon**, **Yandex Market**, **Avito**,
**AliExpress**, **Taobao**, **DNS**, **Citilink**, **Megamarket**, **Lamoda**,
**Detsky Mir**, plus **Cian** real estate.

`compare_prices` ranks product prices across marketplaces in one call, separates
accessories and flash drives from the actual product (`cheapest_comparable`), and
keeps conditional wallet/card discounts (`WB Wallet`, `Yandex Pay`, AliExpress
coupons) out of regular-price rankings. Read-only, with no API keys or seller
accounts: open endpoints use anonymous HTTP, and anti-bot-gated sites run through
your own dedicated Chrome (Taobao and Megamarket need a shopper login in that
browser).

<p align="center">
  <img src="docs/assets/demo-compare.en.svg" width="560"
       alt="compare_prices(&quot;iphone 16 128gb&quot;): 1,531 RUB USB drive and 56,833 RUB WB Wallet price excluded; cheapest_comparable picks Apple iPhone 16 at 64,990 RUB on AliExpress">
</p>

<sub>Live replay from <a href="#a-real-answer">A real answer</a> (`c1-iphone16-128gb.json`); product names match the marketplaces.</sub>

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/). Without a
checkout, one command installs from PyPI, detects your MCP clients, and wires the
connection (`--dry-run` shows the plan and writes nothing):

```console
uvx ru-marketplace-mcp setup
```

Or install in one click:

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_MCP-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=marketplace&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22ru-marketplace-mcp%22%2C%22compare-mcp%22%5D%7D)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_MCP-000000?logo=cursor&logoColor=white)](https://cursor.com/en/install-mcp?name=marketplace&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJydS1tYXJrZXRwbGFjZS1tY3AiLCJjb21wYXJlLW1jcCJdfQ==)

For **Claude Desktop** without a terminal, open `ru-marketplace-mcp-compare-<version>.mcpb` from [GitHub Releases](https://github.com/Vladimir-Human/ru-marketplace-mcp/releases) (checked on Windows, not checked on macOS).

### Choose a profile

Every mounted MCP tool schema is paid on each model request, so `setup` and
`install` default to the compact `compare` profile:

| Profile | Size | Best for |
| --- | --- | --- |
| **`compare`** *(default)*<br>`compare-mcp` | 4 · ≈1.7k | Compare prices across sites and verify the winning offer |
| **`decision`**<br>`decision-mcp` | 5 · ≈2.0k | Compare plus shortlist card and review inspection |
| **`full`**<br>`marketplace-mcp` | 39 · ≈13.1k | Per-site search, reviews, seller tax IDs, catalogs, Cian |
| Single marketplace<br>`--per-source` | 2–8 · min. | Only one connector (`wb-mcp`, `ozon-mcp`, `cian-mcp`, …) |

> **Security and limits:** Do not run these servers in one agent session with tools that read private data and can send it out without your approval, and use a separate scraping Chrome profile without email or banking ([SECURITY.md](SECURITY.md#security)).

[First successful query](docs/QUICKSTART.md) · [Tool reference](docs/TOOLS.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Anti-bot notes](docs/ANTI_BOT.md)

### Connecting an MCP client (all 9 targets)

`marketplace-mcp setup` auto-detects **Claude Desktop**, **Cursor**, **Claude Code**,
**VS Code**, **Windsurf**, **Antigravity**, **Gemini CLI**, and **Codex CLI**,
writes (or prints) the `compare` profile by default, starts the scraping Chrome,
runs `doctor`, and offers `unblock` (`--dry-run`, `--no-browser`, `--no-doctor`).

To configure one target directly:

```console
uvx ru-marketplace-mcp install <client> --write --profile compare
```

| Client (`<client>`) | What `--write` updates |
| --- | --- |
| **Claude Desktop**<br>`claude` | `mcpServers.marketplace` in Claude Desktop config + `.bak` (or `.mcpb` in [Releases](https://github.com/Vladimir-Human/ru-marketplace-mcp/releases)) |
| **Cursor**<br>`cursor` | `mcpServers.marketplace` in `~/.cursor/mcp.json` + `.bak` |
| **VS Code**<br>`vscode` | **`servers.marketplace`** (`"type": "stdio"`) in `Code/User/mcp.json` + `.bak` |
| **Windsurf**<br>`windsurf` | `mcpServers.marketplace` in Windsurf config + `.bak` |
| **Antigravity**<br>`antigravity` | `mcpServers.marketplace` in Antigravity config + `.bak` |
| **Gemini CLI**<br>`gemini` | `mcpServers.marketplace` in `~/.gemini/settings.json` + `.bak` |
| **Claude Code**<br>`claude-code` | Prints `claude mcp add marketplace --scope user -- ...` (or `/plugin install` below) |
| **Codex CLI**<br>`codex` | Prints `codex mcp add marketplace -- ...` to run in your shell |
| **DeepSeek Harness**<br>`dsh` | Prints `cordis.patch.yml` rows (plugin bundle install below and in [dsh/README.md](dsh/README.md)) |

<sub>Config paths: **Claude Desktop** — `%APPDATA%\Claude\claude_desktop_config.json` (Win), `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `~/.config/Claude/claude_desktop_config.json` (Linux); **Windsurf** — `~/.codeium/windsurf/mcp_config.json`; **Antigravity** — `~/.gemini/config/mcp_config.json`. Existing files get a `.bak-&lt;timestamp&gt;` backup first.</sub>

`--write --dry-run` previews changes without writing; `--force` replaces an
existing conflicting `marketplace` entry; `--per-source` emits per-source
entries across all 15 console scripts (`wb-mcp`, `ozon-mcp`, `yandex-mcp`,
`detmir-mcp`, `avito-mcp`, `taobao-mcp`, `megamarket-mcp`, `lamoda-mcp`,
`dns-mcp`, `citilink-mcp`, `aliexpress-mcp`, `cian-mcp`, `compare-mcp`,
`decision-mcp`, `marketplace-mcp`).

If you prefer to paste JSON manually (replace with `"servers"` and `"type": "stdio"` in VS Code):

```json
{
  "mcpServers": {
    "marketplace": {
      "command": "uvx",
      "args": ["--from", "ru-marketplace-mcp", "compare-mcp"]
    }
  }
}
```

Browser and health verbs:
- `marketplace-mcp browser start` — launch the scraping Chrome on-screen (`--hidden` for off-screen);
- `marketplace-mcp browser status` — check CDP reachability, Chrome version, tabs, and window position;
- `marketplace-mcp browser show` — bring an off-screen Windows scraping Chrome window on-screen;
- `marketplace-mcp unblock <source>` — open the site's login/check page in a new tab, wait while you pass it yourself, and re-check only that source;
- `marketplace-mcp doctor` — exits `0` healthy, `1` parser drift (alarm), `2` blocked/inconclusive (`budget_refused` never counts as healthy), `3` status-file write failure, `4` checker/usage failure (alarm).

**DeepSeek Harness (dsh)** installs as a plugin bundle from [`dsh/`](dsh/README.md) (`pnpm` must be on PATH):

```console
dsh plugin --profile web add ru-marketplace-mcp-dsh
# or directly from GitHub:
dsh plugin --profile web add github:Vladimir-Human/ru-marketplace-mcp#path:/dsh
```

That gives you 14 skills immediately and **no** MCP tools: all three MCP rows stay
disabled until `RU_MARKETPLACE_MCP_DIR` points at a clone.
<!-- tools-cost:start -->
A mounted server is paid on every request — ≈1.7k tokens for the
recommended price-comparison profile, ≈2.0k for the decision profile and
≈13.1k for the full set (estimated from `tools/list`; details in
[docs/TOOLS.md](docs/TOOLS.md)) — so opting in is left to you.
<!-- tools-cost:end -->
[dsh/README.md](dsh/README.md) covers enabling it and the full mode.

---

## Supported marketplaces (12 sources)

| Source | Access | Scope |
| --- | --- | --- |
| **Wildberries** (8) | direct HTTP (Chrome on 403) | Search, cards, reviews, Q&A, seller tax IDs, catalogs |
| **Yandex Market** (2) | direct HTTP | Multi-seller prices, star breakdown, reviews |
| **Detsky Mir** (3) | direct HTTP | Kids' goods, offline store stock, catalogs |
| **Ozon** (3) | Chrome (or HTTP outside cloud) | Search, cards, reviews |
| **Avito** (3) | Chrome + non-cloud IP & pauses | Classifieds, cards, seller reputation |
| **Taobao** (2) | Chrome with login | Search and cards, prices in yuan |
| **Megamarket** (2) | Chrome with login | Search and cards via mobile API |
| **Lamoda** (2) | HTTP → Chrome (search Chrome) | Search, cards with sizes |
| **DNS** (2) | Chrome (Qrator) | Tech search and cards |
| **Citilink** (2) | Chrome (Qrator) | Tech search and cards |
| **AliExpress** (2) | Chrome (x5sec) | Search and cards, ruble prices |
| **Cian** (2) | Chrome (WAF by IP) | Real estate: sale, rent, daily, card |
| **Compare** (4) | queries sites above | Comparison, card check, sources, snapshot |

Anonymous, no browser: Wildberries, Yandex Market and Detsky Mir (when WB's API
returns 403, `wb_search` and `wb_card` re-read through your Chrome automatically).
Lamoda cards try HTTP GraphQL first and need your Chrome (CDP) only for the
fallback tier. The rest need your Chrome (CDP), and Taobao and Megamarket
additionally need you signed into a shopper account in that browser — without it
Taobao hits a login wall and Megamarket returns an empty result. Avito blocks
datacenter IPs and expects a non-cloud connection with requests spaced
apart. Connectors hold a deliberate gap between CDP calls so back-to-back bursts
do not drop sessions. Run `marketplace-mcp doctor` from your own environment for
the current state.

MPStats analytics moved to the official MPStats MCP server (`https://mcp.mpstats.io/mcp?token=<your MPStats token>`) and are not part of `ru-marketplace-mcp`.

37 tools across 13 stdio MCP servers sharing one runtime (`mcp-core`), plus
`decision-mcp` (5 tools) and the unified `marketplace-mcp` that mounts them all
under one client entry and adds `marketplace_sources` and `marketplace_health`
(39 tools total). stdio is the default; HTTP transport is opt-in for remote
deployment — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### A real answer

`compare_prices(query="iphone 16 128gb")`. The marketplaces' answers were recorded in Chrome on the owner's machine on 17 November 2026 at 01:29 UTC (audit cycle 1, record [c1-iphone16-128gb.json](packages/compare-connector/tests/benchmark/recordings/c1-iphone16-128gb.json)) and replayed through `2.11.0` (commit `f39a786f`). Trimmed to key fields and one warning per kind; the full answer also includes `source_outcomes` and all 25 entries in `offers`.

```json
{
  "query": "iphone 16 128gb",
  "sources_queried": [
    "aliexpress", "citilink", "dns",
    "taobao", "wildberries", "yandex_market"
  ],
  "sources_ok": [
    "aliexpress", "dns", "taobao", "wildberries", "yandex_market"
  ],
  "complete": false,
  "total_offers": 25,
  "cheapest": {
    "source": "aliexpress",
    "product_id": "1005009110731940",
    "title": "MOVESPEED флешка USB Type C 64/128/256/512 ГБ",
    "price_rub": 1531.0,
    "currency": "rub",
    "rating": 4.8,
    "url": "https://aliexpress.ru/item/1005009110731940.html"
  },
  "cheapest_comparable": {
    "source": "aliexpress",
    "product_id": "1005008110020734",
    "title": "Смартфон Apple iPhone 16, 8/128ГБ",
    "price_rub": 64990.0,
    "currency": "rub",
    "rating": 5.0,
    "url": "https://aliexpress.ru/item/1005008110020734.html"
  },
  "price_spread_rub": 93459.0,
  "warnings": [
    "partial: 1 of 6 marketplaces did not answer (citilink) ...",
    "conditional_price: wildberries/1415238452: 56833 RUB requires WB Wallet; not ranked",
    "info:conditional_price_available: yandex_market/103530694148: 72825 RUB with Yandex Pay",
    "price_outlier: the cheapest offer is 1531.0 ₽ against a median of 70299.0 ₽ across 17 offers ...",
    "comparable_price: the raw cheapest listing is an accessory, a different condition, variant or model; cheapest_comparable is the safer like-for-like candidate",
    "foreign_currency: 5 offer(s) are priced in cny, not roubles ... excluded from the ranking"
  ],
  "server_version": "2.11.0"
}
```

<details>
<summary>Full <code>warnings</code> list from this recording (15 entries)</summary>

```json
[
  "partial: 1 of 6 marketplaces did not answer (citilink) — prices below cover only aliexpress, dns, taobao, wildberries, yandex_market",
  "source_warning:aliexpress: 2 tile(s) advertise a coupon price; price_rub carries the regular price and the coupon value is not part of the response — treat 'cheapest' ranking with care",
  "source_warning:wildberries: dom_fallback: WB API refused this address (HTTP 403); read through the scraping Chrome, in the WB region set in that browser rather than dest",
  "source_warning:wildberries: wb_dom_search: price_rub is the WB Wallet price on search tiles (the regular card price is equal or a few % higher and shows only on the product page — verify leaders with wb_card)",
  "conditional_price: wildberries/1415238452: 56833 RUB requires WB Wallet; not ranked",
  "conditional_price: wildberries/580725150: 76922 RUB requires WB Wallet; not ranked",
  "conditional_price: wildberries/580725161: 76922 RUB requires WB Wallet; not ranked",
  "info:conditional_price_available: yandex_market/103572173063: 71800 RUB with Yandex Pay",
  "info:conditional_price_available: yandex_market/103530694148: 72825 RUB with Yandex Pay",
  "info:conditional_price_available: yandex_market/4775394349: 68107 RUB with Yandex Pay",
  "info:conditional_price_available: yandex_market/5302780524: 66699 RUB with Yandex Pay",
  "info:conditional_price_available: yandex_market/4775394347: 69323 RUB with Yandex Pay",
  "price_outlier: the cheapest offer is 1531.0 ₽ against a median of 70299.0 ₽ across 17 offers — check it is the same product and configuration",
  "comparable_price: the raw cheapest listing is an accessory, a different condition, variant or model; cheapest_comparable is the safer like-for-like candidate",
  "foreign_currency: 5 offer(s) are priced in cny, not roubles — their price is in price_native and they are excluded from the ranking and from cheapest"
]
```

</details>

The raw cheapest listing is a USB stick whose title contains "128", so the answer names `cheapest_comparable`, the phone itself, and warns about the price outlier. The Wildberries price that needs WB Wallet is shown but not ranked. Citilink refused this call, and the answer says it is incomplete.

## The tools

Generated per-tool reference with parameters and context cost:
[docs/TOOLS.md](docs/TOOLS.md).

The `*_selfcheck` canaries are deliberately absent from these tables: they are not
published over MCP, because operator diagnostics would cost the model ~7.5k tokens
on every request. `marketplace-mcp doctor` runs them all from the command line.

### Wildberries — `wb_*`

| Tool | What it does |
| --- | --- |
| `wb_search`<br><sub>`query, page`</sub> | Text search, up to 100 products/page with prices and stock |
| `wb_card`<br><sub>`nm_ids`</sub> | Batch lookup for up to 100 known SKUs |
| `wb_root_info`<br><sub>`nm_id`</sub> | Resolves `imt_id` (needed for reviews) plus colour variants |
| `wb_reviews`<br><sub>`imt_id, limit, sort`</sub> | Review pool, keyed by `imt_id`, not `nm_id` |
| `wb_questions`<br><sub>`imt_id, limit, skip, answered_only`</sub> | Buyer questions with seller answers, also keyed by `imt_id` |
| `wb_seller`<br><sub>`supplier_id`</sub> | Registered entity, INN, KPP, OGRN, legal address |
| `wb_categories`<br><sub>`root, max_depth`</sub> | Catalog tree with WB's own shard/query selectors |
| `wb_category_products`<br><sub>`shard, query, page, sort, dest`</sub> | Products in a category, using those selectors |

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

| Tool | What it does |
| --- | --- |
| `yandex_search`<br><sub>`query, page, limit`</sub> | Search with both prices, ratings, sellers |
| `yandex_card`<br><sub>`product_id, include_reviews`</sub> | Full detail plus star breakdown and reviews |

**Two prices, always.** `price_rub` is what anyone pays. `price_with_plus` is the
conditional price for paying with Yandex Pay (the page's «Пэй» price; earlier Yandex Plus).
Yandex leads with it, so quoting it uncritically misstates the real cost.

**A search row is the SERP's offer, not the card's default offer.** One `product_id`
covers a product family, and search may show one member while the card for the same id
defaults to another — reconcile rows with cards by `sku_id`, not by URL. `price_old_rub`
on search rows is the struck-through reference price, never a price to quote.

`rating_stars` gives the distribution, for example `{1: 10, 2: 3, 3: 10, 4: 19, 5: 502}`.
That reveals whether a 4.8 average is earned or hides a cluster of complaints.

### Detsky Mir — `detmir_*`

| Tool | What it does |
| --- | --- |
| `detmir_categories`<br><sub>`parent, limit, region`</sub> | Catalog tree, start here |
| `detmir_category`<br><sub>`alias, limit, offset, region`</sub> | Products in a category, with real totals |
| `detmir_card`<br><sub>`product_id, region`</sub> | Price, rating, online and offline store stock |

**Region is per call.** Prices and especially offline availability swing by city —
one item sat in 152 Moscow stores, 37 in St Petersburg, 2 in Khabarovsk. The
`region` parameter overrides `DETMIR_REGION`, so one session can compare cities.

**There is no text search, deliberately.** Detsky Mir's API silently ignores every
text filter and returns its entire 300k-item catalog; the website's search route
answers 404 and renders a promo carousel. A search tool would return confidently
wrong products, so discovery goes through categories instead. See
[docs/ANTI_BOT.md](docs/ANTI_BOT.md).

### Ozon — `ozon_*`

| Tool | What it does |
| --- | --- |
| `ozon_search`<br><sub>`query`</sub> | Text search |
| `ozon_card`<br><sub>`sku_or_path`</sub> | Product detail |
| `ozon_reviews`<br><sub>`sku_or_path, limit, sort`</sub> | Reviews |

Ozon rejects datacenter traffic, so this connector is two-tier: TLS impersonation
first, then a fetch inside your own logged-in Chrome over the DevTools Protocol when
Cloudflare challenges. Nothing is stored; you log in yourself, in a browser you
control. Setup: [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

Outside cloud and datacenter networks, the first tier usually works and no browser is needed.

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

| Tool | What it does |
| --- | --- |
| `aliexpress_search`<br><sub>`query`</sub> | Search: up to 48 tiles with ruble prices |
| `aliexpress_card`<br><sub>`item_id_or_url`</sub> | Card: title, price, rating, order count |

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

| Tool | What it does |
| --- | --- |
| `cian_search`<br><sub>`deal, offer_type, region, rooms, price_min, price_max, area_min, area_max, page`</sub> | Search by filters — sale, long-term rent, daily: 28 offers per page |
| `cian_card`<br><sub>`offer_id_or_url`</sub> | One offer: price and its history, layout, building, address, metro, publisher |

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

| Tool | What it does |
| --- | --- |
| `avito_search`<br><sub>`query, page, location_id, category_id, include_inactive`</sub> | Classified search through the internal `js/items` API; `availability` from `isReserved`; reserved hits kept, `include_inactive=false` drops them (`excluded_inactive`) |
| `avito_card`<br><sub>`item_id_or_url, photos`</sub> | CDP item page: title, price, description, image count, seller (rating, profile link), up to 3 photos of an active ad on request (`photos`, costly), reservation `availability` (active/reserved/unknown) from service flags, deal/delivery terms, city and stations |
| `avito_seller`<br><sub>`seller_id_or_url`</sub> | Seller rating, review count; active listing count unavailable (`null`) |

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

| Tool | What it does |
| --- | --- |
| `taobao_search`<br><sub>`query, page`</sub> | Catalog search |
| `taobao_card`<br><sub>`item_id_or_url`</sub> | Product card |

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
sources are browser-backed in total (these four plus Taobao, AliExpress, Cian,
Ozon and Avito; for Ozon and Avito, Chrome is only the fallback tier when tier 1
is challenged), and Wildberries also falls back to Chrome over CDP by default
when its API returns 403 (`WB_DOM_FALLBACK=1`).

### Cross-marketplace — `compare_*`

| Tool | What it does |
| --- | --- |
| `compare_prices`<br><sub>`query, per_source_limit, sources, in_stock_only`</sub> | Rank all sources at once |
| `compare_verify_offer`<br><sub>`source, product_id_or_url,`<br>`expected_price_rub, expected_identity,`<br>`expected_variant_id`</sub> | Verify winning offer on card |
| `compare_sources()` | Sources active in install |
| `compare_browser_snapshot`<br><sub>`handoff_id`</sub> | Retained tab screenshot |

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

## Agent skills

Every connector ships its own skill under `skills/` — fourteen of them — one per source plus a shared
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
| `skills/marketplace`          | `marketplace-mcp` |

`mcp-core` is the shared runtime rather than a server, so it has no skill.

The mapping is enforced by a test
([test_skills_parity.py](packages/marketplace-connector/tests/test_skills_parity.py)): a new connector
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
| `WB_`                | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES`, `CACHE_TTL`, `PROXY`, `TRANSPORT`, `DOM_FALLBACK`   |
| `YANDEX_`            | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY`                                                                                 |
| `DETMIR_`            | `REGION` (`RU-MOW`, `RU-SPE`, and others), `CACHE_TTL`, `PROXY`                                                            |
| `OZON_`              | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY`, `CHROME_COMPAT`                                                 |
| `AVITO_`             | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY`, `LOCATION_ID`, `MAX_PHOTOS` (0–10, default 3)                   |
| `TAOBAO_`            | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`                                                                                          |
| `ALI_`               | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`                                                                                          |
| `MEGAMARKET_`        | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `USE_PROFILE_ADDRESS` (0/1, default 0 — the profile's private address list is not read; 1 = the logged-in profile's default address, prices "as the operator sees them") |
| `LAMODA_`            | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY`                                                                                 |
| `DNS_` / `CITILINK_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`                                                                                          |
| `CIAN_`              | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `MAX_BODY_BYTES`, `REGION`                                                              |
| `CHROME_`            | `CDP_HOST`, `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH`, `NAV_TIMEOUT_MS`, `CHALLENGE_HANDOFF_S`       |
| `COMPARE_`           | `SOURCE_TIMEOUT`                                                                                                           |
| `MCP_`               | `TRANSPORT` (`stdio` default, or `http`), `HTTP_HOST`, `HTTP_PORT`                                                         |

`*_CACHE_TTL=0` disables caching. `*_PROXY` overrides the standard
`HTTPS_PROXY`/`ALL_PROXY` — six connectors carry one: `WB_`, `YANDEX_`, `DETMIR_`,
`OZON_`, `AVITO_` and `LAMODA_`. Taobao has none by design and
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

**Only the sources you use: `MARKETPLACE_SOURCES`.** The unified server mounts every
source, and their tool schemas are sent to the client on every request. Set
`MARKETPLACE_SOURCES` in the `marketplace-mcp` entry's env to a comma-separated list
to mount only those, e.g. `wildberries,ozon,compare` (plus `yandex_market`, `avito`, `aliexpress`, `dns`).
Names are canonical (`wildberries`, `ozon`, `yandex_market`, `detsky_mir`, `avito`,
`taobao`, `megamarket`, `lamoda`, `dns`, `citilink`, `aliexpress`, `cian`, `compare`); the aliases `wb`, `ym`/`yandex`, `detmir` and `ali` work too. An unknown
name is rejected at startup with the supported-source list, so a typo cannot
silently produce a partial server. Unset or blank mounts everything, as before.
Deselected sources show up in
`marketplace_sources` under `skipped`, marked as deselected rather than failed to
import. By default, `compare_prices` queries only selected goods sources with
text search: Detsky Mir has no text search, and Cian does not take part
in goods comparison. Explicit `sources` can narrow this subset, not re-enable a
disabled source: that request returns `bad_request`, as does inspecting its card.

## Development

```bash
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"    # offline tests (what CI runs)
uv run pytest -q -m "not live and not cdp" --cov --cov-fail-under=80  # coverage, CI enforces an 80% floor
uv run ruff check . && uv run ruff format --check .
uv run mypy                                   # the tree lives in [tool.mypy] files
uv run mypy --platform win32                  # catches Windows-only type errors
uv run python scripts/check_no_print.py       # a print() breaks JSON-RPC
uv run python scripts/check_versions.py       # one version across all tracked files
```

Publishing to PyPI (maintainer): [DEPLOYMENT.md — PyPI](docs/DEPLOYMENT.md#pypi-maintainer). Adding a marketplace: [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md).

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
3.12 and 3.13; 3.14 (all three OSes) and 3.15 (Ubuntu, Windows) run too, without blocking a merge. Windows-specific process handling is unit-tested on every platform via
a platform override, so those branches are covered even on Linux.

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
profile address list is read only with `MEGAMARKET_USE_PROFILE_ADDRESS=1`. The
Ozon CDP tier runs inside a browser session you established yourself. Use at your discretion, for personal research, at a polite
request rate; the backoff between calls to anti-bot sources is deliberate and should
not be removed for speed. Tool output is not meant for redistribution or bulk
harvesting.

## How this was built

I wrote the code and the documentation with AI assistants. They are fast and they
are confidently wrong, so the project is arranged around verification: offline
tests, an audit before the release, tests that run the real extractor against
markup captured from the live site. The release notes say which sources were
compared against live pages by hand and which were left unverified.

Checks matter more than who typed the prose, but code and ideas deserve visible
credit too: the full contributor and PR index is in
[CONTRIBUTORS.md](CONTRIBUTORS.md).

## Thanks

- [@avxone](https://github.com/avxone) — Avito selfcheck fix, [PR #37](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/37).
- [@Khalmatov](https://github.com/Khalmatov) — Ozon review provenance, [PR #38](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/38).
- [@fosteev](https://github.com/fosteev) — macOS CDP stealth and the Cian connector, [PR #42](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/42), [PR #47](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/47).
- [@ilodezis](https://github.com/ilodezis) — unified-server source selection, [PR #48](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/48).
- [@HeckfyCU](https://github.com/HeckfyCU) — AliExpress navigation timing measurements in [issue #190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190): those measurements located the page-readiness deadline that has been fixed.
- [@XaruStar](https://github.com/XaruStar) — live Ozon `webListReviews` report on decimal-string `itemId`, [issue #214](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/214): fixed review SKU attribution (`own_reviews`) and `ozon_selfcheck` `item_id` drift detection.

## License

MIT, see [LICENSE](LICENSE).
