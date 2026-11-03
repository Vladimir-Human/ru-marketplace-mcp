# v2.8.1 — 2026-11-03

Исправлены 20 дефектов и расхождений v2.8.0: разбор цен Яндекса, счётчики Ozon, выбор сопоставимых предложений, схемы и гайды MCP, восстановление после ошибок CDP и проверки репозитория. Новые инструменты не добавлялись; внешние зависимости в lock-файле остались на прежних версиях.

## Исправления

**Цены и сравнение**

- Все денежные поля Яндекса читаются общим безопасным парсером. Неоднозначные строки вроде `1 999 2 999` дают `null` и `price_unparseable`, а обычные цены `3 983` и `1 999,50` сохраняются. Нечитаемая зачёркнутая цена также сопровождается предупреждением. Некорректная предпочтительная сумма любого типа не подменяется справочной ценой; диагностируется тип без раскрытия произвольного содержимого (#184, #185).
- Цена Plus больше не подставляется вместо неизвестной обычной цены. `compare_verify_offer` не подтверждает подписочную сумму как обычную; карточка с нечитаемой regular-ценой не называется карточкой только с Plus (#184, #185).
- Страница капчи и фатально испорченная выдача Яндекса не остаются в кэше: следующий вызов может получить восстановленную страницу. Явно пустая выдача остаётся здоровым пустым ответом и сохраняет обычный cache hit (#184).
- `Suitcase`, «стеклокерамика» и `unused` не считаются аксессуаром или б/у из-за подстроки. Комплектные фразы «зарядное устройство», «кабель USB-C», «чехол силиконовый» и определения после `with` / «в комплекте:» распознаются одинаково в заголовке и запросе. Разбор ограничен тремя известными соседними словами; другой аксессуар и состояние б/у сохраняют смысл. Исходные предложения и raw cheapest не меняются (#184, #186).
- Счётчики отзывов Ozon и сравнения читаются строго: `1 200` даёт 1200, а `1.2K`, диапазон или склеенные числа не превращаются в неверный счётчик. `ozon_search.count` равен числу возвращённых элементов; `parsed_count`, `truncated` и предупреждение показывают усечение (#185).

**Контракты агента**

- `wb_search` возвращает найденные товары и `no_results` в объявленной SDK-обёртке `result`; типизированные отказы остаются ошибками MCP. Гайды строят списки полей из моделей сериализации, включая реальные алиасы, обёртки и семантику проверок цены (#184).
- Размеры партий, положительность идентификаторов и закрытые перечисления видны в inputSchema. Инвентарь различает обязательный и возможный CDP и выбранный маршрут Wildberries. При усечении warnings последняя запись сообщает число подавленных предупреждений (#185).

**Транспорт и проверки**

- Raw-CDP корректно собирает IPv6 websocket URL и соблюдает абсолютный дедлайн отправки и ответа команды, даже при backpressure или потоке посторонних событий. Поля ошибок busy-handoff, включая `409` и `retry_after_s`, сохраняются через обработчики Taobao/Lamoda (#181).
- Диагностика в успешных ответах и doctor редактирует секреты; recovery-подсказки учитывают checkout, установленный пакет и `uvx`, не требуя отсутствующих в пакете локальных файлов (#181).
- Авария selfcheck, повреждённая под-проверка, неизвестный статус или источник дают doctor exit 4, а не неаварийный exit 2. Отказ бюджета остаётся `budget_refused`, не здоровой проверкой (#184).
- Wire-гейт отвергает нечисловые и бесконечные лимиты. Provenance-гейт проверяет повреждённые sidecars, отсутствующие pins и несвязанные пары; 13 датированных исключений сохраняются явно, а не выдаются за новые подтверждённые captures (#180).

## Как обновиться

1. Для checkout выполните `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапустите серверы. Нужен Python 3.12 или новее.
2. Для установки через uvx после выпуска обновите закреплённую запись: `uvx ru-marketplace-mcp@2.8.1 install <client> --write --force`. Команда должна соответствовать вашему клиенту; старые конфиги не меняются автоматически.
3. Если читаете сырой MCP `structuredContent` от `wb_search`, учитывайте обёртку `result` и актуальный `guide://wb/wb_search`. Для остальных инструментов используйте их `guide://`-ресурсы.
4. `null` при неоднозначной цене или счётчике означает, что значение не удалось достоверно прочитать. Не заменяйте его нулём и не считайте Plus/Wallet обычной ценой.

## Что проверено

- 3208 теста пакетов прошли, 2 пропущены, 5 исключены маркерами; 312 проверки скриптов прошли. Покрытие — 88,86% при пороге 80%.
- Все 16 серверов ответили в обоих вариантах MCP-протокола: 32/32 stdio-сессии в checkout и 32/32 после установки собранного wheel обычным pip в чистое окружение вне checkout.
- Собраны и проверены версии 16 wheel и 16 sdist, а также отдельного PyPI bundle (wheel + sdist). В каждой метаданные версии 2.8.1.
- Шесть прежних контрольных случаев через публичный MCP проходят на установленном wheel с FastMCP 4.1.6 и Pydantic 2.14.2; тот же набор на старом коде даёт 2 провала и 4 прохода. Ожидания не менялись.
- Предварительный doctor до финальной правки по ревью: exit 2, две healthy selfchecks и десять inconclusive, без drift и аварий проверок. HTTP selfchecks Яндекса и Детского мира прошли; браузер в этом прогоне не запускался.

## Для мейнтейнеров

Версия синхронизирована [штатным скриптом](https://github.com/Vladimir-Human/ru-marketplace-mcp/blob/main/scripts/bump_version.py) во всех 88 объявлениях. Lock-файл обновляется uv 0.13.6; версии внешних пакетов сохраняются. Перед тегом проходят проверки версий, пакетов, настоящих stdio-сессий, внешние регрессии R01–R20 и отдельный Image check обоих образов. Проверки регрессий используют сохранённые или синтетические ответы источников; MCP-клиент, адаптеры и ранжирование остаются настоящими.

## Состав

Состав артефактов этой версии: 16 wheel и 16 sdist для GitHub Release, отдельный bundle `ru-marketplace-mcp` для PyPI (один wheel и один sdist), stdio-образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.8.1` и запись в MCP Registry. HTTP-образ также собирается в предрелизном Image check без публикации.

## English

This patch release fixes 20 defects and contract mismatches left in v2.8.0: Yandex money parsing, Ozon counts, comparable-offer selection, MCP schemas and guides, CDP error recovery and repository gates. No tools were added; registry dependency versions in the lockfile are unchanged.

### Fixes

- Yandex money fields use the shared conservative parser. Ambiguous strings return `null` with `price_unparseable`; ordinary grouped/decimal amounts remain valid. Unreadable struck-through prices also produce a warning. Any present unusable preferred amount stops fallback to a reference price, with a safe type-only diagnostic for non-string payloads (#184, #185).
- Plus prices are not substituted for unknown everyday prices or certified as ordinary prices. An unreadable regular-price field is not mislabeled as a Plus-only card (#184, #185).
- Yandex challenge pages and fatal value-drift responses are evicted so the next call can recover; an explicit empty-result banner remains healthy and normally cached (#184).
- Comparison uses word/phrase boundaries rather than substrings and recognizes explicitly bundled noun phrases/modifiers in both titles and queries. Each occurrence allows at most three recognized adjacent words; independent accessories, used conditions, raw offers and raw cheapest are preserved (#184, #186).
- Ozon/comparison review counts reject ambiguous display forms. Returned count, parsed count, truncation flags and warnings agree with delivered items (#185).
- `wb_search` preserves its advertised `result` envelope, and guides derive serialized field lists from the actual models. Input schemas expose batch/enum bounds; WB routing metadata follows configuration; warning truncation is explicit (#184, #185).
- Raw CDP uses IPv6-safe netlocs and absolute command deadlines covering both outbound send and response. Busy-handoff status/delay survives error handlers; success diagnostics redact secrets and recovery hints follow installation mode (#181).
- Doctor rejects malformed supplied subchecks and distinguishes hard checker/usage failures (exit 4) from acknowledged blocks (exit 2) and drift (exit 1), without calling budget refusal healthy (#184).
- Wire budgets reject non-finite or invalid values. Provenance validates complete sidecar/fixture pairs while retaining 13 explicit dated exceptions (#180).

### Upgrading

Update a checkout, run `uv sync --all-packages --frozen`, and restart the servers. After publication, refresh a uvx-pinned client entry with `uvx ru-marketplace-mcp@2.8.1 install <client> --write --force`. Python 3.12+ is required. Raw `wb_search` structured output has the `result` envelope; consult its current guide. Unknown prices/counts remain `null`, never zero or an inferred everyday price.

### Validation

- Package tests: 3208 passed, 2 skipped, 5 deselected; infrastructure tests: 312 passed. Coverage: 88.86% against the 80% floor.
- All 16 servers completed both protocol eras: 32/32 real stdio sessions from the checkout and 32/32 after installing the built wheel with plain pip in a clean environment outside the checkout.
- Version metadata was checked in 16 wheels, 16 sdists and the separate bundle wheel/sdist: every artifact reports 2.8.1.
- Six existing public-MCP holdouts pass on the installed wheel with FastMCP 4.1.6/Pydantic 2.14.2; the unchanged oracle yields two failures and four passes on the old code.
- The preliminary doctor round before the final review fixes exited 2: two healthy selfchecks and ten inconclusive, with no drift or checker crashes. Yandex/Detsky Mir HTTP selfchecks passed; no browser was launched in that round.

### For maintainers

The shared bump updates all 88 declarations. Relock with uv 0.13.6 without upgrading registry dependencies. Version/artifact gates, real stdio sessions, the external R01–R20 regression gate and Image check run before tagging. Regression source replies are captured or synthetic; the MCP clients, adapters and ranking are real.

### Contents

Artifacts for this version: 16 wheels and 16 sdists on GitHub, one bundled wheel and sdist on PyPI, the versioned stdio OCI image and an MCP Registry entry. The HTTP image is also built by pre-tag Image check without publishing it.
