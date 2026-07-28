# Anti-bot reality, source by source

Every marketplace here was probed live from a datacenter IP in July 2026. This
document records what actually happened — including the sources that only became
buildable once the CDP tier existed — because that determines what is buildable
and what is a trap.

The single most useful finding: **anti-bot posture, not API quality, decides
whether a marketplace is usable.** Wildberries has a messy API and works
perfectly. Megamarket has a clean one and was unreachable until the fetch moved
inside a real browser.

## Summary

| Marketplace | Datacenter IP | Anti-bot | In release |
|---|---|---|---|
| Wildberries | ✅ works | none observed | ✅ |
| Yandex Market | ✅ works | SmartCaptcha (dormant) | ✅ |
| Detsky Mir | ✅ works | none | ✅ |
| Ozon | ❌ 307 loop | Cloudflare | ✅ via your Chrome |
| Avito | ❌ 403 firewall | IP reputation + captcha | ✅ via your Chrome |
| Taobao | ⚠️ shell only | signed mtop API | ✅ via your Chrome + login |
| Megamarket | ❌ blocked | ServicePipe | ✅ via your Chrome + login |
| Lamoda | ⚠️ partial | anti-bot redirect loop | ✅ via your Chrome |
| DNS | ❌ 401 | Qrator JS proof-of-work | ✅ via your Chrome |
| Citilink | ❌ 429 | Qrator rate block | ✅ via your Chrome |

A second live run in July 2026 confirmed all four CDP sources on the maintainer's
own machine — a Russian residential IP, a logged-in Chrome over CDP — and it
changed some verdicts. Wildberries, Ozon, Yandex Market, Detsky Mir and Citilink
were stable and healthy. Citilink and DNS carried a real bug, now fixed: the
product-id regex demanded 24 hex characters (a MongoDB ObjectId shape) while the
live routes are `/product/noutbuk-lenovo-2169270/` on Citilink (a slug ending in
digits) and `/product/b7a1667f9b19ed20/` on DNS (16 hex), so search parsed zero
tiles and reported drift. Three sources stayed hard: Megamarket returns an empty
result to an anonymous session, Lamoda renders no product links, and Avito hands
back an IP block. The one lesson under all of it — there is no public endpoint
that turns these sources healthy. Every open-source implementation lands on the
same three requirements: a real browser or a residential/mobile IP, a logged-in
session, and requests paced apart. The per-source sections below say which each
one needs.

A burst of back-to-back requests is its own failure mode, separate from the
initial challenge. DNS went healthy after the regex fix, then degraded to
`transport_down` once it was hit with a run of requests with no gap between them;
Taobao did the same, dropping from healthy to a parse failure after a burst. The
connectors pace their own requests for exactly this reason. Do not remove the gap.

## Независимая проверка с резидентного IP, июль 2026

Прогон из отдельного облачного Chrome с резидентным прокси — не с машины
мейнтейнера. Он важен тем, что отделяет «площадка блокирует датацентр» от
«площадка блокирует всех».

**Wildberries** отвечает анонимно, как и раньше. Заодно выяснилось, почему по
запросу «iphone 15» самое дешёвое предложение оказывалось на треть ниже
остальных: дешёвые позиции — «Восстановленный» и «Витринный образец», то есть
телефон после ремонта и витринный экземпляр против новых у конкурентов. Ни по
названию, ни по величине скидки это не отличить автоматически без словаря
состояний, поэтому он и появился в `compare_prices`.

**Ситилинк и DNS-Shop** отдали настоящую выдачу без видимого челленджа Qrator.
Это не значит, что анонимный HTTP-клиент справится: запрос шёл из настоящего
Chrome, то есть ровно из того окружения, ради которого и существует CDP-уровень.
Вывод скромнее и полезнее — proof-of-work смотрит на репутацию адреса, и с
резидентного IP реальный браузер проходит его незаметно.

**Ситилинк в этой выдаче рисует плитки внутри iframe.** В верхнем документе
товарных ссылок нет вообще, все 27 лежат во вложенном фрейме того же
происхождения. Экстрактор, который читает только `document`, на такой раскладке
видит ноль плиток и сообщает дрейф, хотя парсер исправен. Отсюда и плавающий
результат: раскладка приходит не всем и не всегда. Экстракторы Ситилинка и DNS
теперь собирают ссылки и из фреймов, которые им разрешено читать.

**Lamoda** отвечает «Запрос отклонен» и на поиск, и на главную. Это блок по
адресу, а не смена вёрстки: страница вообще не отдаётся. Совпадает с выводом из
разбора публичных реализаций — искать несуществующий search-endpoint смысла нет.

Идентификатор товара Ситилинка подтверждён на живой карточке: числовой
(`2140628`), не 24-символьный hex.

Отдельно про Lamoda: её GraphQL-эндпоинт ответил **с датацентрового адреса**,
хотя HTML-страницы того же домена отдают «Запрос отклонен». То есть анонимный
tier карточек живёт по своим правилам, и архитектура «карточка через GraphQL,
поиск через CDP» подтверждается напрямую. Снятые конверты (июль 2026):

| Запрос | Ответ |
|---|---|
| корректные поля | `{"error": null, "result": [{…}]}` |
| неизвестный SKU | `{"error": null, "result": null}` |
| поле `old_price` | `{"error": "Internal server error", "code": -32603}` |

Стандартного GraphQL-конверта здесь нет: товары лежат в `result`, а не в
`data.products`, и ошибка приходит одной строкой `error`, а не массивом `errors`.
Коннектор читал стандартную форму, поэтому успешный ответ выглядел дрейфом.

Полная картина по всем десяти источникам с этого адреса, без единого логина:

| Источник | Что ответил | Что это значит |
|---|---|---|
| Wildberries | выдача с товарами | анонимный доступ работает |
| Яндекс Маркет | выдача с товарами | анонимный доступ работает |
| Детский мир | каталог отрисован | анонимный доступ работает |
| Ситилинк | выдача, плитки в iframe | челлендж не показан, но раскладка другая |
| DNS-Shop | 24 товарные ссылки | челлендж не показан, плитки в основном документе |
| Ozon | «Похоже, нет соединения» | соединение не состоялось: адрес отклонён на транспорте |
| Авито | «Доступ ограничен: проблема с IP» | ровно тот файрвол, что описан выше |
| Мегамаркет | слайдер-капча «разверните картинку» | ServicePipe показывает челлендж человеку |
| Lamoda | «Запрос отклонен» | отказ на всех путях, включая главную |
| Taobao | стена логина | без сессии выдачи нет, как и задокументировано |

Lamoda проверена с двух независимых резидентных адресов, на поиске, категории и
главной — везде одинаковый отказ. Это блокировка целого класса трафика, а не
сгоревший конкретный адрес, и перебор адресов её не решает.

Три источника разошлись с прогоном на машине мейнтейнера, и расхождение
полезно. Ситилинк и DNS отдали данные без логина и без челленджа — значит их
нестабильность у оператора была не про доступ, а про темп запросов подряд.
Ozon, наоборот, здоров с домашнего российского адреса и недоступен отсюда:
адрес решает больше, чем браузер.

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

Two more endpoints were verified live for v1.1.0, each with a silent-failure mode
worth knowing:

- **Buyer questions** live on `questions.wildberries.ru/api/v1/questions`, keyed by
  `imtId` like reviews. `take` and `skip` are **mandatory**: omit either and every
  product returns `{"questions": null, "count": 0}`, which reads as "nobody asked
  anything" but is really a rejected request. `take` is capped at 30 — `take=31` is
  a 400. There are no mirrors; `questions1`/`questions2` and `questions*.wb.ru` all
  answer 502. Worse, `feedbacks{1,2}.wb.ru` answer *any* questions-shaped path with
  an identical 277-byte empty stub, so pointing questions at the reviews hosts
  looks like a valid empty result.
- **Category feeds** live at `catalog.wb.ru/catalog/{shard}/v4/catalog`, with the
  shard in the path. Only v4 works; v2, v8, v9 and the shard-less form are 404.
  The catch is the shard `blackhole`, which several of the largest sections carry
  (smartphones `cat=9455`, laptops `cat=9491`, TV/audio `cat=9834`): those have no
  feed at all. The first probe answered 429, which looked like throttling, but four
  more spaced 8s apart all returned a clean empty 404. `wb_category_products`
  refuses those up front and names the alternative, because an empty product list
  would assert the category is empty.

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
- **Region only applies via `filter=withregion:`.** The query-parameter spelling
  `?withregion=RU-SPE` is accepted and silently ignored, which is how `detmir_card`
  shipped in v1.0.0 reporting `store_count: 0` for every product while labelling
  the response with a region. The filter form returns real per-city numbers — one
  product sat in 152 Moscow stores, 37 in St Petersburg, 2 in Khabarovsk. Fixed in
  v1.1.0, where every tool also takes a per-call `region`.

And one thing that is not a quirk but a genuine absence — see
[the Detsky Mir search trap](#the-detsky-mir-search-trap).

### Ozon — needs your browser

`composer-api.bx` answers a datacenter IP with an endless self-referential 307
redirect loop (`?...&__rr=1`, `__rr=2`, …). curl follows it until it hits its
redirect ceiling. TLS impersonation alone does not clear it.

The working answer is not a better fingerprint but a different vantage point: run
the fetch **inside a browser the operator already logged into**, over the DevTools
Protocol. That is tier 2. Setup and threat model: [CDP_SETUP.md](CDP_SETUP.md).

Ozon's seller pages were spiked for v1.1.0 and deliberately left out — see
[the seller-details refusal](#the-ozon-seller-details-refusal-v110).

### Avito — hard IP firewall, workable API behind it

Probed July 2026 from a datacenter IP with TLS impersonation (the same
`curl_cffi` setup that clears Ozon's tier 1):

- **Search page** (`/moskva/noutbuki?q=…`): HTTP 403, «Доступ ограничен:
  проблема с IP». The block page sets `srv_id` and `_avisc` cookies — Avito
  fingerprints the session before it will talk at all.
- **`/web/1/js/items`** (the internal JSON search endpoint, the one
  third-party parsers drive): 403 with
  `{"too-many-requests":{"link":"ru.avito://1/firewall/captcha/show"}}` — a
  firewall captcha challenge, not a rate limit that clears by waiting.
- **Mobile API** (`app.avito.ru/api/1/search`): nginx 404 — the route third
  parties use lives elsewhere, and it is gated the same way.

The API itself is well documented by open-source parsers (Duff89/parser_avito):
`GET /web/1/js/items` with `categoryId`, `locationId`, `p`, `q`, `context`,
`updateListOnly=true` plus price/seller/delivery filters returns the same JSON
the site renders. Those parsers survive on mobile proxies and bought cookies —
the firewall is the whole problem.

**Verdict:** tier 2. From a residential Russian IP with a warmed-up session the
`js/items` endpoint answers; from your logged-in Chrome it always does. The
connector tries tier 1 (impersonated HTTPS) first and falls back to CDP, same
as Ozon — but expect tier 1 to be dead from any datacenter.

The July 2026 run confirmed the endpoint is right and the block is the whole
problem. Even from the residential IP, search came back with an HTTP 439/429-class
refusal — an IP/rate block, not a challenge that a browser clears. The seller
endpoint answered while search did not, which is the rate limiter biting the
heavier query first. Three public parsers — `Duff89/parser_avito`,
`ihydrad/avito-parser`, `ergon73/avito-parser` — all drive the same `js/items`
endpoint this connector uses, and their code and issue trackers are dominated by
cookie handling and HTTP 429; Duff89 has an open issue reporting 429 even behind
a server proxy. Nobody has a cleaner path. IP reputation and request rate are the
blocker, so this one wants a residential or mobile IP and patience on top of the
browser.

### Taobao — anonymous pages, signed API

Also probed July 2026 from a datacenter IP:

- **Search page** (`s.taobao.com/search?q=…`): HTTP 200, but only a ~33 KB
  client-side-rendered shell. No `g_page_config` item list, no embedded state —
  results load over XHR after the page boots.
- **The XHR layer is mtop** (`h5api.m.taobao.com`): every call wants a `sign`
  parameter computed from the `_m_h5_tk` cookie token plus the request body.
  Without the token: `FAIL_SYS_TOKEN_EMPTY::令牌为空`. The signing scheme is
  reversed in open source, but token rotation makes it a treadmill.
- **No captcha on anonymous browsing.** Main page, 1688 search and AliExpress
  all answer 200 from the same IP. The block is API-level, not IP-level.

**Verdict:** tier 2 by a different mechanism than Avito — not IP reputation but
a signed API. A real Chrome computes signatures natively via the site's own JS,
so the connector drives search through CDP page evaluation and reads the
rendered DOM / page state. No impersonation shortcut is worth maintaining here.

Live check, July 2026: this works only while the operator's Taobao session is
logged in. A QR login in the scraping profile took it to healthy; a burst of
back-to-back requests then took it back down to a parse failure. So Taobao needs
two things the other CDP sources do not both need at once — a logged-in session
*and* paced requests. `taobao_search` says as much when it lands on a login wall,
and the connector paces its own calls.

## Sources that needed the CDP tier

The four below were rejected in v1.0/v1.1 because anonymous probing could not
confirm them end to end. The CDP tier changed that — in a real Chrome the
proof-of-work and IP-reputation challenges pass natively. All four shipped in
v1.2.0, and the July 2026 run on the maintainer's residential IP and logged-in
Chrome finally confirmed them end to end. DNS and Citilink went healthy once the
product-id regex was fixed. Megamarket and Lamoda did not: the challenge passes
but the result comes back empty or link-less, which the sections below explain.
Run the `*_selfcheck` tools from your own session before quoting either of those.

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
a browser that has passed the challenge. Shipped in v1.2.0 via the CDP tier: the
`megamarket-connector` POSTs the mobile API from inside the operator's Chrome and
maps the code-7 refusal to `transport_down`.

The July 2026 run got past the code-7 block — from the browser the ServicePipe
challenge passes and the API answers a valid envelope — but `items` came back as
an empty array for every query. That points at the session, not the fetch. The
actively maintained `xob0t/mmparser` (Python, same mobile API) documents that
since early 2025 parsing without cookies does not work: the challenge passes, the
session is not authenticated, and the API answers an empty result instead of an
error. So a passed challenge is not enough — Megamarket needs a session that is
actually logged in, and an anonymous but challenge-cleared browser reads empty.
`mmparser` also paces its calls at 1.8 s normally and 5 s after an error and
carries a dedicated "you are logged out" alert, which matches what an empty
`items` array means here. Run `megamarket_selfcheck` from a logged-in session.

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

**Verdict:** shipped in v1.2.0 as two tiers. Cards come from the anonymous GraphQL
endpoint (works tier 1, no ratings — `rating` is not in the schema); search runs
through the CDP tier, which is what supplies the discovery the GraphQL lacks.

The split is right, and the July 2026 run is why it matters. The public GraphQL
endpoint `POST /goapi/v2/catalog/graphql/products/` takes a list of SKUs, not a
search query — it enriches known products, and there is no public search API at
all. Every open-source Lamoda scraper reads catalog pages for discovery, which is
exactly this connector's architecture. So when the live run found the search page
rendering zero `/p/` product links with no embedded product JSON state, and the
anonymous GraphQL card endpoint no longer answering either, that is a block or a
layout change on top of the right design — not a missing endpoint to go hunting
for. Cards and search were both down that day; run `lamoda_selfcheck` from a
residential IP to tell a layout drift from a block before you conclude anything.

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

**Verdict:** shipped in v1.2.0 via the CDP tier. Qrator's proof-of-work executes
natively in a real Chrome, so the `dns-connector` renders the page and reads the
DOM rather than touching the CSRF-gated ajax routes.

Getting there took two separate fixes, and the first one only looked like a win.
The product-id regex demanded 24 hex while the real id is 16
(`/product/b7a1667f9b19ed20/`), so search had been parsing zero tiles; fixing it
made the tool report 24 links and `selfcheck` go green. The data was still empty —
every item came back with `title=None` and `price=None`, because the extractor
resolved a tile with `closest()`, which tests the element itself first and so
landed on the image link instead of the tile. That is the whole lesson of this
file in one bug: a green selfcheck proves the transport answered, not that the
parser understood the answer. The 2026-07-28 audit fixed the tile resolution and
pinned it with a test that runs the extractor against captured tile markup.

Note what the same audit found next door: DNS tiles advertise an instalment
("от 5 751 ₽/ мес.") beside the real price, and the old price heuristic took the
smallest number on the tile. Had the tile resolution been correct from the start,
DNS would have reported the monthly payment as the price — green, plausible, and
wrong. Prefer the null.

Then a burst of back-to-back requests degrades DNS to `transport_down`, which is
Qrator rate-limiting the rendered browser, not a parser fault. The DOM route is
the only route: no public JSON API exists, and every open-source DNS
implementation drives a real browser through Selenium, undetected-chromedriver or
Puppeteer — the same thing this connector does. Pace the calls and run
`dns_selfcheck` from your Chrome; from a datacenter address the site answers 401
and there is nothing to verify.

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

**Verdict:** shipped in v1.2.0 via the CDP tier, with gRPC-web deliberately not
reversed. A real Chrome passes Qrator and renders the pages, so the
`citilink-connector` reads the DOM instead of the binary protocol — the
maintainable route.

The July 2026 run had Citilink stable and healthy once the product-id regex was
fixed. The old pattern demanded 24 hex; the real route is a slug ending in digits
(`/product/noutbuk-lenovo-2169270/`), so search had been parsing zero tiles and
reporting drift. With that corrected it held steady across the run. This is the
right route for the same reason as DNS: every working third-party Citilink parser
drives a real browser, and the `github.com/citilinkru` repos are internal
infrastructure tooling, not a catalog client.

## Traps and refusals worth their own section

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

### The Ozon seller-details refusal (v1.1.0)

Ozon product pages link to a seller page carrying the legal entity behind a
listing — the OGRN/INN equivalent of what `wb_seller` returns for Wildberries.
That parity would be genuinely useful, so it was spiked for v1.1.0. It is not in
the release.

The path is right. `composer-api.bx/page/json/v2?url=/seller/{slug}-{id}/` is the
route Ozon's own web client uses, and the seller id is already available: it comes
back on `ozon_card` as `seller.link`, so nothing has to be guessed.

What could not be done is see a successful response. From a datacenter IP every
attempt ends the same way:

| Composer `url=` | Result |
|---|---|
| `/product/{id}/` (known-good control) | 307 → 403 `fab_…` |
| `/seller/ozon-1749/` | 307 → 403 `fab_…` |
| `/seller/1749/` | 307 → 403 `fab_…` |
| `/` (homepage warmup) | 403 |

The first request returns 307 from nginx and sets a `__Secure-ETC` cookie with a
`&__rr=1` retry hint; following it with the cookie, under chrome124 TLS
impersonation, yields 403 with an anti-bot incident body. The control matters more
than the seller rows: the repo's **already-working** product path fails identically,
which proves this is the IP being gated, not a malformed seller URL.

So the endpoint almost certainly works. The **field paths do not exist yet** —
nobody has seen the widget that carries the seller's legal name and tax ids, and
Ozon's payload is a dictionary of JSON-encoded widget states whose keys carry
version suffixes. Writing a parser against an unseen shape means inventing field
names and shipping whatever they happen to match.

That is the Detsky Mir lesson in a different costume. A seller tool returning a
plausible-looking company name for the wrong legal entity is worse than no tool,
because the entire point of a seller lookup is telling an official brand store from
a lookalike reseller. Confidence without verification is the failure mode this
project refuses.

**What exists instead:** the verified URL template and the live-verification steps
are in `RELEASE_PROMPT.md`, for an operator on a Russian residential IP or with the
CDP tier available. If the payload confirms, the tool is a small addition on top of
the existing two-tier fetch. Until then there is no `ozon_seller`.

For the same reason, `wb_questions` **did** ship in v1.1.0: its endpoint was
verified across six products from this same datacenter IP before a line of it was
written. The difference is evidence, not ambition.

## Practical guidance

**Rate limits are real.** WB search rate-limits within a few requests; Yandex is
fine at a steady pace but bursts invite SmartCaptcha. Every connector enforces a
minimum gap by default. Do not disable it.

**A block is not an absence.** `transport_down` or `rate_limited` means "we were
refused", not "the product does not exist there". `compare_prices` keeps these
distinct via `source_outcomes` and `complete`.

**Residential IP changes the picture.** From a Russian residential address, Ozon
tier 1 often works, Lamoda's HTML likely opens, and Citilink's rate block may
clear — and Ozon's seller pages become verifiable, which is the one thing blocking
an `ozon_seller` tool. Set `*_PROXY` to route through one. As of v1.1.0 every
connector honours its own proxy variable (`WB_PROXY`, `OZON_PROXY`,
`YANDEX_PROXY`, `DETMIR_PROXY`), not just two of them.

**Run the selfchecks.** `success` / `drift_detected` / `inconclusive` — and note
that `inconclusive` from a geo block says nothing about whether the parsers still
work.
