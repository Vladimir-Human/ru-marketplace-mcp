# v2.5.1 — 2026-10-12

Патч: три источника, которые молча отдавали пустые или чужие данные, снова читают правду. Схемы параметров инструментов не изменились; в ответах появились только заполненные значения там, где раньше было пусто.

## Изменения

- **Яндекс Маркет, карточки** (#105, PR #116): Яндекс перенёс страницы товаров на `/card/{slug}/{id}`, а старый `/product/{id}` отдаёт пустую рамку. Из-за этого каждая `yandex_card` и проверка предложения Яндекса в сравнении отвечала `empty_product_shell`, хотя товар был в продаже. Карточка читается по новому адресу; запасные ссылки строятся так же.
- **Lamoda, карточки** (PR #118): антибот-петля 307 закрыла GraphQL и для обычного HTTPS. Карточка повторяет тот же запрос изнутри страницы lamoda.ru в Chrome оператора (`tier_used: graphql_cdp`). Если Lamoda показывает проверку, ответ сразу `challenge_required`, а не таймаут через 30 с.
- **Авито, карточки** (#110, PR #119): `/web/1/js/items` перестал учитывать `itemId` и отдаёт общий каталог; карточка возвращала `success` со всеми пустыми полями. Теперь она читает schema.org Product со страницы объявления: название, цена в рублях, описание, фото, ссылка. Снятое объявление — `not_found`, страница без Product — `parser_drift`.
- **Авито, продавцы** (PR #120): тот же API не учитывает и `userId` — `avito_seller` отдавал «1500 активных объявлений» про любого продавца. Теперь он читает страницу профиля: имя, оценку, число отзывов; `active_items` — `null`, потому что страница общего числа не показывает. Карточка возвращает продавца с id, который принимает `avito_seller`.
- **Самопроверка Авито** проходит цепочку поиск → карточка → продавец. Раньше она не проверяла карточку вовсе — поэтому пустые карточки и не были замечены.
- **GRACE** (PR #117): `.grace/` с требованиями, графом модулей и картой проверок, контракты в начале каждого `server.py` и модулей `mcp-core`.

## Статус живой проверки

Одна машина, один адрес, 12 октября. Chrome оператора с прогретым профилем; проверки площадок проходил человек.

| Источник | Доказательства | Ограничение |
| --- | --- | --- |
| Ozon, Детский мир, Taobao, Ситилинк, AliExpress, Циан | `doctor`: success | Визуальная сверка цен не выполнена |
| Яндекс Маркет | `doctor`: поиск и карточка healthy; товар из #105 отдаёт цену и рейтинг | — |
| Авито | `doctor`: поиск, карточка и продавец healthy; карточка и профиль продавца сходятся по оценке и отзывам; снятое объявление — `not_found` | HTTP 439 из #110 с этого профиля не воспроизвёлся |
| Lamoda | После прохождения проверки: карточка `graphql_cdp` с ценой, старой ценой и размерами; selfcheck success | Кука проверки живёт минуты; в финальном прогоне снова `challenge` |
| DNS, Мегамаркет | Поиск работал сразу после прохождения проверки | Кука живёт минуты; в финальном прогоне снова отказ |
| Wildberries | API с этого адреса отвечает 403 (`transport_down`); `WB_TRANSPORT=dom` — 40 товаров в поиске | Режим `api` зависит от адреса |
| MPStats | `auth_missing` | Нужен платный токен |

Итог финального `doctor`: 8 healthy, 5 inconclusive, 0 drifted.

## Проверка поставки

- Офлайн: 2001 passed, 1 skipped, 5 live deselected; покрытие ветвей 83.21 %.
- Эксплуатационные тесты скриптов: 70 passed.
- Ruff, форматирование, mypy host/win32/darwin, lockfile, версия 2.5.1 в 84 местах, 29 пинов фикстур, защита stdout, счётчик тестов (2002 в 7 местах), wire budget.
- 16/16 настоящих stdio MCP-сессий на v2.5.1.

Поставка содержит 16 wheels и 16 sdists; на PyPI пакеты не публикуются.

## English

v2.5.1 fixes three sources that were returning empty or wrong data under a success status. Yandex Market cards moved to `/card/{slug}/{id}` and are read there again (#105). Lamoda cards fall back to an in-page GraphQL POST in the operator's Chrome now that plain HTTPS gets a 307 loop, and an anti-bot landing answers `challenge_required` at once. Avito's `/web/1/js/items` ignores `itemId` and `userId`: cards now read the item page's schema.org Product and its seller block, `avito_seller` reads the profile page (`active_items` is `null`, the page shows no total), and the Avito canary chains search → card → seller. The repository also gains GRACE markup.

Live evidence, one address on 12 October: Ozon, Yandex Market, Detsky Mir, Avito, Taobao, Citilink, AliExpress and Cian passed selfcheck; Lamoda, DNS and Megamarket passed right after a human cleared their browser checks, whose cookies last minutes; Wildberries answered through `WB_TRANSPORT=dom`. No drift; no visual acceptance.

Offline gate: 2001 passed, coverage 83.21 %, mypy on three platforms, 84 version declarations, 16/16 stdio sessions.
