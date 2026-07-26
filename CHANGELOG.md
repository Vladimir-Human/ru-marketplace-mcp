# Changelog

Здесь записаны все заметные изменения. Формат — по
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), нумерация версий — по
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Русский текст первый, английский — ниже в каждом разделе. Аудитория проекта
русскоязычная, и переводить для неё собственные заметки о релизе странно.

## [1.1.0] — 2026-07-26

Технический долг, дыры в функциональности и два новых инструмента Wildberries.
Имена и сигнатуры двадцати инструментов версии 1.0.0 не менялись: на них завязаны
конфиги MCP-клиентов, так что только добавления.

### Добавлено

**Новые инструменты Wildberries**
- `wb_questions(imt_id, limit, skip, answered_only)` — вопросы покупателей и ответы
  продавца. Отзывы рассказывают, каково владеть товаром; вопросы уточняют, что это
  за товар. Ответ продавца часто единственное публичное утверждение о том, чего нет
  в описании. Эндпоинт проверен живьём на шести товарах до того, как была написана
  первая строка кода: у него три ловушки, каждая из которых выглядит как пустой
  результат, а не как ошибка. Подробности в `docs/ANTI_BOT.md`.
- `wb_category_products(shard, query, page, sort, dest)` — товары категории по
  `shard` и `query`, которые отдаёт `wb_categories`. Раньше эти селекторы было
  некуда применить. Формат элементов совпадает с `wb_search`, поэтому обход
  категорий и текстовый поиск сравнимы напрямую.

**Регион Детского мира на каждый вызов**
- У всех четырёх инструментов появился параметр `region`, он перекрывает
  `DETMIR_REGION`. До этого сменить город можно было только перезапуском сервера,
  что посреди диалога с агентом невозможно.

**Кэш и прокси у Wildberries и Ozon**
- `WB_CACHE_TTL`, `WB_PROXY`, `OZON_CACHE_TTL`, `OZON_PROXY`. README и SECURITY
  обещали `*_PROXY` у всех коннекторов, а на деле он был у двух из четырёх.
  Кэшируются только удачные ответы: запомнить сбой значило бы растянуть секундную
  помеху на весь TTL, а для Ozon кэш блокировки неотличим от настоящей.

**Запуск и развёртывание**
- HTTP-транспорт как опция (`MCP_TRANSPORT=http`). По умолчанию по-прежнему stdio,
  так что существующие конфиги клиентов работают без правок.
- Docker-образ и `compose`. Ограничения второго уровня Ozon в контейнере описаны
  честно, а не замазаны: `docs/DEPLOYMENT.md`.
- `server.json` — манифест для реестра MCP-серверов.

**Инфраструктура**
- Измерение покрытия тестами в CI с порогом 70% (фактическое покрытие ветвей —
  74%, порог взят с запасом, чтобы не ломать сборку из-за постороннего шума).
- `check_untyped_defs` включён для `mcp_core`.
- Dependabot для `uv` и GitHub Actions.
- Релизный workflow: по тегу `v*` собираются wheels и sdist всех шести пакетов и
  прикладываются к релизу. Публикации в PyPI нет — имена пакетов ещё не решены.
- Шаблоны issue и PR, `CODE_OF_CONDUCT.md`, бейджи в README.

### Исправлено

- **Карточка Детского мира игнорировала регион.** Она не отправляла фильтр региона
  вообще, но подписывала ответ значением `DETMIR_REGION`. Из-за этого
  `store_count` всегда был 0, а ответ выглядел достоверным. Регион работает только
  через `filter=withregion:`; форма `?withregion=` принимается и молча
  игнорируется. Один и тот же товар: 152 магазина в Москве, 37 в Петербурге, 2 в
  Хабаровске.
- **Адаптер Ozon в сравнении цен был нерабочим.** Он читал поля `price_rub`,
  `reviews_count`, `feedbacks`, `name`, `id`, `brand` — ни одного из них нет в
  `OzonSearchItemOut`. Все молча превращались в `None`. Хуже: те поля, что он
  всё-таки находил, — это текст для показа (`1 234 ₽`, `4,8`), а `price_rub` в
  модели `float | None`, так что pydantic ронял валидацию и весь источник целиком.
- **Дубли в выдаче Яндекса.** Один товар может занимать несколько сниппетов на
  странице. Дедупликация идёт до применения `limit`, иначе дубль съедал часть
  запрошенного объёма без всяких пояснений.

### Изменено

- `MetaOut` и модели selfcheck переехали в `mcp_core.models`. Не одним плоским
  классом: у Яндекса есть поле `extraction`, у Детского мира — `cached`, и слить их
  значило бы удалить оба и сломать два контракта. Сериализованный JSON всех 37
  моделей побайтово совпадает с 1.0.0.
- Логика запросов Wildberries поднята в ядро как `get_text_budgeted`: общий
  дедлайн на всю операцию, ошибки возвращаются классифицированной строкой, а не
  бросаются, вежливая пауза соблюдается и перед повтором. Обратное направление
  (перевести WB на более слабый общий хелпер) означало бы регресс.
- Отображение товара WB в карточку было скопировано в трёх местах — теперь одно
  `_card_item_dict`. Правило `in_stock` живёт в одном месте: остаток без цены —
  это непродаваемая позиция, и назвать её доступной значило бы вывести мёртвый
  товар в самые дешёвые.
- Тесты `mcp_core.process` переехали из набора Ozon в `mcp-core`.
- Тестов стало 406 вместо 221.

### Не сделано намеренно

- **`ozon_seller`.** Реквизиты продавца Ozon — прямой аналог `wb_seller`, и спайк
  был. Путь верный, id продавца уже приходит в `ozon_card` как `seller.link`, но с
  датацентрового IP каждый запрос заканчивается 403 от анти-бота. Контрольная
  проверка показательнее самих попыток: уже работающий путь `/product/{id}/` падает
  точно так же — блокируют IP, а не адрес. Значит, эндпоинт почти наверняка живой,
  а вот **пути к полям никто не видел**. Писать парсер под неувиденную структуру —
  это придумать имена полей и отдать то, что случайно совпадёт. Инструмент,
  возвращающий правдоподобное название чужого юрлица, хуже отсутствующего:
  проверяют продавца ровно для того, чтобы отличить официальный магазин от
  похожего перекупщика. Шаблон URL и порядок проверки — в `RELEASE_PROMPT.md`.

---

## [1.1.0] — 2026-07-26 (English)

Technical debt, functional gaps, and two new Wildberries tools. The 20 tool names
and signatures from 1.0.0 are untouched: MCP client configs depend on them, so this
release only adds.

### Added

- `wb_questions(imt_id, limit, skip, answered_only)` — buyer questions with seller
  answers. Verified live across six products before any code was written; the
  endpoint has three failure modes that each look like an empty result rather than
  an error (see `docs/ANTI_BOT.md`).
- `wb_category_products(shard, query, page, sort, dest)` — the products behind the
  `shard`/`query` selectors `wb_categories` already returned and nothing consumed.
- Per-call `region` on all four Detsky Mir tools, overriding `DETMIR_REGION`.
- `WB_CACHE_TTL`, `WB_PROXY`, `OZON_CACHE_TTL`, `OZON_PROXY` — the docs promised
  `*_PROXY` everywhere while only two connectors had it. Only successful reads are
  cached.
- Optional HTTP transport (`MCP_TRANSPORT=http`); stdio remains the default, so
  existing client configs keep working.
- Docker image and compose, with the Ozon tier-2 limitations documented rather than
  glossed over: `docs/DEPLOYMENT.md`.
- `server.json` registry manifest.
- CI coverage gate at 70% (measured branch coverage is 74%), `check_untyped_defs`
  for `mcp_core`, Dependabot, a tag-triggered release workflow that builds wheels
  and sdists for all six packages, issue/PR templates, `CODE_OF_CONDUCT.md`, README
  badges.

### Fixed

- **`detmir_card` ignored the region entirely** — it sent no region filter but
  labelled the response with `DETMIR_REGION`, so `store_count` was always 0 while
  the answer looked authoritative. Only `filter=withregion:` works; `?withregion=`
  is accepted and silently ignored.
- **The compare connector's Ozon adapter could not work.** It read six field names
  `OzonSearchItemOut` does not declare, and the fields it did hit are display text
  (`1 234 ₽`, `4,8`) where `price_rub` is `float | None`, so pydantic failed
  validation and killed the whole source.
- **Duplicate products in Yandex search results**, deduped by `product_id` before
  the limit is applied so a repeat cannot eat the caller's page budget.

### Changed

- `MetaOut` and the selfcheck envelopes moved into `mcp_core.models` as base
  classes, not one flat class: Yandex adds `extraction`, Detsky Mir adds `cached`,
  and flattening would have deleted both. Serialized JSON for all 37 response
  models is byte-identical to 1.0.0.
- Wildberries' request logic was promoted into the core as `get_text_budgeted`
  (whole-operation deadline, classified error strings instead of exceptions, polite
  gate re-entered before each retry) rather than porting WB down to the weaker
  shared helper.
- The WB product-to-card mapping, previously copy-pasted three times, is one
  `_card_item_dict`.
- `mcp_core.process` tests moved out of the Ozon suite into `mcp-core`.
- 406 tests, up from 221.

### Deliberately not shipped

- **`ozon_seller`.** The path is right and the seller id already arrives via
  `ozon_card`'s `seller.link`, but every request from a datacenter IP ends in an
  anti-bot 403 — and the repo's already-working `/product/{id}/` path fails
  identically, which is what proves the IP is gated rather than the URL wrong. The
  endpoint is almost certainly live; the field paths are what nobody has seen.
  A seller tool returning a plausible name for the wrong legal entity is worse than
  no tool, since the only reason to look a seller up is telling an official store
  from a lookalike. Template and verification steps: `RELEASE_PROMPT.md`.

---

## [1.0.0] — 2026-07-26

First public release. The project grew from two connectors into a uv workspace of
five MCP servers over a shared runtime.

### Added

**New marketplaces**
- **Yandex Market** connector (`yandex_search`, `yandex_card`, `yandex_selfcheck`).
  Reads the server-rendered widget state, since Yandex exposes no usable JSON API.
  Reports the everyday price and the Plus-subscriber price separately, plus the
  per-star rating distribution and server-rendered reviews.
- **Detsky Mir** connector (`detmir_card`, `detmir_category`, `detmir_categories`,
  `detmir_selfcheck`) over its anonymous public JSON API, including offline store
  availability.

**Cross-marketplace comparison**
- New `compare-connector` with `compare_prices` and `compare_sources`. Queries
  every installed marketplace concurrently, ranks offers by everyday price, and
  reports a per-source outcome so a partial result is never mistaken for a
  complete one. Subscription-only prices are excluded from ranking.

**New Wildberries tools**
- `wb_seller(supplier_id)` — registered legal entity, INN, KPP, OGRN, legal
  address and trademark behind a seller id.
- `wb_categories(root, max_depth)` — catalog tree with WB's own shard/query
  selectors, bounded so a response stays a usable size.

**Shared runtime (`mcp-core`)**
- `transport.http_tier` — polite rate limiting, capped bodies, and retries scoped
  to transport faults and gateway statuses (429 deliberately excluded).
- `transport.chrome_cdp` — the authenticated tier, generalised out of the Ozon
  connector and now cross-platform.
- `process` — cross-platform worker spawn/reap with an allowlisted child
  environment.
- `cache` — in-process TTL cache with concurrent-miss collapsing.
- Proxy support across connectors via `*_PROXY` or the standard proxy variables.

**Project infrastructure**
- uv workspace monorepo; each connector is an installable package with a console
  script (`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`).
- GitHub Actions CI: ruff, mypy and the test suite on Ubuntu/Windows/macOS against
  Python 3.12 and 3.13.
- `scripts/check_no_print.py` — fails the build on any stdout write in server
  code, since a stray `print()` corrupts the JSON-RPC stream.
- `scripts/start_chrome_cdp.sh` — Linux/macOS counterpart to the PowerShell
  launcher.
- Agent skill documentation for every connector.
- Test suite grown from 66 to 221 offline tests, including real trimmed fixtures
  for the Yandex SSR parser.

### Fixed

- **`wb_search` returned pages where nothing had a price.** It resolved ids through
  `search-goods.wildberries.ru`, which serves a stale index: for one live query
  every id it returned was a delisted SKU with `price: null`, while the v9 search
  endpoint returned 100 in-stock products with real prices. `wb_search` now reads
  `search.wb.ru` v9 directly — one request instead of two, 100 results per page
  instead of 30 — and keeps the old path as a flagged fallback.
- **Ozon's process teardown was Windows-only.** `taskkill` paths, creation flags
  and the child environment allowlist assumed Windows; the POSIX branch was
  untested and its test asserted a Windows path, so it could not pass on Linux or
  macOS. Now cross-platform, with both branches unit-tested on every OS.
- **`taskkill` could be redirected through the environment.** The system directory
  was resolved via `SystemRoot`/`WINDIR`, which any process able to set the
  environment could point elsewhere. Now resolved via `GetSystemDirectoryW` or a
  literal fallback.
- **Windows paths were built with forward slashes off-Windows.** Switched to
  `PureWindowsPath` so the Windows branch composes correct paths when exercised
  from a POSIX host.
- **POSIX-only calls broke type checking and tests on Windows.** `terminate_process_tree`
  referenced `os.killpg`, `os.getpgid` and `signal.SIGKILL` literally. Those names do
  not exist on Windows, so mypy failed there while passing on Linux, and the POSIX
  tests could not monkeypatch attributes the module lacked. The calls now go through
  `kill_process_group()`, which resolves them via `getattr` and raises cleanly where
  process groups are unavailable; the tests patch that function instead. CI now runs
  `mypy --platform win32` and `--platform darwin`, which is what would have caught this
  from a Linux host in the first place.
- **PEP 561 markers were missing.** Without `py.typed`, mypy treated every
  cross-package import as `Any` and reported phantom missing-return errors. All
  packages now ship the marker; the tree is mypy-clean.
- **Error bodies were truncated unconditionally.** Detsky Mir's search route
  answers 404 while rendering a full page, so an error-body cap discarded real
  content. The cap is now opt-out per call.
- **Gateway errors were not retried.** Detsky Mir emits sporadic 502s and Yandex
  occasionally answers 302 with an empty body; both are now retried, while 429 is
  still passed straight through.

### Removed

- **`detmir_search` was implemented, tested against live data, and deleted.** Its
  results were plausible-looking nonsense: a query for "лего" returned nappies and
  collagen supplements, because Detsky Mir's API ignores text filters and its
  website search route renders a promo carousel behind a 404. No search tool is
  better than a confidently wrong one; discovery goes through `detmir_categories`.

### Not included, and why

Marketplaces evaluated during this release and deliberately left out:

- **Megamarket** — its mobile API works, but ServicePipe blocks datacenter traffic
  outright and requires cookies from a browser that has passed a JS challenge.
- **Lamoda** — its GraphQL endpoint returns prices for a *known* SKU, but catalog
  and search sit behind an anti-bot redirect loop, so there is no way to discover
  products in the first place.
- **DNS** — Qrator serves a JavaScript proof-of-work challenge on all dynamic
  pages; only `robots.txt` and `sitemap.xml` are reachable anonymously.
- **Citilink** — Qrator rate-blocks the entire domain, and the data transport is
  gRPC-web requiring a reversed protobuf schema.

Details in [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

[1.0.0]: https://github.com/Vladimir-Human/ru-marketplace-mcp/releases/tag/v1.0.0
