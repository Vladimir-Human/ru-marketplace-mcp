# v2.9.4 — 2026-11-16

Патч-релиз. Мегамаркет и Ситилинк сразу говорят, что сайт отказал и когда повторить; повторный вызов больше не получает из кэша забракованный ответ; `marketplace-mcp doctor` не называет источник здоровым, если поиск вернул товары без цен; ошибки от старой сборки подсказывают обновиться.

## Исправления

- **Мегамаркет** (#250, #253). Один поиск загружает сайт один раз, а не четыре. Если сайт отказал (HTTP 401, 403 или 429), инструмент сразу отвечает `rate_limited` и просит подождать 20 минут: на странице отказа нечего проходить, такой отказ снимается сам.
- **Ситилинк** (#249). На ответ 429 инструмент сразу отвечает `rate_limited` с повтором через 30 секунд, а не ждёт 45 секунд до таймаута.
- **Циан** (#247). Карточка объявления об аренде больше не отвечает «объявление удалено».
- **Кэш** (#257). Повторный вызов больше не получает из кэша ответ, который сам инструмент забраковал (например, сбой Avito с кодом 200 или карточку без названия). Следующий вызов снова идёт к источнику.
- **`marketplace-mcp doctor`** (#256, #263). Источник больше не считается здоровым, если поиск нашёл товары, но ни у одного нет цены в том виде, в каком её читает сам поиск. Причина в отчёте: `no_prices`. Так проверяются все источники с проверкой поиска, включая Wildberries.
- **Avito** (#264). Карточка без артикула на странице принимается, только если адрес объявления на странице содержит запрошенный номер; иначе ответ `parser_drift`, а не чужое объявление. Ссылки с не-ASCII символами, точками или `%` в пути объявления отклоняются до открытия браузера.
- **Настройки таймаута** (#264). `CITILINK_TIMEOUT`, `DNS_TIMEOUT` и `TAOBAO_TIMEOUT` теперь действительно ограничивают чтение страницы: от 5 до 45 секунд, по умолчанию 30, как и было на деле.
- **Запуск Chrome** (#240). Если Chrome запустился, но не открыл порт отладки, `marketplace-mcp browser start` говорит, что случилось: профиль занят другим Chrome, Chrome завершился с кодом или ещё запускается.

## Новое

- **Старая сборка** (#258). Ошибки `parser_drift`, `transport_down`, `challenge_required` и `rate_limited` от сборки старше 30 дней содержат поле `old_build`: версию, дату выпуска, возраст и команду обновления. `marketplace-mcp doctor` пишет то же первой строкой. В сеть за этим сервер не ходит.
- **Безопасность и границы** (#242). В README и в руководстве по сравнению цен появился короткий блок: не подключайте сервер в одну сессию с инструментами, которые читают личные данные и могут отправить их наружу без подтверждения, и держите отдельный профиль Chrome для парсинга.

## Изменения

- Описание параметра `sources` у `compare_prices` перечисляет все площадки сравнения, а описания `old_price_rub` и `discount_percent` уточняют, что это цена в том виде, в каком её показывает площадка (#238).
- Подсказки для агентов (skills) перечисляют четыре инструмента сравнения, которые DeepSeek Harness подключает по умолчанию; подсказка Ozon объясняет, что рейтинг и отзывы общие для всех вариантов карточки (#264).
- Удалена настройка `DETMIR_WALL_TIMEOUT`: сервер её никогда не применял. Если она осталась в окружении или `.env`, она просто игнорируется (#264).

## Известные ограничения

- Мегамаркет, Ситилинк, Lamoda и Циан в день выпуска отказывали браузеру сборщика или просили пройти проверку. Их инструменты отвечают `rate_limited`, `challenge_required` или `transport_down`; что это значит и что делать, написано в самой ошибке. Полный список исправлений по этим площадкам — в `CHANGELOG.md`.

## Как обновиться

- Новых обязательных настроек нет.
- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.
- Через `uvx`: `uvx ru-marketplace-mcp@2.9.4 install <client> --write --force`.

## Состав

16 wheel и 16 sdist на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.4` и запись в MCP Registry.

---

## English

Patch release. Megamarket and Citilink say at once that the site refused and when to retry; a repeated call no longer gets a rejected answer from the cache; `marketplace-mcp doctor` no longer calls a source healthy when its search returns items without prices; errors from an old build suggest an upgrade.

### Fixes

- **Megamarket** (#250, #253). One search loads the site once, not four times. When the site refuses (HTTP 401, 403 or 429), the tool answers `rate_limited` at once and asks to wait 20 minutes: the refusal page has nothing to pass and lifts by itself.
- **Citilink** (#249). A 429 answers `rate_limited` at once with a 30-second retry instead of waiting 45 seconds for a timeout.
- **Cian** (#247). A rental offer's card no longer answers "removed".
- **Cache** (#257). A repeated call no longer gets from the cache an answer the tool itself rejected (for example an Avito failure with status 200, or a card without a title). The next call goes to the source again.
- **`marketplace-mcp doctor`** (#256, #263). A source is no longer healthy when its search found items but none has a price as the search itself reads it. The report names the reason `no_prices`. Every source with a search check works this way, Wildberries included.
- **Avito** (#264). A card whose page carries no item SKU is accepted only when the ad's address on that page holds the requested id; otherwise the answer is `parser_drift`, not another ad. Links with non-ASCII characters, dots or `%` in the ad path are refused before the browser opens.
- **Timeout settings** (#264). `CITILINK_TIMEOUT`, `DNS_TIMEOUT` and `TAOBAO_TIMEOUT` now really bound the page read: 5 to 45 seconds, default 30, as it effectively was.
- **Chrome start** (#240). When Chrome starts but does not open its debugging port, `marketplace-mcp browser start` says what happened: the profile is held by another Chrome, Chrome exited with a code, or it is still starting.

### New

- **Old build** (#258). `parser_drift`, `transport_down`, `challenge_required` and `rate_limited` errors from a build older than 30 days carry an `old_build` field: version, release date, age and the upgrade command. `marketplace-mcp doctor` prints the same as its first line. The server makes no network call for this.
- **Safety and limits** (#242). README and the price-comparison guide gain a short block: do not connect the server in one session with tools that read personal data and can send it out without confirmation, and keep a separate Chrome profile for scraping.

### Changes

- The `sources` parameter of `compare_prices` lists every comparison source, and `old_price_rub` and `discount_percent` say the price is as the marketplace shows it (#238).
- The agent guides (skills) list the four comparison tools DeepSeek Harness mounts by default; the Ozon guide explains that the rating and reviews are shared by all variants of a card (#264).
- The `DETMIR_WALL_TIMEOUT` setting is removed: the server never applied it. If it is still in the environment or `.env`, it is simply ignored (#264).

### Known limits

- On release day Megamarket, Citilink, Lamoda and Cian refused the scraping browser or asked for a check. Their tools answer `rate_limited`, `challenge_required` or `transport_down`; the error itself says what it means and what to do. The full list of fixes for these sources is in `CHANGELOG.md`.

### Upgrading

- No new required settings.
- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.
- `uvx`: `uvx ru-marketplace-mcp@2.9.4 install <client> --write --force`.

### Contents

16 wheels and 16 sdists on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.4`, and the MCP Registry entry.
