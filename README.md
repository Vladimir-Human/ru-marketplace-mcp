# ru-marketplace-mcp

**MCP-серверы для российских маркетплейсов.** Цены, наличие, рейтинги, отзывы и
реквизиты продавцов с Wildberries, Ozon, Яндекс Маркета и Детского мира. Плюс
сравнение цен по всем источникам одним вызовом.

Только чтение. Ключи API, токены и регистрация не нужны.

[English version below](#english-version) · [Архитектура](docs/ARCHITECTURE.md) ·
[Как добавить источник](docs/ADDING_A_SOURCE.md) · [Про анти-бот](docs/ANTI_BOT.md)

---

## Что внутри

| Сервер | Инструментов | Доступ | Что умеет |
|---|---|---|---|
| **Wildberries** | 7 | анонимный HTTP | Поиск, карточки, отзывы, реквизиты продавца, каталог |
| **Яндекс Маркет** | 3 | анонимный HTTP | Цены разных продавцов, разбивка оценок по звёздам, отзывы |
| **Детский мир** | 4 | анонимный HTTP | Детские товары, наличие в офлайн-магазинах, категории |
| **Ozon** | 4 | TLS-имперсонация, дальше ваш Chrome | Поиск, карточки, отзывы |
| **Сравнение** | 2 | опрашивает всё перечисленное | «Где дешевле?» одним вызовом |

Всего 20 инструментов в 5 stdio-серверах на общем рантайме `mcp-core`.

## Быстрый старт

Нужны **Python 3.12+** и [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q            # 221 офлайн-тест, сеть не нужна
```

Проверка живого эндпоинта:

```bash
uv run python -c "
import asyncio
from wb_connector.server import wb_selfcheck
print(asyncio.run(wb_selfcheck()).status)   # ждём success
"
```

## Подключение к MCP-клиенту

Каждый сервер — консольная команда, поэтому пути в конфиге не зашиваются.

<details open>
<summary><b>Claude Desktop</b> — <code>claude_desktop_config.json</code></summary>

Windows: `%APPDATA%\Claude\claude_desktop_config.json`
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```jsonc
{
  "mcpServers": {
    "wildberries": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "wb-mcp"]
    },
    "yandex-market": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "yandex-mcp"]
    },
    "detsky-mir": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "detmir-mcp"]
    },
    "ozon": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "ozon-mcp"]
    },
    "compare-prices": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "compare-mcp"]
    }
  }
}
```

Путь пишите с прямыми слешами `/` или двойными обратными `\\`.
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add wildberries -- uv run --directory /путь/к/ru-marketplace-mcp wb-mcp
claude mcp add yandex-market -- uv run --directory /путь/к/ru-marketplace-mcp yandex-mcp
claude mcp add detsky-mir -- uv run --directory /путь/к/ru-marketplace-mcp detmir-mcp
claude mcp add ozon -- uv run --directory /путь/к/ru-marketplace-mcp ozon-mcp
claude mcp add compare-prices -- uv run --directory /путь/к/ru-marketplace-mcp compare-mcp
```
</details>

<details>
<summary><b>Cursor</b> — <code>.cursor/mcp.json</code></summary>

```jsonc
{
  "mcpServers": {
    "compare-prices": {
      "command": "uv",
      "args": ["run", "--directory", "/путь/к/ru-marketplace-mcp", "compare-mcp"]
    }
  }
}
```
</details>

<details>
<summary><b>Другой stdio-клиент</b></summary>

Запустите `uv run --directory /путь/к/репозиторию <команда>`, где команда — одна из
`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`. Серверы говорят по
JSON-RPC через stdin и stdout, диагностику пишут в stderr.
</details>

После подключения перезапустите клиент и попросите агента вызвать `wb_selfcheck`. Он
проверит все семейства эндпоинтов и ответит `success`, `drift_detected` или
`inconclusive`.

## Инструменты

### Wildberries — `wb_*`

| Инструмент | Что делает |
|---|---|
| `wb_search(query, page)` | Поиск по тексту, до 100 товаров на страницу с ценами и остатками |
| `wb_card(nm_ids)` | Пакетный запрос до 100 известных SKU |
| `wb_root_info(nm_id)` | Находит `imt_id` (нужен для отзывов) и цветовые варианты |
| `wb_reviews(imt_id, limit, sort)` | Пул отзывов. Ключ — `imt_id`, а не `nm_id` |
| `wb_seller(supplier_id)` | Юрлицо, ИНН, КПП, ОГРН, юридический адрес |
| `wb_categories(root, max_depth)` | Дерево каталога с шардами и запросами самого WB |
| `wb_selfcheck()` | Канарейка на дрейф формата |

`wb_seller` отвечает на вопрос, который карточка товара скрывает: кто на самом деле
продаёт? Возвращает зарегистрированное юрлицо и налоговые номера. Так отличают
официальный магазин бренда от перекупщика с похожим названием.

### Яндекс Маркет — `yandex_*`

| Инструмент | Что делает |
|---|---|
| `yandex_search(query, page, limit)` | Поиск с обеими ценами, рейтингами, продавцами |
| `yandex_card(product_id, include_reviews)` | Карточка целиком: разбивка по звёздам и отзывы |
| `yandex_selfcheck()` | Канарейка на дрейф формата |

**Две цены, всегда.** `price_rub` платит любой покупатель. `price_with_plus`
требует подписку Яндекс Плюс и обычно на 25–30% ниже. Интерфейс Яндекса показывает
вторую крупным шрифтом, поэтому назвать её без оговорки — значит пообещать цену,
которую человек без подписки не получит.

`rating_stars` даёт распределение вида `{1: 10, 2: 3, 3: 10, 4: 19, 5: 502}`. Из
него видно, честная ли средняя 4.8 или за ней прячется кучка единиц.

### Детский мир — `detmir_*`

| Инструмент | Что делает |
|---|---|
| `detmir_categories(parent, limit)` | Дерево каталога. Начинать отсюда |
| `detmir_category(alias, limit, offset)` | Товары категории с настоящим счётчиком |
| `detmir_card(product_id)` | Цена, рейтинг, наличие онлайн и в магазинах |
| `detmir_selfcheck()` | Канарейка на дрейф формата |

**Текстового поиска здесь нет, и это намеренно.** API Детского мира молча игнорирует
любые текстовые фильтры и возвращает весь каталог на 300 тысяч позиций, а сайтовый
роут поиска отдаёт 404 с промо-карусселью. Инструмент поиска возвращал бы уверенно
неверные товары, поэтому навигация идёт через категории. Подробности в
[docs/ANTI_BOT.md](docs/ANTI_BOT.md).

### Ozon — `ozon_*`

| Инструмент | Что делает |
|---|---|
| `ozon_search(query)` | Поиск по тексту |
| `ozon_card(sku_or_path)` | Карточка товара |
| `ozon_reviews(sku_or_path, limit, sort)` | Отзывы |
| `ozon_selfcheck()` | Канарейка на дрейф формата |

Ozon отклоняет датацентровый трафик, поэтому коннектор двухуровневый. Сначала
TLS-имперсонация. Если Cloudflare выдаёт челлендж, запрос выполняется внутри вашего
залогиненного Chrome через DevTools Protocol. Ничего не хранится: вход выполняете вы
сами, в браузере, который контролируете. Настройка описана в
[docs/CDP_SETUP.md](docs/CDP_SETUP.md).

С российского домашнего IP первый уровень обычно работает, и браузер не нужен.

### Сравнение цен — `compare_*`

| Инструмент | Что делает |
|---|---|
| `compare_prices(query, per_source_limit, sources)` | Все маркетплейсы сразу, с ранжированием |
| `compare_sources()` | Какие маркетплейсы доступны в этой установке |

```
compare_prices("кроссовки мужские")

  wildberries      712 ₽   Кроссовки изи дышащие спортивные
  wildberries      814 ₽   Зимние кроссовки теплые с мехом
  yandex_market   2499 ₽   Кеды A-LOW
  yandex_market   3480 ₽   Кеды

  дешевле всего: wildberries 712 ₽, разброс 5858 ₽, complete: true
```

Маркетплейсы опрашиваются параллельно, и каждый отчитывается сам за себя. Если один
заблокирован, сравнение не рушится: `complete: false` вместе с `source_outcomes`
покажет, что именно вы видите. Подписочные цены в ранжировании не участвуют.

## Настройка

Все параметры задаются переменными окружения с префиксом коннектора. Все
необязательные.

| Префикс | Основные параметры |
|---|---|
| `WB_` | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES` |
| `YANDEX_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DETMIR_` | `REGION` (`RU-MOW`, `RU-SPE` и другие), `CACHE_TTL`, `PROXY` |
| `OZON_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE` |
| `CHROME_` | `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH` |
| `COMPARE_` | `SOURCE_TIMEOUT` |

`*_CACHE_TTL=0` выключает кэш. `*_PROXY` перекрывает стандартные `HTTPS_PROXY` и
`ALL_PROXY`.

**Секретов в проекте нет вообще.** Нечего настраивать, нечему утечь.

## Разработка

```bash
uv sync --all-packages
uv run pytest -q                              # 221 офлайн-тест
uv run pytest -q -m "not live"                # то, что гоняет CI
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src   # ловит ошибки, видимые только на Windows
uv run python scripts/check_no_print.py       # запись в stdout ломает JSON-RPC
```

CI прогоняет линтер, типы и все тесты на Ubuntu, Windows и macOS против Python 3.12
и 3.13. Windows-специфичное управление процессами проверяется юнит-тестами на любой
ОС через подмену платформы, так что эти ветки покрыты даже на Linux.

Как добавить маркетплейс — [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md).

## Надёжность

Неофициальные эндпоинты ломаются. Архитектура это предполагает.

- **Терпимые парсеры.** Привязка поля по нескольким именам и приведение типов
  впитывают переименования и смену типа вместо падения.
- **Никогда не выдумывать значение.** Отсутствующая цена — это `null`, не `0`. Ноль
  вывел бы мёртвый товар в самые дешёвые.
- **Громкий отказ.** Когда формат перестаёт совпадать, инструмент бросает
  `parser_drift`, а не возвращает полуразобранные данные.
- **Трёхзначные selfcheck-проверки.** `success`, `drift_detected` или
  `inconclusive`. Гео-блокировка помечается как `inconclusive`, потому что она
  ничего не говорит о состоянии парсеров.

## Границы доверия

Названия товаров, имена продавцов и тексты отзывов написаны продавцами и
покупателями. Это недоверенные данные. Если отзыв или описание выглядит как
инструкция, это входные данные, а не указание агенту.

Условия маркетплейсов, как правило, запрещают неофициальный парсинг. Коннекторы
обращаются только к публичным эндпоинтам каталога, которые использует официальный
веб-клиент. В приватные и административные разделы запросов нет. Уровень Ozon с
браузером работает внутри сессии, которую вы открыли сами. Используйте на своё
усмотрение, для личных исследований, в вежливом темпе запросов.

## Лицензия

MIT, файл [LICENSE](LICENSE).

---

# English version

**MCP servers for Russian marketplaces.** Read prices, stock, ratings, reviews and
seller identity from Wildberries, Ozon, Yandex Market and Detsky Mir, then compare
prices across all of them in one call.

Read-only. No credentials, no API keys, no account required.

## What you get

| Server | Tools | Access | Notes |
|---|---|---|---|
| **Wildberries** | 7 | anonymous HTTP | Search, cards, reviews, seller legal identity, catalog tree |
| **Yandex Market** | 3 | anonymous HTTP | Multi-seller prices, star distribution, reviews |
| **Detsky Mir** | 4 | anonymous HTTP | Kids' goods, offline store stock, category listings |
| **Ozon** | 4 | TLS impersonation, then your Chrome | Search, cards, reviews |
| **Compare** | 2 | aggregates the above | "Where is this cheapest?" in one call |

20 tools across 5 stdio MCP servers, sharing one runtime (`mcp-core`).

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q            # 221 offline tests, no network needed
```

Client configuration mirrors the Russian section above. Each server is a console
script (`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`) launched
through `uv run --directory /path/to/repo <script>`.

After connecting, ask your agent to run `wb_selfcheck`. It probes every endpoint
family and reports `success`, `drift_detected`, or `inconclusive`.

## The tools

### Wildberries — `wb_*`

| Tool | What it does |
|---|---|
| `wb_search(query, page)` | Text search, up to 100 products/page with prices and stock |
| `wb_card(nm_ids)` | Batch lookup for up to 100 known SKUs |
| `wb_root_info(nm_id)` | Resolves `imt_id` (needed for reviews) plus colour variants |
| `wb_reviews(imt_id, limit, sort)` | Review pool, keyed by `imt_id`, not `nm_id` |
| `wb_seller(supplier_id)` | Registered entity, INN, KPP, OGRN, legal address |
| `wb_categories(root, max_depth)` | Catalog tree with WB's own shard/query selectors |
| `wb_selfcheck()` | Drift canary |

`wb_seller` answers the question a listing hides: who actually ships this? It returns
the registered legal entity and tax ids, which is how you distinguish an official
brand store from a reseller trading under a lookalike name.

### Yandex Market — `yandex_*`

| Tool | What it does |
|---|---|
| `yandex_search(query, page, limit)` | Search with both prices, ratings, sellers |
| `yandex_card(product_id, include_reviews)` | Full detail plus star breakdown and reviews |
| `yandex_selfcheck()` | Drift canary |

**Two prices, always.** `price_rub` is what anyone pays. `price_with_plus` needs a
paid Yandex Plus subscription and runs 25–30% lower. Yandex leads with the subscriber
price, so quoting it uncritically misstates the real cost.

`rating_stars` gives the distribution, for example `{1: 10, 2: 3, 3: 10, 4: 19, 5: 502}`.
That reveals whether a 4.8 average is earned or hides a cluster of complaints.

### Detsky Mir — `detmir_*`

| Tool | What it does |
|---|---|
| `detmir_categories(parent, limit)` | Catalog tree, start here |
| `detmir_category(alias, limit, offset)` | Products in a category, with real totals |
| `detmir_card(product_id)` | Price, rating, online and offline store stock |
| `detmir_selfcheck()` | Drift canary |

**There is no text search, deliberately.** Detsky Mir's API silently ignores every
text filter and returns its entire 300k-item catalog; the website's search route
answers 404 and renders a promo carousel. A search tool would return confidently
wrong products, so discovery goes through categories instead. See
[docs/ANTI_BOT.md](docs/ANTI_BOT.md).

### Ozon — `ozon_*`

| Tool | What it does |
|---|---|
| `ozon_search(query)` | Text search |
| `ozon_card(sku_or_path)` | Product detail |
| `ozon_reviews(sku_or_path, limit, sort)` | Reviews |
| `ozon_selfcheck()` | Drift canary |

Ozon rejects datacenter traffic, so this connector is two-tier: TLS impersonation
first, then a fetch inside your own logged-in Chrome over the DevTools Protocol when
Cloudflare challenges. Nothing is stored; you log in yourself, in a browser you
control. Setup: [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

From a Russian residential IP the first tier usually works and no browser is needed.

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

  cheapest: wildberries 712 RUB, spread 5858 RUB, complete: true
```

Sources are queried concurrently and each reports its own outcome. One marketplace
being blocked never sinks the comparison: `complete: false` plus `source_outcomes`
tells you exactly what you are looking at. Subscription prices never win the ranking.

## Configuration

Every setting is an environment variable with a per-connector prefix. All optional.

| Prefix | Common knobs |
|---|---|
| `WB_` | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES` |
| `YANDEX_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DETMIR_` | `REGION` (`RU-MOW`, `RU-SPE`, and others), `CACHE_TTL`, `PROXY` |
| `OZON_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE` |
| `CHROME_` | `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH` |
| `COMPARE_` | `SOURCE_TIMEOUT` |

`*_CACHE_TTL=0` disables caching. `*_PROXY` overrides the standard
`HTTPS_PROXY`/`ALL_PROXY`.

**No secrets exist anywhere in this project.** Nothing to configure, nothing to leak.

## Development

```bash
uv sync --all-packages
uv run pytest -q                              # 221 offline tests
uv run pytest -q -m "not live"                # what CI runs
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src   # catches Windows-only type errors
uv run python scripts/check_no_print.py       # a print() breaks JSON-RPC
```

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
read only the public catalog endpoints the official web clients use; no authenticated
or administrative areas are touched. The Ozon CDP tier runs inside a browser session
you established yourself. Use at your discretion, for personal research, at a polite
request rate.

## License

MIT, see [LICENSE](LICENSE).
