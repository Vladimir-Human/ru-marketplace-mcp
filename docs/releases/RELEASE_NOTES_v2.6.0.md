# v2.6.0 — 2026-10-30

Минорный релиз: серверы перешли на FastMCP 4, а поиск Taobao исправляет три замечания из v2.5.4. Для существующих MCP-клиентов конфигурация, имена инструментов, параметры и поля ответов не меняются. Операторам нужно обновить зависимости; ограничение сохранённых вкладок по HTTP без сессий описано ниже.

## Изменения

- **FastMCP 4.** Все пакеты используют FastMCP 4.1.4 (MCP Python SDK v2) вместо 3.x, минимальная версия pydantic — 2.12. Поддерживаются оба протокола: прежний с рукопожатием (`initialize`, 2025-11-25 и старше) и новый без сессий (`server/discover`, 2026-07-28). Публичный контракт инструментов не изменился: блок `_meta` остаётся `_meta`, а `compare_verify_offer` по-прежнему вкладывает данные карточки под `meta`. Клиентам с прежним рукопожатием ничего менять не нужно (#138).
- **Передача браузера и снимки.** В протоколе без сессий каждый запрос получает новый `session_id`. По stdio сохранённая вкладка теперь привязана к процессу сервера, который обслуживает одного клиента: повтор запроса к тому же процессу продолжает чтение той же вкладки. По HTTP без сессий запросы нельзя связать с одним клиентом, поэтому `challenge_required` возвращается без сохранения вкладки. У клиентов с рукопожатием остаётся прежняя привязка к MCP-сессии. `compare_browser_snapshot` читает объявленные клиентом возможности, включая поддержку изображений, в обоих протоколах (#138).
- **Taobao: пробел в запросе.** Пробел отправляется как `%20`, а не `+`: площадка ищет «iphone 15», а не буквальное «iphone+15». Настоящий плюс в запросе остаётся `%2B`. Один канонический URL используется для поиска, кэша и передачи браузера (#141).
- **Taobao: товары Tmall.** Поиск больше не пропускает плитки с `detail.tmall.com`: возвращает настоящий хост, id, название, цену в юанях и магазин. Рекламные трекеры `click.simba.taobao.com` намеренно остаются за пределами выдачи: переход оплачивает рекламодатель и это не ссылка на товар. `taobao_card` явно отказывает для Tmall с невосстановимым `bad_request` и объясняет, что основные данные уже есть в поисковой выдаче. Поддержка карточек Tmall не добавлена, политика разрешённых хостов не расширена (#141).
- **Taobao: сетка ещё грузится.** Маркер «加载中» передаётся как отдельный признак загрузки. Пустая загружающаяся сетка перечитывается в той же вкладке около 10 с; если она так и не готова, возвращается повторяемый `timeout`, а не `parser_drift`. В `compare_prices` причина отказа Taobao тоже становится `timeout`, результаты других источников сохраняются. Вход и капча не ждут и по-прежнему дают `challenge_required`; пустая сетка без маркера загрузки остаётся `parser_drift` (#141).

Эти три исправления Taobao закрывают [три пункта «в этом релизе не исправлено» в заметках v2.5.4](RELEASE_NOTES_v2.5.4.md#статус-живой-проверки): буквальный плюс, пропущенные плитки и загрузку, ошибочно названную дрейфом. Исключение рекламных трекеров остаётся намеренным; это не обещание вернуть каждую плитку страницы.

## Безопасность

- Dependabot уже следит за базовыми образами Docker, закреплёнными по digest (#134, включено в v2.5.4), и подхватывает пересобранные образы.
- Теперь он следит и за npm lock-файлом для разработки (`package-lock.json`, jsdom для офлайн-тестов DOM-экстракторов): проверка еженедельная, minor- и patch-обновления сгруппированы (#140). Это расширение автоматических проверок, а не результат нового аудита безопасности; аудит зависимостей для этого релиза не запускался.

## Обновление

- Python остаётся 3.12+. Зависимости теперь требуют `fastmcp>=4.1.4,<5`, `pydantic>=2.12`; общий runtime также требует `mcp-types>=2.3,<3`. Не оставляйте FastMCP 3.x в окружении и устанавливайте коннекторы вместе с `mcp-core` версии 2.6.0. Оператор обновляет lock-файл, выполняет frozen-установку, перезапускает серверы и пересобирает оба образа.
- Конфигурация MCP-клиентов и переменные окружения прежние. Сохранение вкладок по-прежнему включается явно через `CHROME_CHALLENGE_HANDOFF_S`; для HTTP-клиента без сессий оно недоступно. Порядок повторного запроса и ограничения — в [настройке Chrome](../CDP_SETUP.md#optional-challenge-handoff).
- `scripts/e2e_stdio_check.py` теперь проверяет оба протокола: ожидается 32 успешные stdio-сессии на 2.6.0 вместо 16. На 2.6.0 получено 32 из 32.

## Статус живой проверки

30 октября, одна машина и один адрес, Chrome оператора через CDP. Проверялся код коммита 99b7212 (main после #141): он совпадает с релизом во всём, кроме номеров версий. Живых запросов к площадкам было около 45 при лимите 40 из правил проекта; перепроверку после капч и два последних запроса к Taobao владелец разрешил.

| Источник | selfcheck | Примечание |
| --- | --- | --- |
| Ozon | поиск, карточка, отзывы, отзывы с сортировкой — healthy | |
| Яндекс Маркет, Детский мир, Ситилинк, AliExpress, Циан | success | |
| Taobao | поиск — healthy | исправления #141 проверены отдельно, см. ниже |
| Avito, Мегамаркет, Lamoda, DNS | сначала `inconclusive`: защита от ботов, у Мегамаркета HTTP 403 | оператор прошёл капчи в том же Chrome; при повторном прогоне все четыре — success |
| Wildberries | API `card.wb.ru` и `search.wb.ru` с этого адреса — `transport_down`; отзывы, search-goods и basket — healthy | с `WB_TRANSPORT=dom` поиск «iphone 15» вернул 40 товаров с ценами, карточка nm 1642036955 — 37 885 ₽, в наличии |
| MPStats | не проверен: нет токена (`auth_missing`) | |

**Taobao после #141.**

- `taobao_search` по запросу «iphone 15» ушёл как `q=iphone%2015` и вернул 37 товаров, у всех есть цена в юанях. 7 из них с `detail.tmall.com`, тоже с ценой и магазином (например, 424 ¥, «天猫国际全球探物»).
- `taobao_card` на ссылку Tmall сразу вернул `bad_request` с `retryable: false`, не переходя на страницу.
- `compare_prices` по запросу «iphone 15» на Taobao и Ситилинке: `complete: true`, оба источника ответили, по 20 предложений. Предложения Taobao в юанях не попали в рублёвое ранжирование, в `warnings` есть `foreign_currency`. Сетка с «加载中» в этом прогоне не встретилась, этот случай покрыт офлайн-тестами. В прогоне за 18 минут до этого Ситилинк не ответил; причина не записана.

**Сверка глазами.** Цены Taobao прочитаны из той же отрисованной страницы поиска, что была открыта у оператора: экстрактор нашёл цену у 38 плиток из 38. Отдельной сверки каждой цифры с сайтом не было.

**Решение:** go. Не проверены MPStats (нет токена) и API Wildberries с этого адреса; чтение WB через Chrome работает.

## Проверка поставки

- Офлайн: 2317 passed, 1 skipped, 5 live deselected.
- Ruff, форматирование, mypy, версия 2.6.0 в 84 местах, защита stdout, привязки фикстур (31), счётчик тестов (2318 в 7 местах).
- 32/32 настоящих stdio MCP-сессий на v2.6.0: каждый сервер в обоих протоколах.
- Lock-файл и `uv sync --frozen` проверены uv 0.13.6, той же версией, что в сборочном образе.
- Образы Docker: <!-- OPERATOR: image check -->

Поставка содержит 16 wheels и 16 sdists; на PyPI пакеты не публикуются.

## English

v2.6.0 is a minor release. Every package moves to FastMCP 4.1.4 (MCP Python SDK v2), with pydantic's floor at 2.12 (#138). Both the handshake protocol (`initialize`, 2025-11-25 and older) and the sessionless 2026-07-28 protocol (`server/discover`) are supported. Existing MCP clients need no configuration or tool-contract changes: names, arguments and response fields stay the same, including `_meta` and the nested `meta` in `compare_verify_offer`. Browser handoff on sessionless stdio belongs to the single client's server process; retry against that same process. Sessionless HTTP has no stable client identity and reports the challenge without retaining the tab. Handshake clients keep per-session handoff. `compare_browser_snapshot` reads declared client capabilities in both protocols.

Taobao (#141): spaces are encoded as `%20`, not a literal `+`; a real plus remains `%2B`. Search includes ordinary Tmall tiles with their real URLs, ids, titles, CNY prices and shops. Ad-click trackers stay out deliberately because visiting them bills the advertiser. `taobao_card` clearly refuses Tmall with a non-retryable `bad_request`; Tmall card support and a wider host policy are not part of this release. A loading grid is re-read in the same tab for about 10 seconds and, if it never finishes, becomes a retryable `timeout`, including in `compare_prices`, not `parser_drift`. Login and CAPTCHA walls still immediately return `challenge_required`; a genuinely empty non-loading grid remains `parser_drift`. These address the [three outstanding Taobao observations in v2.5.4](RELEASE_NOTES_v2.5.4.md#статус-живой-проверки), not a promise to include every advertising tile.

Dependabot already watches digest-pinned Docker base images (#134, shipped in v2.5.4); it now also watches the dev-only npm lockfile weekly, grouping minor and patch updates (#140). No new dependency audit was run for this release. Operators must update the dependency environment (`fastmcp>=4.1.4,<5`, `pydantic>=2.12`, and `mcp-types>=2.3,<3` in the shared runtime), keep all connectors and `mcp-core` at 2.6.0, relock, perform a frozen installation, restart servers and rebuild both images. Python stays at 3.12+; client configuration and environment variables are unchanged.

Live, 30 October, one address, the operator's Chrome over CDP, on commit 99b7212, which matches the release except for version strings. About 45 live requests were made against the project's limit of 40; the recheck after the captchas and the last two Taobao requests had the owner's consent. Ozon (all four checks), Yandex Market, Detsky Mir, Taobao, Citilink, AliExpress and Cian passed selfcheck. Avito, Megamarket, Lamoda and DNS were first stopped by anti-bot checks; after the operator passed the captchas in the same Chrome, all four passed. Wildberries' API hosts refused this address (`transport_down`), while `WB_TRANSPORT=dom` returned 40 priced search results and a card at 37,885 ₽. MPStats was not checked (no token). Taobao after #141: the query went out as `q=iphone%2015`; 37 results, all priced in yuan, 7 of them Tmall with price and shop; `taobao_card` refused a Tmall URL with `bad_request` before navigating; `compare_prices` with Citilink was complete, with the yuan offers kept out of the ruble ranking. A loading grid did not occur live and is covered offline. Decision: go.

Offline: 2317 passed, 1 skipped; ruff, mypy, 84 version declarations, 31 fixture pins, test count 2318; 32/32 real stdio sessions on v2.6.0; lock and frozen sync checked with uv 0.13.6. Docker images: <!-- OPERATOR: image check -->
