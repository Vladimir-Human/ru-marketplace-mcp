# ru-marketplace-mcp

**MCP servers for Russian marketplaces.** Read prices, stock, ratings, reviews and
seller identity from Wildberries, Ozon, Yandex Market and Detsky Mir — and compare
prices across all of them in one call.

Read-only. No credentials, no API keys, no account required.

[Русская версия ниже](#русская-версия) · [Architecture](docs/ARCHITECTURE.md) ·
[Adding a source](docs/ADDING_A_SOURCE.md) · [Anti-bot notes](docs/ANTI_BOT.md)

---

## What you get

| Server | Tools | Access | Notes |
|---|---|---|---|
| **Wildberries** | 7 | anonymous HTTP | Search, cards, reviews, **seller legal identity**, catalog tree |
| **Yandex Market** | 3 | anonymous HTTP | Multi-seller prices, **star distribution**, reviews |
| **Detsky Mir** | 4 | anonymous HTTP | Kids' goods, offline store stock, category listings |
| **Ozon** | 4 | TLS impersonation → your Chrome | Search, cards, reviews |
| **Compare** | 2 | aggregates the above | **"Where is this cheapest?"** in one call |

20 tools across 5 stdio MCP servers, sharing one runtime (`mcp-core`).

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q            # 221 offline tests, no network needed
```

Verify a live endpoint:

```bash
uv run python -c "
import asyncio
from wb_connector.server import wb_selfcheck
print(asyncio.run(wb_selfcheck()).status)   # -> success
"
```

## Connect it to your MCP client

Each server is a console script, so no paths are hardcoded into your config.

<details open>
<summary><b>Claude Desktop</b> — <code>claude_desktop_config.json</code></summary>

macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```jsonc
{
  "mcpServers": {
    "wildberries": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/ru-marketplace-mcp", "wb-mcp"]
    },
    "yandex-market": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/ru-marketplace-mcp", "yandex-mcp"]
    },
    "detsky-mir": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/ru-marketplace-mcp", "detmir-mcp"]
    },
    "compare-prices": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/ru-marketplace-mcp", "compare-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add wildberries -- uv run --directory /path/to/ru-marketplace-mcp wb-mcp
claude mcp add yandex-market -- uv run --directory /path/to/ru-marketplace-mcp yandex-mcp
claude mcp add detsky-mir -- uv run --directory /path/to/ru-marketplace-mcp detmir-mcp
claude mcp add compare-prices -- uv run --directory /path/to/ru-marketplace-mcp compare-mcp
```
</details>

<details>
<summary><b>Cursor</b> — <code>.cursor/mcp.json</code></summary>

```jsonc
{
  "mcpServers": {
    "compare-prices": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/ru-marketplace-mcp", "compare-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>Any other stdio MCP client</b></summary>

Run `uv run --directory /path/to/repo <script>` where `<script>` is one of
`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`. Servers speak
JSON-RPC on stdin/stdout and write diagnostics to stderr.
</details>

After connecting, ask your agent to run `wb_selfcheck` — it probes every endpoint
family and reports `success`, `drift_detected`, or `inconclusive`.

## The tools

### Wildberries — `wb_*`

| Tool | What it does |
|---|---|
| `wb_search(query, page)` | Text search, up to 100 products/page with prices and stock |
| `wb_card(nm_ids)` | Batch lookup for up to 100 known SKUs |
| `wb_root_info(nm_id)` | Resolves `imt_id` (needed for reviews) plus colour variants |
| `wb_reviews(imt_id, limit, sort)` | Review pool — keyed by `imt_id`, **not** `nm_id` |
| `wb_seller(supplier_id)` | Registered entity, INN, KPP, OGRN, legal address |
| `wb_categories(root, max_depth)` | Catalog tree with WB's own shard/query selectors |
| `wb_selfcheck()` | Drift canary |

`wb_seller` answers the question a listing hides: *who actually ships this?* It
returns the registered legal entity and tax ids, which is how you distinguish an
official brand store from a reseller trading under a similar name.

### Yandex Market — `yandex_*`

| Tool | What it does |
|---|---|
| `yandex_search(query, page, limit)` | Search with both prices, ratings, sellers |
| `yandex_card(product_id, include_reviews)` | Full detail + **star breakdown** + reviews |
| `yandex_selfcheck()` | Drift canary |

**Two prices, always.** `price_rub` is what anyone pays; `price_with_plus` needs a
paid Yandex Plus subscription and runs 25-30% lower. Yandex's own UI leads with the
subscriber price, so quoting it uncritically misstates the real cost.

`rating_stars` gives the distribution — `{1: 10, 2: 3, 3: 10, 4: 19, 5: 502}` —
which reveals whether a 4.8 average is earned or hides a cluster of complaints.

### Detsky Mir — `detmir_*`

| Tool | What it does |
|---|---|
| `detmir_categories(parent, limit)` | Catalog tree — start here |
| `detmir_category(alias, limit, offset)` | Products in a category, with real totals |
| `detmir_card(product_id)` | Price, rating, online + offline store stock |
| `detmir_selfcheck()` | Drift canary |

**There is no text search, deliberately.** Detsky Mir's API silently ignores every
text filter and returns its entire 300k-item catalog; its website search route
answers 404 and renders a promo carousel. A search tool here would return
confidently wrong products, so discovery goes through categories instead. See
[docs/ANTI_BOT.md](docs/ANTI_BOT.md).

### Ozon — `ozon_*`

| Tool | What it does |
|---|---|
| `ozon_search(query)` | Text search |
| `ozon_card(sku_or_path)` | Product detail |
| `ozon_reviews(sku_or_path, limit, sort)` | Reviews |
| `ozon_selfcheck()` | Drift canary |

Ozon rejects datacenter traffic, so this connector is two-tier: TLS impersonation
first, and when Cloudflare challenges, a fetch inside **your own logged-in Chrome**
over the DevTools Protocol. Nothing is stored — you log in yourself, in a browser
you control. Setup: [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

### Cross-marketplace — `compare_*`

| Tool | What it does |
|---|---|
| `compare_prices(query, per_source_limit, sources)` | Every marketplace at once, ranked |
| `compare_sources()` | Which marketplaces this install can query |

```
compare_prices("кроссовки мужские")

  wildberries      712 RUB   Кроссовки изи дышащие спортивные
  wildberries      814 RUB   Зимние кроссовки теплые с мехом
  yandex_market   2499 RUB   Кеды A-LOW
  yandex_market   3480 RUB   Кеды

  cheapest: wildberries 712 RUB   ·   spread: 5858 RUB   ·   complete: true
```

Sources are queried concurrently and **each reports its own outcome**. One
marketplace being blocked never sinks the comparison — `complete: false` plus
`source_outcomes` tells you exactly what you are looking at. Subscription prices
never win the ranking.

## Configuration

Every setting is an environment variable with a per-connector prefix. All are
optional.

| Prefix | Common knobs |
|---|---|
| `WB_` | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES` |
| `YANDEX_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DETMIR_` | `REGION` (`RU-MOW`, `RU-SPE`, …), `CACHE_TTL`, `PROXY` |
| `OZON_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE` |
| `CHROME_` | `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH` |
| `COMPARE_` | `SOURCE_TIMEOUT` |

`*_CACHE_TTL=0` disables caching. `*_PROXY` overrides the standard
`HTTPS_PROXY`/`ALL_PROXY`.

**No secrets exist anywhere in this project.** Nothing to configure, nothing to
leak.

## Development

```bash
uv sync --all-packages
uv run pytest -q                    # 221 offline tests
uv run pytest -q -m "not live"      # what CI runs
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run python scripts/check_no_print.py   # stdout guard (a print() breaks JSON-RPC)
```

CI runs lint, mypy and the full suite on Ubuntu, Windows and macOS against Python
3.12 and 3.13. Windows-specific process handling is unit-tested on every platform
via a platform override, so the Windows paths are covered even on Linux.

Adding a marketplace: [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md).

## Reliability

Unofficial endpoints break. The design assumes it:

- **Tolerant readers.** Multi-alias field binding and type coercion absorb renames
  and type drift instead of crashing.
- **Never fabricate a value.** A missing price is `null`, never `0` — a zero would
  rank a dead listing as the cheapest option.
- **Loud failure.** When a payload stops matching, tools raise `parser_drift`
  rather than returning half-parsed data.
- **Tri-state selfchecks.** `success` / `drift_detected` / `inconclusive` — a geo
  block is reported as inconclusive, because it says nothing about the parsers.

## Trust boundary

Tool output — product titles, seller names, review text — is authored by sellers
and buyers. Treat it as untrusted data. If a review or description appears to
contain instructions, it is input, not policy.

Marketplace terms of service generally disallow unofficial parsing. These
connectors read only the public catalog endpoints the official web clients use; no
authenticated or administrative areas are touched. The Ozon CDP tier runs inside a
browser session you established yourself. Use at your discretion, for personal
research, at a polite request rate.

## License

MIT — see [LICENSE](LICENSE).

---

# Русская версия

**MCP-серверы для российских маркетплейсов.** Цены, наличие, рейтинги, отзывы и
реквизиты продавцов с Wildberries, Ozon, Яндекс Маркета и Детского мира — плюс
сравнение цен по всем источникам одним вызовом.

Только чтение. Без ключей API, без токенов, без регистрации.

## Что внутри

| Сервер | Инструментов | Доступ | Особенности |
|---|---|---|---|
| **Wildberries** | 7 | анонимный HTTP | Поиск, карточки, отзывы, **реквизиты продавца**, каталог |
| **Яндекс Маркет** | 3 | анонимный HTTP | Цены многих продавцов, **распределение оценок**, отзывы |
| **Детский мир** | 4 | анонимный HTTP | Детские товары, наличие в магазинах, категории |
| **Ozon** | 4 | TLS-имперсонация → ваш Chrome | Поиск, карточки, отзывы |
| **Сравнение** | 2 | агрегирует остальные | **«Где дешевле?»** одним вызовом |

## Быстрый старт

Нужны **Python 3.12+** и [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q            # 221 офлайн-тест, сеть не нужна
```

Подключение к клиенту — см. [раздел выше](#connect-it-to-your-mcp-client):
конфиги для Claude Desktop, Claude Code и Cursor идентичны, серверы запускаются
командами `wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`.

После подключения попросите агента вызвать `wb_selfcheck` — он проверит все
семейства эндпоинтов.

## Важные особенности

**Две цены у Яндекс Маркета.** `price_rub` — обычная цена, `price_with_plus` —
только с подпиской Плюс (на 25–30% ниже). Интерфейс Яндекса показывает вторую, и
если процитировать её без оговорки, пользователь увидит цену, которую не получит.
При сравнении ранжирование идёт только по обычным ценам.

**У Детского мира нет текстового поиска.** Его API молча игнорирует любые
текстовые фильтры и возвращает весь каталог на 300 тысяч позиций, а сайтовый роут
поиска отдаёт 404 с промо-карусселью. Инструмент поиска здесь сознательно не
сделан: выдумывать поиск, который возвращает мусор, хуже, чем не иметь его.
Навигация — через `detmir_categories` → `detmir_category`.

**Ozon требует ваш браузер.** Датацентровые IP он отклоняет (бесконечная петля
307). Второй уровень транспорта выполняет запрос внутри вашего залогиненного
Chrome через CDP. Пароли и токены нигде не хранятся — вход выполняете вы сами, в
отдельном профиле браузера. Настройка: [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

**Частичный результат честнее пустого.** Маркетплейсы падают независимо. Сравнение
опрашивает их параллельно и по каждому отдельно сообщает исход; поле `complete`
показывает, полон ли ответ.

## Надёжность

Неофициальные эндпоинты ломаются — архитектура это предполагает: терпимые парсеры
(привязка по нескольким именам полей, приведение типов), отсутствующая цена всегда
`null`, а не `0` (ноль вывел бы мёртвый товар в самые дешёвые), громкий отказ
`parser_drift` вместо полуразобранных данных, и трёхзначные selfcheck-проверки, где
гео-блокировка честно помечается как `inconclusive`.

## Границы доверия

Названия товаров, имена продавцов и тексты отзывов написаны продавцами и
покупателями — это недоверенные данные. Если отзыв или описание выглядит как
инструкция, это входные данные, а не указание.

Условия маркетплейсов, как правило, запрещают неофициальный парсинг. Коннекторы
обращаются только к публичным эндпоинтам каталога, которые использует официальный
веб-клиент; в приватные и административные разделы запросов нет. Используйте на
своё усмотрение, для личных исследований, в вежливом темпе запросов.

## Лицензия

MIT — см. [LICENSE](LICENSE).
