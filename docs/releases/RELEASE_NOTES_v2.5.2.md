# v2.5.2 — 2026-10-13

Патч про честность ответов: страница проверки, отказ сервиса или чужой ответ больше не выдаются за товар или за «ничего не найдено». Схемы параметров инструментов не изменились. В ответах изменились коды ошибок там, где раньше был ложный успех. `avito_card.images` теперь может быть `null`.

## Изменения

- **DNS и Ситилинк:** страница Qrator с HTTP 200 попадала в кеш до всякой проверки. Карточка брала её заголовок («Доступ ограничен») как название товара и отвечала успехом. Проверка на стену была написана, но не вызывалась. Теперь стена распознаётся по явным признакам до разбора и кеша. Ответ — `challenge_required`, самопроверка пишет «заблокировано», а не «дрейф».
- **Taobao, карточки:** проверка на капчу идёт до разбора, независимо от названия и цены. Текст скрытых виджетов её не обманет; на живых карточках ложных срабатываний нет.
- **Wildberries:** если основной поиск упал и запасной тоже (429, HTTP-ошибка, битый JSON), ответом было `no_results`. Теперь это `rate_limited`, `parser_drift` или `transport_down`; «ничего не найдено» — только когда сервис сам так ответил. В режиме `WB_TRANSPORT=dom` пустой поиск сохраняет предупреждение о регионе. Бейдж «-500 ₽» не читается как цена, копейки не теряются.
- **Авито, поиск:** ответ-ошибка с HTTP 200 даёт `challenge_required`, `rate_limited` или `transport_down`, тело без списка объявлений — `parser_drift`. Раньше оба случая были пустым успешным поиском.
- **Авито, карточки:** объявления магазинов, где на странице нет schema.org Product, читаются из разметки страницы: название, цена только со знаком ₽, описание. Id объявления на странице обязан совпасть с запрошенным. 13 октября без Product было одно объявление из шести, и самопроверка показывала дрейф, когда такое попадалось первым. Число фото — `null`, если страница его не сообщает.
- **Ozon:** ответ без `widgetStates` — `parser_drift`, а не пустая карточка; если такая страница попалась среди следующих страниц отзывов, собранные отзывы возвращаются с пометкой «частично». **Мегамаркет:** ответ `success: false` больше не превращается в карточку с запрошенным id и не попадает в кеш.
- **Работа через Chrome:** при таймауте подключения сообщение говорит, сколько вкладок открыто, и просит закрыть лишние (раньше винило версию Chrome). Брошенная пауза для ручной проверки освобождает вкладку по таймауту простоя. `aliexpress_card` сверяет id целиком, закрывает только свою вкладку и не держит блокировку на зависшем закрытии.
- **`scripts/start_chrome_cdp.ps1`** снова запускается в Windows PowerShell 5.1: в файле без BOM были длинные тире, и PowerShell читал их как кавычку.
- **Порог покрытия в CI** — 80 % (было 70 %).

## Статус живой проверки

Одна машина, один адрес, 13 октября, Chrome оператора с прогретым профилем. Проверки площадок в этот день человек не проходил.

| Источник | Доказательства | Ограничение |
| --- | --- | --- |
| Ozon, Яндекс Маркет, Детский мир, Taobao, Ситилинк, AliExpress, Циан | `doctor`: success | Визуальная сверка цен не выполнена |
| Авито | `doctor`: поиск, карточка и продавец healthy; 6 из 6 карточек из живого поиска прочитаны с ценой и продавцом, в том числе объявление магазина без Product | — |
| Taobao, карточки | 4 из 4 карточек из живого поиска с названием и ценой; ложных капч нет | — |
| Wildberries | `WB_TRANSPORT=dom`: 40 товаров, цены положительные, предупреждение о регионе на месте; API с этого адреса — 403 (`transport_down`) | Режим `api` зависит от адреса |
| Lamoda, DNS, Мегамаркет | `doctor`: inconclusive (blocked / transport_down) | Куки проверки истекли; человек их в этот день не обновлял |
| MPStats | `auth_missing` | Нужен платный токен |

Итог финального `doctor`: 8 healthy, 5 inconclusive, 0 drifted.

## Проверка поставки

- Офлайн: 2263 passed, 1 skipped, 5 live deselected; покрытие ветвей 84.64 %.
- Эксплуатационные тесты скриптов: 70 passed.
- Ruff, форматирование, mypy host/win32/darwin, версия 2.5.2 в 84 местах, 30 пинов фикстур, защита stdout, счётчик тестов (2264 в 7 местах), wire budget.
- 16/16 настоящих stdio MCP-сессий на v2.5.2.

Поставка содержит 16 wheels и 16 sdists; на PyPI пакеты не публикуются.

## English

v2.5.2 stops several sources from presenting a failure as data. DNS and Citilink cached a Qrator page served with HTTP 200, and their cards took its title as the product name; walls are now detected before mapping and caching and answer `challenge_required`. Taobao cards check for a challenge before mapping. `wb_search` no longer answers `no_results` when both search paths failed; the DOM mode keeps its region warning on empty results and ignores minus-signed badges. Avito search raises on HTTP-200 error envelopes and on a missing items list; `avito_card` reads shop listings without schema.org Product from the page markers, with an id match, and reports an unknown image count as `null`. Ozon and Megamarket no longer turn a failed or empty envelope into a card. The browser tier reports the open tab count on an attach timeout, frees idle handoffs, and AliExpress closes only its own popup. The Windows Chrome launcher parses again, and the CI coverage floor rises to 80 %.

Live evidence, one address on 13 October: Ozon, Yandex Market, Detsky Mir, Avito (search, card, seller), Taobao, Citilink, AliExpress and Cian passed selfcheck; Wildberries answered through `WB_TRANSPORT=dom`; Lamoda, DNS and Megamarket were blocked because nobody cleared their browser checks that day. No drift; no visual acceptance.

Offline gate: 2263 passed, coverage 84.64 %, mypy on three platforms, 84 version declarations, 16/16 stdio sessions.
