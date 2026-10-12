# v2.5.0 — 2026-10-12

Минорный релиз: новый опциональный режим чтения Wildberries через браузер, фото в карточках Циана и честные ошибки там, где раньше были устаревшие или пустые ответы. Схемы параметров инструментов совместимы; новые поля ответов необязательны.

## Изменения

- **Wildberries, `WB_TRANSPORT=dom`** (по умолчанию `api`, PR #108): `wb_search` и `wb_card` читают отрисованные страницы в Chrome оператора через CDP — для адресов, где API WB отвечает 403 любому клиенту. Карточка отдаёт обычную цену, цену с WB Кошельком и зачёркнутую отдельно, продавца и рейтинг, склад, срок доставки, «Оригинал», других продавцов и предупреждение о риске подделки. «Подозрительная активность» и капча останавливают чтение без повторов. Пустая первая страница поиска без надписи WB «ничего не нашлось» — `parser_drift`, а не «ничего не найдено».
- **Wildberries, отказ поиска** (#101, PR #113): ответ 401/403 или HTML-стена от `search.wb.ru` v9 теперь возвращает `transport_down` вместо устаревших товаров без цен со старого пути; отказ старого пути больше не выглядит как `no_results`.
- **Wildberries, имперсонация** (#100): `card.wb.ru`, `search.wb.ru` и `catalog.wb.ru` читаются с браузерной TLS-подписью с одним откатом на общий клиент; отказы доходят до пейсера; канарейки читают мимо кэша.
- **Циан** (PR #106): региональные редиректы (`rostov.cian.ru`) больше не дают `transport_down`; `cian_card` возвращает `photo_urls` с пометкой планировок.
- **Ситилинк** (#104, PR #107): поиск отправляет запрос в параметре `text` — с `?q=` сайт показывал пустую страницу поиска.
- **Отзывы** (#111, PR #112): маленькая выборка отзывов только с оценками больше не называется дрейфом ключей.
- Сравнение сохраняет собственные предупреждения источников рядом с валидными предложениями; ночная живая канарейка больше не делит слот конкурентности с пушами в `main`.

## Статус живой проверки

Релиз следует правилу conditional go из release checklist. `marketplace-mcp doctor` с одного адреса 12 октября: 5 источников healthy, 8 inconclusive, 0 drifted (код выхода 2).

| Источник | Доказательства 12 октября | Ограничение |
| --- | --- | --- |
| Ozon, Детский мир, Авито, AliExpress, Циан | Selfcheck `success` | Визуальная сверка цен с сайтом не выполнена |
| Wildberries | Отзывы и `search_goods` healthy; v9 и карточка ответили отказом (`transport_down`) — новый код честно его сообщил | Доступность API зависит от адреса; режим `dom` вживую в этом прогоне не проверялся |
| Яндекс Маркет | Поиск healthy | Карточка — `empty_product_shell` (#105) |
| Taobao, Мегамаркет, Lamoda, DNS, Ситилинк | `inconclusive`: вход в аккаунт, репутация IP или CDP-транспорт | Не проверены с этого адреса |
| MPStats | `auth_missing` | Нужен платный токен |

Taobao остаётся в юанях и исключён из рублёвого ранжирования; это проверено офлайн.

## Проверка поставки

- Офлайн: 1930 passed, 1 skipped, 5 live deselected; покрытие ветвей 82.71 %.
- Эксплуатационные тесты скриптов: 70 passed.
- Ruff, форматирование, mypy host/win32/darwin, lockfile, версия 2.5.0 в 84 местах, 26 пинов фикстур, защита stdout, счётчик тестов (1931 в 7 местах), wire budget.
- 16/16 настоящих stdio MCP-сессий на v2.5.0; объединённый сервер смонтировал 14 источников.

Поставка содержит 16 wheels и 16 sdists; на PyPI пакеты не публикуются.

## English

v2.5.0 adds an opt-in Wildberries transport that reads rendered pages through the operator's Chrome (`WB_TRANSPORT=dom`), returns Cian photo URLs and follows Cian's regional redirects, fixes the Citilink search parameter, and turns refusals that used to look like stale or empty results into explicit `transport_down` / `parser_drift` errors. Tool parameter schemas are unchanged; new response fields are optional.

Live acceptance is partial: from one address on 12 October, Ozon, Detsky Mir, Avito, AliExpress and Cian passed selfcheck; Wildberries' v9 search and card were refused there, Yandex cards served empty shells (#105), and the remaining sources were inconclusive, with no drift. No visual verification against the sites was performed.

Offline gate: 1930 passed, coverage 82.71 %, mypy on three platforms, 84 version declarations, 16/16 stdio sessions.
