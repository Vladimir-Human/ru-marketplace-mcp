# ru-marketplace-mcp

[![CI](https://github.com/Vladimir-Human/ru-marketplace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Vladimir-Human/ru-marketplace-mcp/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20http-orange.svg)](https://modelcontextprotocol.io)

**MCP-серверы для российских и китайских маркетплейсов.** Цены, наличие,
рейтинги, отзывы и реквизиты продавцов с Wildberries, Ozon, Яндекс Маркета,
Детского мира, Авито, Taobao, Мегамаркета, Lamoda, DNS и Ситилинка. Плюс
сравнение цен по всем источникам одним вызовом.

Только чтение. Ключи API, токены и регистрация не нужны — площадки с жёстким
анти-ботом читаются через ваш собственный Chrome.

[English version below](#english-version) · [Архитектура](docs/ARCHITECTURE.md) ·
[Как добавить источник](docs/ADDING_A_SOURCE.md) · [Про анти-бот](docs/ANTI_BOT.md)

---

## Что внутри

| Сервер | Инструментов | Что нужно, чтобы читалось | Что умеет |
|---|---|---|---|
| **Wildberries** | 9 | анонимный HTTP | Поиск, карточки, отзывы, вопросы о товаре, реквизиты продавца, каталог и товары категории |
| **Яндекс Маркет** | 3 | анонимный HTTP | Цены разных продавцов, разбивка оценок по звёздам, отзывы |
| **Детский мир** | 4 | анонимный HTTP | Детские товары, наличие в офлайн-магазинах, категории |
| **Ozon** | 4 | ваш Chrome; с домашнего IP часто и без него | Поиск, карточки, отзывы |
| **Авито** | 4 | ваш Chrome + российский домашний IP и запросы вразрядку — иначе блок по IP | Поиск объявлений, карточки, репутация продавца |
| **Taobao** | 3 | ваш Chrome с активным входом в Taobao | Поиск и карточки, цены в юанях |
| **Мегамаркет** | 3 | ваш Chrome с активным входом — анонимной сессии API отдаёт пусто | Поиск и карточки через мобильный API |
| **Lamoda** | 3 | карточки анонимно (GraphQL), поиск — ваш Chrome | Поиск, карточки с размерами |
| **DNS** | 3 | ваш Chrome (Qrator) | Поиск и карточки электроники |
| **Ситилинк** | 3 | ваш Chrome (Qrator) | Поиск и карточки электроники |
| **Сравнение** | 2 | опрашивает всё перечисленное | «Где дешевле?» одним вызовом |

Читается анонимно, без браузера: Wildberries, Яндекс Маркет, Детский мир и
карточки Lamoda. Остальным нужен ваш залогиненный Chrome (CDP). Taobao и
Мегамаркет вдобавок требуют активного входа в саму площадку — без него Taobao
упирается в стену логина, а Мегамаркет отдаёт пустой ответ. Авито ещё и блокирует
по IP: с датацентрового адреса это глухой отказ, с российского домашнего — работает,
если не частить запросами. Запросы к CDP-источникам идут вразрядку: очередь
подряд без пауз роняет их (DNS и Taobao в проверке так и деградировали), поэтому
коннекторы держат паузу между вызовами сами. Точное состояние из вашей сессии
покажет `*_selfcheck`.

Всего 41 инструмент в 11 серверах на общем рантайме `mcp-core`. Плюс объединённый
`marketplace-mcp`, который монтирует всё разом — одна запись в конфиге клиента
вместо одиннадцати. Он добавляет свой инструмент `marketplace_sources` (какие коннекторы
поднялись, а какие отвалились и почему), так что в нём 42 инструмента: 41
смонтированный плюс этот.

## Быстрый старт

Нужны **Python 3.12+** и [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"   # 822 офлайн-теста, сеть не нужна
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

Проще всего подключить **одну запись** — объединённый сервер монтирует все
источники разом, а имена инструментов (`wb_search`, `avito_seller`, …) не
меняются:

```jsonc
{
  "mcpServers": {
    "marketplace": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "marketplace-mcp"]
    }
  }
}
```

Если нужны отдельные серверы, `marketplace-mcp install claude` напечатает
готовый блок для вставки — с реальным путём к вашему checkout, а не с заглушкой
`/path/to/ru-marketplace-mcp`, которую пришлось бы править руками. При установке
из wheel вместо путей печатаются консольные команды на PATH, а неизвестное имя
клиента (допустимы `claude`, `claude-code`, `cursor`) отклоняется, а не молча
подменяется блоком для Claude. Минимальный вариант вручную:

```jsonc
{
  "mcpServers": {
    "wildberries": {
      "command": "uv",
      "args": ["run", "--directory", "C:/путь/к/ru-marketplace-mcp", "wb-mcp"]
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

Путь пишите с прямыми слешами `/` или двойными обратными `\\`. Полный список
команд — `wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `avito-mcp`,
`taobao-mcp`, `megamarket-mcp`, `lamoda-mcp`, `dns-mcp`, `citilink-mcp`,
`compare-mcp`, `marketplace-mcp`.
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
| `wb_questions(imt_id, limit, skip, answered_only)` | Вопросы покупателей и ответы продавца. Тоже по `imt_id` |
| `wb_seller(supplier_id)` | Юрлицо, ИНН, КПП, ОГРН, юридический адрес |
| `wb_categories(root, max_depth)` | Дерево каталога с шардами и запросами самого WB |
| `wb_category_products(shard, query, page, sort, dest)` | Товары категории по `shard` и `query` из `wb_categories` |
| `wb_selfcheck()` | Канарейка на дрейф формата |

`wb_seller` отвечает на вопрос, который карточка товара скрывает: кто на самом деле
продаёт? Возвращает зарегистрированное юрлицо и налоговые номера. Так отличают
официальный магазин бренда от перекупщика с похожим названием.

`wb_questions` закрывает другой пробел. Отзывы говорят, каково владеть товаром;
вопросы уточняют, что это вообще за товар — «10 или 16 ампер», «кабель в комплекте?».
Ответ продавца часто единственное публичное утверждение об этом. Пул общий для всех
вариантов товара, ключ — `imt_id` из `wb_root_info`.

`wb_category_products` замыкает связку с `wb_categories`: та отдаёт `shard` и `query`,
это — товары по ним. Формат элементов совпадает с `wb_search`, поэтому обход категорий
и текстовый поиск сравнимы напрямую. Часть крупных разделов WB помечает шардом
`blackhole` — у них нет своей выдачи, и инструмент честно об этом говорит вместо
пустого списка.

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
| `detmir_categories(parent, limit, region)` | Дерево каталога. Начинать отсюда |
| `detmir_category(alias, limit, offset, region)` | Товары категории с настоящим счётчиком |
| `detmir_card(product_id, region)` | Цена, рейтинг, наличие онлайн и в магазинах |
| `detmir_selfcheck()` | Канарейка на дрейф формата |

**Регион задаётся на каждый вызов.** Цены и особенно наличие в офлайн-магазинах
сильно зависят от города: один и тот же товар лежал в 152 магазинах Москвы, 37
Петербурга и 2 Хабаровска. Параметр `region` перекрывает `DETMIR_REGION`, так что
города можно сравнивать в одной сессии.

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

### Авито — `avito_*`

| Инструмент | Что делает |
|---|---|
| `avito_search(query, page, location_id, category_id)` | Поиск объявлений через внутренний `js/items` API |
| `avito_card(item_id_or_url)` | Одно объявление: цена, описание, просмотры, продавец |
| `avito_seller(seller_id_or_url)` | Рейтинг продавца, число отзывов, активные объявления |
| `avito_selfcheck()` | Канарейка на дрейф формата |

Авито — это объявления, а не каталог: пула отзывов на товар нет, репутация
продавца и есть сигнал доверия. Бесплатное/обменное объявление приходит с
`price_rub: null` — никогда не `0`, чтобы не оказаться «самым дешёвым» в
сравнении. С датацентрового IP Авито отвечает 403-файрволом, поэтому коннектор
двухуровневый: TLS-имперсонация, дальше ваш Chrome (как у Ozon).

### Taobao — `taobao_*`

| Инструмент | Что делает |
|---|---|
| `taobao_search(query, page)` | Поиск по каталогу Taobao |
| `taobao_card(item_id_or_url)` | Карточка товара |
| `taobao_selfcheck()` | Канарейка на дрейф формата |

Поиск Taobao — клиентское React-приложение с подписанным mtop API: каждый запрос
требует `sign`, вычисленный из cookie-токена, поэтому анонимного пути нет.
Все чтения идут внутри вашего Chrome, где сайт сам подписывает запросы. **Цены в
юанях (CNY)** и не конвертируются: зашитый курс молча устарел бы, так что
сравнение с рублёвыми источниками делайте явно.

### Мегамаркет, Lamoda, DNS, Ситилинк

Эти четыре читаются через ваш Chrome (CDP). Мегамаркет (`megamarket_*`) — мобильный
JSON API из-за ServicePipe, и одного пройденного челленджа мало: анонимной сессии
API отдаёт пустой список, нужен активный вход в Мегамаркет. DNS (`dns_*`) и Ситилинк
(`citilink_*`) — отрисованный DOM из-за Qrator; у всех трёх анонимного пути нет вообще.
Lamoda (`lamoda_*`) наполовину: карточки берутся анонимно через GraphQL, а поиск —
через Chrome. Chrome с CDP (`scripts/start_chrome_cdp.sh`) нужен всем, кроме карточек
Lamoda.

Всего через CDP ходят семь источников — эти плюс Ozon и Авито, где Chrome лишь
запасной уровень: их tier 1 обычно отвечает, а браузер включается, когда анонимный
уровень упёрся в челлендж. Проверка `*_selfcheck` из вашего браузера скажет, какие
эндпоинты подтверждены.

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
Совпадающие предложения по паре (источник, id товара) схлопываются, так что один
и тот же товар не занимает два места в ранжировании.

У каждого предложения есть `currency` (строчный ISO-код, по умолчанию `rub`) и
`price_native` — цена в этой валюте, как её показывает маркетплейс. Для российских
источников она совпадает с `price_rub`; у Taobao в ней лежит цена в юанях, которую
`price_rub` намеренно оставляет пустой. Раньше юаневую цену забирали и молча
выбрасывали, и строка Taobao приходила с пустой ценой без намёка, что цена вообще
есть. Теперь юань виден, но в рублёвом ранжировании по-прежнему не участвует: в
`warnings` появляется `foreign_currency: …` с числом исключённых предложений и
причиной. Конвертировать здесь значило бы зашить курс, который молча устареет, —
пересчёт за вами.

## Навыки для агента

У каждого коннектора — свой навык в `skills/`, двенадцать штук на двенадцать
серверов. Навык это не пересказ README: он объясняет агенту, когда за этот
источник вообще браться, чего у источника нет, и каким его ответам нельзя верить
без второго взгляда.

| Навык | Сервер |
|---|---|
| `skills/wb-connector` | `wb-mcp` |
| `skills/ozon-connector` | `ozon-mcp` |
| `skills/yandex-connector` | `yandex-mcp` |
| `skills/detmir-connector` | `detmir-mcp` |
| `skills/avito-connector` | `avito-mcp` |
| `skills/taobao-connector` | `taobao-mcp` |
| `skills/megamarket-connector` | `megamarket-mcp` |
| `skills/lamoda-connector` | `lamoda-mcp` |
| `skills/dns-connector` | `dns-mcp` |
| `skills/citilink-connector` | `citilink-mcp` |
| `skills/compare-prices` | `compare-mcp` |
| `skills/marketplace` | `marketplace-mcp` |

`mcp-core` — общий рантайм, а не сервер, и навыка у него нет.

Соответствие проверяется тестом
(`packages/marketplace-connector/tests/test_skills_parity.py`): новый коннектор
без навыка роняет прогон, как и навык, который называет несуществующий
инструмент или забыл существующий. До этого теста навык DNS почти год советовал
формат ссылки `/product/<24-hex>/` — тот самый шаблон, который чинили как баг.

Скиллы едут в Docker-образ (`/app/skills/`), но **в колёсах их нет**: `skills/`
лежит в корне репозитория, а не внутри пакетов. Ставите с PyPI — возьмите навыки
из репозитория отдельно.

## Настройка

Все параметры задаются переменными окружения с префиксом коннектора. Все
необязательные.

| Префикс | Основные параметры |
|---|---|
| `WB_` | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES`, `CACHE_TTL`, `PROXY` |
| `YANDEX_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DETMIR_` | `REGION` (`RU-MOW`, `RU-SPE` и другие), `CACHE_TTL`, `PROXY` |
| `OZON_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY` |
| `AVITO_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY`, `LOCATION_ID` |
| `TAOBAO_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `MEGAMARKET_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL` |
| `LAMODA_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DNS_` / `CITILINK_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL` |
| `CHROME_` | `CDP_HOST`, `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH` |
| `COMPARE_` | `SOURCE_TIMEOUT` |
| `MCP_` | `TRANSPORT` (`stdio` по умолчанию, либо `http`), `HTTP_HOST`, `HTTP_PORT` |

`CHROME_CDP_HOST` указывает, куда дозвониться CDP-клиенту (по умолчанию
`127.0.0.1`). Из контейнера ставьте `chrome` (сайдкар) или `host.docker.internal`
— это открывает tier-2 источники (Ozon, Авито, Taobao, Мегамаркет, Lamoda,
DNS, Ситилинк) в Docker без host networking. Подробности в
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

`*_CACHE_TTL=0` выключает кэш. `*_PROXY` перекрывает стандартные `HTTPS_PROXY` и
`ALL_PROXY` — свой префикс есть у семи коннекторов: `WB_`, `YANDEX_`, `DETMIR_`,
`OZON_`, `AVITO_`, `TAOBAO_` и `LAMODA_`. У Мегамаркета, DNS и Ситилинка своего нет:
их трафик идёт через ваш Chrome, а его egress — дело настроек браузера. Кэшируются
только удачные ответы: запомнить сбой значило бы растянуть секундную помеху на весь
TTL.

У Ozon прокси применяется к первому уровню. Второй идёт через ваш собственный Chrome,
и его трафик — дело настроек этого браузера, а не наших.

**Секретов в проекте нет вообще.** Нечего настраивать, нечему утечь.

## Разработка

```bash
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"    # 822 офлайн-теста
uv run pytest -q -m "not live"                # то, что гоняет CI
uv run pytest -q -m "not live" --cov          # покрытие, порог 70% в CI
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src   # ловит ошибки, видимые только на Windows
uv run python scripts/check_no_print.py       # запись в stdout ломает JSON-RPC
```

Часть тестов прогоняет **настоящий JS-экстрактор коннектора** по снятой разметке
и проверяет результат против цен, которые в тот момент были на странице. Для
этого нужен Node с jsdom:

```bash
npm install jsdom      # либо NODE_PATH на уже установленный
uv run pytest -q packages/dns-connector/tests/test_search_extractor_dom.py \
              packages/citilink-connector/tests/test_search_extractor_dom.py
```

Без jsdom эта половина честно скипается, а питоновская часть — выбор цены из
кандидатов — идёт всегда. jsdom нужен только разработчику: в зависимости
коннекторов он не входит.

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

**MCP servers for Russian and Chinese marketplaces.** Read prices, stock, ratings,
reviews and seller identity from Wildberries, Ozon, Yandex Market, Detsky Mir, Avito,
Taobao, Megamarket, Lamoda, DNS and Citilink, then compare prices across all of them
in one call. Taobao is the Chinese one; the other nine are Russian.

Read-only. No credentials, no API keys, no account required — the marketplaces with
hard anti-bot are read through your own Chrome.

## What you get

| Server | Tools | What it takes to read | Notes |
|---|---|---|---|
| **Wildberries** | 9 | anonymous HTTP | Search, cards, reviews, buyer questions, seller legal identity, catalog tree and category listings |
| **Yandex Market** | 3 | anonymous HTTP | Multi-seller prices, star distribution, reviews |
| **Detsky Mir** | 4 | anonymous HTTP | Kids' goods, offline store stock, category listings |
| **Ozon** | 4 | your Chrome; often no browser from a residential IP | Search, cards, reviews |
| **Avito** | 4 | your Chrome + a Russian residential IP and spaced requests — else an IP block | Classified search, cards, seller reputation |
| **Taobao** | 3 | your Chrome with an active Taobao login | Search and cards, prices in yuan |
| **Megamarket** | 3 | your Chrome with an active login — an anonymous session reads empty | Search and cards via the mobile API |
| **Lamoda** | 3 | cards anonymous (GraphQL), search via your Chrome | Search, cards with sizes |
| **DNS** | 3 | your Chrome (Qrator) | Electronics search and cards |
| **Citilink** | 3 | your Chrome (Qrator) | Electronics search and cards |
| **Compare** | 2 | aggregates the above | "Where is this cheapest?" in one call |

Anonymous, no browser: Wildberries, Yandex Market, Detsky Mir and Lamoda cards.
The rest need your logged-in Chrome (CDP). Taobao and Megamarket additionally need
you signed into the marketplace itself — without it Taobao hits a login wall and
Megamarket returns an empty result. Avito also blocks by IP: from a datacenter
address it is a flat refusal, from a Russian residential one it works as long as
you do not burst requests. Requests to the CDP sources are paced apart — a run of
back-to-back calls degrades them (DNS and Taobao both dropped that way in testing),
so the connectors hold a gap between calls themselves. Run `*_selfcheck` from your
own session for the current state.

41 tools across 11 stdio MCP servers, sharing one runtime (`mcp-core`), plus the
unified `marketplace-mcp` that mounts them all under one client entry. It adds its
own `marketplace_sources` tool — which connectors mounted, and which dropped out and
why — so it exposes 42 tools: the 41 mounted plus that one. stdio is the default;
HTTP transport is opt-in for remote deployment — see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"    # 822 offline tests, no network needed
```

Client configuration mirrors the Russian section above. Each server is a console
script (`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`) launched
through `uv run --directory /path/to/repo <script>`. `marketplace-mcp install
[claude|claude-code|cursor]` prints the block with your checkout's real path filled
in — no placeholder to hand-edit — or the console-script paths on PATH when installed
as a wheel; an unknown client name is rejected.

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
| `wb_questions(imt_id, limit, skip, answered_only)` | Buyer questions with seller answers, also keyed by `imt_id` |
| `wb_seller(supplier_id)` | Registered entity, INN, KPP, OGRN, legal address |
| `wb_categories(root, max_depth)` | Catalog tree with WB's own shard/query selectors |
| `wb_category_products(shard, query, page, sort, dest)` | Products in a category, using those selectors |
| `wb_selfcheck()` | Drift canary |

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
| `detmir_categories(parent, limit, region)` | Catalog tree, start here |
| `detmir_category(alias, limit, offset, region)` | Products in a category, with real totals |
| `detmir_card(product_id, region)` | Price, rating, online and offline store stock |
| `detmir_selfcheck()` | Drift canary |

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
Offers matching on (source, product id) are collapsed, so one listing can no longer
take two ranking slots.

Every offer carries `currency` (lowercase ISO code, default `rub`) and `price_native`,
the price in that currency as the marketplace quotes it. For Russian sources it mirrors
`price_rub`; for Taobao it holds the yuan price that `price_rub` deliberately leaves
null. That yuan price used to be fetched and silently thrown away, so a Taobao row
showed a blank price with no sign a real one existed. Now the yuan is reported but
still never ranked against roubles: a `foreign_currency: …` warning lists how many
offers were excluded and why. Converting here would bake in an exchange rate that goes
stale silently, so the caller converts if they want to.

## Agent skills

Every connector ships its own skill under `skills/` — twelve of them for twelve
servers. A skill is not a restatement of this README: it tells the agent when to
reach for that source at all, what the source does not have, and which of its
answers should not be trusted without a second look.

| Skill | Server |
|---|---|
| `skills/wb-connector` | `wb-mcp` |
| `skills/ozon-connector` | `ozon-mcp` |
| `skills/yandex-connector` | `yandex-mcp` |
| `skills/detmir-connector` | `detmir-mcp` |
| `skills/avito-connector` | `avito-mcp` |
| `skills/taobao-connector` | `taobao-mcp` |
| `skills/megamarket-connector` | `megamarket-mcp` |
| `skills/lamoda-connector` | `lamoda-mcp` |
| `skills/dns-connector` | `dns-mcp` |
| `skills/citilink-connector` | `citilink-mcp` |
| `skills/compare-prices` | `compare-mcp` |
| `skills/marketplace` | `marketplace-mcp` |

`mcp-core` is the shared runtime rather than a server, so it has no skill.

The mapping is enforced by a test
(`packages/marketplace-connector/tests/test_skills_parity.py`): a new connector
without a skill fails the run, and so does a skill that names a tool which does
not exist — or omits one that does. Before that test existed, the DNS skill spent
months telling operators to pass `/product/<24-hex>/`, the exact pattern a fix had
already removed.

Skills are copied into the Docker image (`/app/skills/`), but they are **not in
the wheels**: `skills/` lives at the repository root rather than inside the
packages. Installing from PyPI means fetching the skills from the repo separately.

## Configuration

Every setting is an environment variable with a per-connector prefix. All optional.

| Prefix | Common knobs |
|---|---|
| `WB_` | `TIMEOUT`, `MIN_GAP`, `DEFAULT_DEST`, `NET_RETRIES`, `MAX_BODY_BYTES`, `CACHE_TTL`, `PROXY` |
| `YANDEX_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DETMIR_` | `REGION` (`RU-MOW`, `RU-SPE`, and others), `CACHE_TTL`, `PROXY` |
| `OZON_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY` |
| `AVITO_` | `TIMEOUT`, `MIN_GAP`, `IMPERSONATE`, `CACHE_TTL`, `PROXY`, `LOCATION_ID` |
| `TAOBAO_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `MEGAMARKET_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL` |
| `LAMODA_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL`, `PROXY` |
| `DNS_` / `CITILINK_` | `TIMEOUT`, `MIN_GAP`, `CACHE_TTL` |
| `CHROME_` | `CDP_HOST`, `CDP_PORT`, `SCRAPING_PROFILE`, `BINARY`, `HEADLESS`, `STEALTH` |
| `COMPARE_` | `SOURCE_TIMEOUT` |
| `MCP_` | `TRANSPORT` (`stdio` default, or `http`), `HTTP_HOST`, `HTTP_PORT` |

`*_CACHE_TTL=0` disables caching. `*_PROXY` overrides the standard
`HTTPS_PROXY`/`ALL_PROXY` — seven connectors carry one: `WB_`, `YANDEX_`, `DETMIR_`,
`OZON_`, `AVITO_`, `TAOBAO_` and `LAMODA_`. Megamarket, DNS and Citilink have none:
their traffic goes through your own Chrome, whose egress is that browser's
configuration. Only successful reads are cached: remembering a failure would stretch
a one-second blip across the whole TTL window.

Ozon's proxy applies to tier 1. Tier 2 runs inside your own Chrome, whose egress is
that browser's configuration, not ours.

**No secrets exist anywhere in this project.** Nothing to configure, nothing to leak.

## Development

```bash
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"    # 822 offline tests
uv run pytest -q -m "not live"                # what CI runs
uv run pytest -q -m "not live" --cov          # coverage, CI enforces a 70% floor
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src   # catches Windows-only type errors
uv run python scripts/check_no_print.py       # a print() breaks JSON-RPC
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
read only the public catalog endpoints the official web clients use; no authenticated
or administrative areas are touched. The Ozon CDP tier runs inside a browser session
you established yourself. Use at your discretion, for personal research, at a polite
request rate.

## License

MIT, see [LICENSE](LICENSE).
