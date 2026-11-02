# v2.8.0 — 2026-11-01

Ответы серверов стали честнее: `meta.healthy` везде значит одно и то же, предупреждения показывают поломку вёрстки и цен, а условные цены и товар с другим объёмом памяти не выигрывают сравнение. Справочник форматов переехал в MCP-ресурсы, поэтому запросы тратят меньше токенов. Появился инструмент `marketplace_health`. Если площадка на паузе или просит пройти проверку, агент узнаёт, когда повторить запрос и что сделать человеку, а секреты в ошибках и логах скрываются сами.

## Что нового

**Честность ответов и структура выдачи**

- `meta.healthy` во всех источниках равен `true`, только когда ответ полный и данным можно верить, и `false` при любом предупреждении о вёрстке, валидации или неполноте. Заметки с префиксом `info:` (например, что регион Wildberries взят из браузера, а не из `dest`) флаг не сбрасывают (#170, #177).
- Поиск и карточки девяти площадок (`avito`, `cian`, `citilink`, `detmir`, `dns`, `lamoda`, `megamarket`, `taobao`, `yandex`) теперь, как `wb` и `ozon`, проверяют цены. Если цена не разобралась хотя бы у половины выдачи, в `meta.warnings` добавляется `price_unparseable` или `price_blank`, а совсем без цен — `no_prices_on_page` (#170).
- Одиннадцать источников (`avito`, `lamoda`, `taobao`, `citilink`, `dns`, `aliexpress`, `cian`, `detmir`, `megamarket`, `ozon`, `wb`) сверяют ответ площадки с полями, которые читает парсер. Если пропала целая группа полей, в `meta.warnings` добавляется `shape_drift: <источник>: missing <группа>`, а `healthy` становится `false` (#162, #166).

**Сравнение цен**

- `compare_prices` пишет тип цены в `price_kind`, и цена с WB Кошельком или по подписке не попадает в `cheapest` и `cheapest_comparable`. Без обычной цены `price_rub` равен `null` (сумма в `price_native`), а `compare_verify_offer` для Кошелька (`price_kind="wallet"`) возвращает `null` в полях проверки (#159, #177).
- `compare_prices` сверяет объём памяти из запроса (`128GB`, `128 ГБ`, `1 ТБ`) с заголовком товара: чужой объём не попадёт в `cheapest_comparable` и даст предупреждение `identity_mismatch`. «1 ТБ» и «1024 ГБ» считаются одним размером, а «1000 ГБ» с «1 ТБ» не совпадает (#161, #164).
- В `compare_prices` пустой ответ источника считается `ok`, только когда сам источник не сообщил о проблеме; иначе это `unhealthy` (`unhealthy_empty`, `complete=false`). Итог по каждому источнику теперь говорит, когда повторить и что сделать человеку: `retry_after_s`, `retryable_now`, `retry_when` и `user_action` (#170, #177).

**Состояние транспорта, паузы площадок и проверки**

- Новый инструмент `marketplace_health` только читает состояние: отвечает ли Chrome (версия и число вкладок), какие площадки на паузе и сколько секунд ждать, какие вкладки с проверкой удержаны и где разблокировать источник. В `marketplace_sources` появилось поле `needs_cdp` (#173).
- Когда площадка на паузе после отказов в Chrome, агент получает `rate_limited` (`retryable=true`) с остатком ожидания в `retry_after_s` и хостом в `provider`. Раньше Avito, Cian, Ozon и Мегамаркет превращали эту паузу в `transport_down` без времени ожидания или вовсе теряли её (#168).
- Ошибка `challenge_required` при проверке (капче или входе) теперь содержит `retryable_now=false`, `retry_when=after_user_confirmation` и `user_action` с командой `marketplace-mcp unblock <source>` или удержанной вкладкой. Taobao, Lamoda и Wildberries не обещают открытую вкладку, если `CHROME_CHALLENGE_HANDOFF_S` её не держит (#160).

**Справочник в MCP-ресурсах и экономия токенов**

- Секции `## Return Format` и `## Error Format` переехали из `tools/list` в ресурсы `guide://<источник>/<инструмент>`, а список ошибок — в `marketplace://errors`. В описании инструмента остались назначение, главные правила одной строкой и ссылка на гайд (#167, #177).
- Сервер `marketplace-mcp` не подключает инструменты `mpstats_*`, пока не задана непустая переменная `MPSTATS_MP_AUTH`. Это экономит около 971 токена (примерно 6 % профиля) на каждый запрос. Источник попадает в `skipped`, а отдельный `mpstats-mcp` по-прежнему отвечает `auth_missing` (#163).

**Безопасность и защита браузера**

- Секреты в текстах и полях ошибок и логов вычищаются сами на обоих транспортах, включая значения `MPSTATS_MP_AUTH` и `MCP_HTTP_AUTH_TOKEN` от 6 символов. Даже неожиданное исключение, например из-за опечатки в адресе прокси, не покажет агенту пароль, а Ozon больше не отдаёт сырой ответ сервера в `ozon_reviews` и отладочный лог (#169, #176).
- `avito_card` открывает ссылки в залогиненном Chrome пользователя, поэтому принимает полный URL только с путём объявления без query и фрагмента. Служебные разделы (`/profile`, `/account`, `/orders`, `/favorites`, `/api/...`), хвосты после ID и пути с `\` или `%` отклоняются как `bad_request` (#160, #176).

## Исправления

- Неоднозначный текст в ценах, счётчиках и рейтингах теперь даёт `null`: склеенные цены (`1 999 2 999 ₽`), рассрочка (`от 5 751 ₽/ мес.`), множители (`15 ТЫС. ₽`, `12万`) и число отзывов вместо рейтинга (`1 299`). Обычные цены и рейтинг `4,9` работают как раньше (#157, #171, #177, #178).
- Ошибки AliExpress и чтения страниц через Chrome приходят как JSON с кодом ошибки, а не голым текстом. Проверка `x5sec` на AliExpress отдаёт `challenge_required` вместо `transport_down`, а не-JSON ответ со страницы — `parser_drift` (#176).
- Пустые ответы при сбое дают `healthy=false`: поиск Wildberries через сайт на второй и следующих страницах без плашки «ничего не нашлось» получает `page_end_unverified`, карточка без единого разобранного товара — `no_items`, а сбой запасного пути API — `fallback_failed`. Мегамаркет помечает поиск без цен кодом `no_prices_on_page`, а карточку — `no_price` (#170, #177).
- Убраны ложные предупреждения `shape_drift` на страницах Wildberries со снятыми с продажи товарами (`"sizes": []`) и в выдаче Avito через конверт `data.items[]`. В Мегамаркете сбой привязки адреса во время паузы площадки больше не отключает адрес доставки до перезапуска (#168, #175).
- Запрос из очереди к площадке, ушедшей на паузу, сразу получает отказ, а ожидание занятого хоста не держит общий слот браузера (#156). Неудачный автозапуск Chrome запоминается на 45 секунд (сбрасывается при `browser start`), чтобы соседние вызовы не ждали таймаут заново (#160).
- В `detmir_card` первый сетевой запрос теперь возвращает `cached=false`, а повторный из кеша — `cached=true` (#160). В `server.json` добавлена пропущенная запись `cian` (#172), а в `instructions` девяти серверов возвращён пропущенный пробел (#177).

## Как обновиться

1. Обновите checkout, выполните `uv sync --all-packages --frozen` и перезапустите серверы. Запись, созданная через `uvx`, закреплена на версии пакета: обновите её командой `uvx ru-marketplace-mcp@2.8.0 install <client> --write --force`.
2. Если промпт или агент читал таблицы полей из `tools/list`, переключите его на ресурсы `guide://<источник>/<инструмент>` и `marketplace://errors` (#167).
3. Чтобы в `marketplace-mcp` появились инструменты `mpstats_*`, задайте непустую переменную `MPSTATS_MP_AUTH` (#163).
4. Учитывайте, что предупреждения в `meta.warnings` (кроме `info:`) ставят `meta.healthy=false`, а неоднозначные цены, счётчики и рейтинги возвращают `null` (#157, #162, #166, #170, #171, #177, #178).
5. В `compare_prices` и `compare_verify_offer` условные цены (`price_kind`) и чужие объёмы памяти (`identity_mismatch`) больше не выигрывают сравнение (#159, #161, #164, #177).
6. Обновите обработку ошибок: пауза площадки приходит как `rate_limited` с `retry_after_s`, проверка `x5sec` на AliExpress — как `challenge_required` с `user_action`, а не-товарные URL в `avito_card` — как `bad_request` (#160, #168, #176, #177).

## Для мейнтейнеров

Все метаданные площадок теперь хранятся в одном реестре источников, а сборка коннекторов вынесена в общий набор (`dns` и `citilink` делят одну реализацию) (#172). Гейт стоимости схем в `scripts/mcp_wire.py` считает не только `wire_tokens`, но и `instructions_tokens` и `model_tokens`, а также держит потолки профилей `ceilings` в `work/performance/wire-baseline.json` (#167, #174). Публикация на PyPI включена, установка без клонирования репозитория идёт через `uvx ru-marketplace-mcp setup` (#155).

## Состав

16 wheels и 16 sdists на этой странице, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.8.0`, запись в MCP Registry и пакет [`ru-marketplace-mcp` на PyPI](https://pypi.org/project/ru-marketplace-mcp/2.8.0/) (один wheel и один sdist со всеми серверами).

## English

Server answers are more honest in this release: `meta.healthy` means the same thing everywhere, warnings report layout changes and missing prices, and conditional prices or a different storage size never win a comparison. The format reference moved into MCP resources, so requests cost fewer tokens. A new `marketplace_health` tool shows browser and host state. When a site is paused or asks for a check, the agent learns when to retry and what a person must do, and secrets in errors and logs are scrubbed automatically.

### What's new

**Honest answers and response shape checks**

- `meta.healthy` is `true` across all sources only when the response is complete and trustworthy, and `false` on any drift, validation, or completeness warning. Notes prefixed with `info:` (such as Wildberries noting a browser-selected region instead of `dest`) do not flip the flag (#170, #177).
- Search and card tools across nine sources (`avito`, `cian`, `citilink`, `detmir`, `dns`, `lamoda`, `megamarket`, `taobao`, `yandex`) now validate prices like `wb` and `ozon`. When prices fail to parse on at least half the page, `meta.warnings` adds `price_unparseable` or `price_blank`, or `no_prices_on_page` when no prices exist (#170).
- Eleven sources (`avito`, `lamoda`, `taobao`, `citilink`, `dns`, `aliexpress`, `cian`, `detmir`, `megamarket`, `ozon`, `wb`) check the site's response against the fields their parser reads. If a whole group of fields disappears, `shape_drift: <source>: missing <group>` is added to `meta.warnings` and `healthy` becomes `false` (#162, #166).

**Price comparison**

- `compare_prices` records the price type in `price_kind`, and a WB Wallet or subscription price never becomes `cheapest` or `cheapest_comparable`. Without a regular price, `price_rub` stays `null` (kept in `price_native`), and `compare_verify_offer` returns `null` check fields for WB Wallet prices (`price_kind="wallet"`) (#159, #177).
- `compare_prices` checks storage sizes in the query (`128GB`, `128 ГБ`, `1 ТБ`) against offer titles: a different size cannot become `cheapest_comparable` and adds an `identity_mismatch` warning. "1 TB" and "1024 GB" match as one size, while "1000 GB" does not (#161, #164).
- In `compare_prices`, an empty source response counts as `ok` only when the source itself reported no problem; otherwise its status is `unhealthy` (`unhealthy_empty`, `complete=false`). Each per-source outcome now says when to retry and what a person must do: `retry_after_s`, `retryable_now`, `retry_when`, and `user_action` (#170, #177).

**Transport health, site pauses, and checks**

- The new `marketplace_health` tool only reads state: whether Chrome answers (version and tab count), which sites are paused and for how many seconds, which check tabs are kept, and which page unblocks each source. `marketplace_sources` gained the `needs_cdp` field (#173).
- When a site is paused after repeated refusals in Chrome, the agent receives `rate_limited` (`retryable=true`) with the remaining wait in `retry_after_s` and the host in `provider`. Previously, Avito, Cian, Ozon, and Megamarket turned it into `transport_down` without a timer or dropped it (#168).
- The `challenge_required` error on a check (CAPTCHA or login) now includes `retryable_now=false`, `retry_when=after_user_confirmation`, and `user_action` naming `marketplace-mcp unblock <source>` or the kept tab. Taobao, Lamoda, and Wildberries no longer promise an open tab when `CHROME_CHALLENGE_HANDOFF_S` keeps none (#160).

**Tool reference in MCP resources and lower token cost**

- The `## Return Format` and `## Error Format` sections moved from `tools/list` into `guide://<source>/<tool>` resources, and the shared error list into `marketplace://errors`. Tool descriptions keep their purpose, the key rules in one line, and the guide URI (#167, #177).
- The `marketplace-mcp` server no longer mounts `mpstats_*` tools unless a non-empty `MPSTATS_MP_AUTH` is set, saving about 971 tokens (~6% of the profile) per request. The source is listed in `skipped`, while standalone `mpstats-mcp` still mounts them and returns `auth_missing` (#163).

**Security and browser safety**

- Secrets in error and log messages and fields are scrubbed automatically on both transports, including exact `MPSTATS_MP_AUTH` and `MCP_HTTP_AUTH_TOKEN` values (6+ characters). Even an unexpected exception, such as a typo in a proxy URL, no longer shows the agent a password, and Ozon no longer passes raw server text into `ozon_reviews` or the debug log (#169, #176).
- Because `avito_card` uses the user's logged-in Chrome, it accepts full URLs only with an item-page path and opens them without query or fragment. Service routes (`/profile`, `/account`, `/orders`, `/favorites`, `/api/...`), extra segments past the ID, and paths with `\` or `%` return `bad_request` (#160, #176).

### Fixes

- Ambiguous prices, counts, and ratings now return `null`: glued prices (`1 999 2 999 ₽`), instalments (`от 5 751 ₽/ мес.`), magnitude words (`15 ТЫС. ₽`, `12万`), and review counts in rating fields (`1 299`). Ordinary prices and `4,9` work as before (#157, #171, #177, #178).
- AliExpress errors and Chrome page reads now come back as JSON with an error code instead of plain text. The AliExpress `x5sec` check now returns `challenge_required` instead of `transport_down`, and non-JSON page output returns `parser_drift` (#176).
- Empty responses after a failure set `healthy=false`: Wildberries site search on page 2 or later without the "nothing found" notice gets `page_end_unverified`, a card where no item parsed gets `no_items`, and a failed API fallback sets `fallback_failed`. Megamarket flags price-free searches with `no_prices_on_page` and cards with `no_price` (#170, #177).
- False `shape_drift` warnings no longer fire on Wildberries pages where all items are delisted (`"sizes": []`) or on Avito responses using the `data.items[]` envelope. On Megamarket, an address-lookup failure during a site pause no longer disables the delivery address until restart (#168, #175).
- Queued requests to a paused site are now refused immediately, and waiting for a busy host no longer holds a global browser slot (#156). Failed Chrome autostarts are remembered for 45 seconds (cleared by `browser start`) so parallel calls do not repeat the timeout (#160).
- In `detmir_card`, the first network fetch now returns `cached=false` and a cached replay returns `cached=true` (#160). `server.json` gained the missing `cian` entry (#172), and a missing space in `instructions` was restored across nine servers (#177).

### Upgrading

1. Update the checkout, run `uv sync --all-packages --frozen` and restart the servers. An entry written from `uvx` is pinned to a package version: refresh it with `uvx ru-marketplace-mcp@2.8.0 install <client> --write --force`.
2. If your prompt or agent read field tables from `tools/list`, read the `guide://<source>/<tool>` and `marketplace://errors` resources instead (#167).
3. Set a non-empty `MPSTATS_MP_AUTH` if you want `mpstats_*` tools mounted on `marketplace-mcp` (#163).
4. Expect `meta.healthy=false` on any data warning in `meta.warnings` (except `info:`), and `null` for ambiguous prices, counts, and ratings (#157, #162, #166, #170, #171, #177, #178).
5. Conditional prices (`price_kind`) and mismatched storage sizes (`identity_mismatch`) no longer win in `compare_prices` or `compare_verify_offer` (#159, #161, #164, #177).
6. Update agent error handling for `rate_limited` site pauses (`retry_after_s`), `challenge_required` on AliExpress `x5sec` (`user_action`), and `bad_request` on non-item `avito_card` URLs (#160, #168, #176, #177).

### For maintainers

All marketplace metadata now lives in one source registry, and the common connector code moved into one kit (`dns` and `citilink` now use one implementation) (#172). The wire gate in `scripts/mcp_wire.py` also counts `instructions_tokens` and `model_tokens` alongside `wire_tokens` and has per-profile `ceilings` in `work/performance/wire-baseline.json` (#167, #174). PyPI publishing is on, and checkout-free installation runs via `uvx ru-marketplace-mcp setup` (#155).

### Contents

16 wheels and 16 sdists on this page, the image `ghcr.io/vladimir-human/ru-marketplace-mcp:2.8.0`, an MCP Registry entry and the [`ru-marketplace-mcp` package on PyPI](https://pypi.org/project/ru-marketplace-mcp/2.8.0/) (one wheel and one sdist with every server).
