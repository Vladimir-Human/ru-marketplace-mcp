# v2.4.2 — 2026-09-19

Патч включает исправления PR #88–90: выбор предложений, диагностику и установку.

## Изменения

- Разные SKU Яндекса сохраняются внутри товарной семьи, в том числе без wareId. Повторы удаляются до лимита.
- Неизвестные остатки WB остаются неизвестными. Отрицательные подписи Ozon означают отсутствие; неоднозначные подписи и упаковки не подтверждают наличие.
- Общая диспетчеризация карточек исправляет аргумент Мегамаркета и обработку ссылок в инспекторе шорт-листа.
- Отключённые источники отличаются от отсутствующих установок и не делают сравнение ложно неполным.
- Install включает AliExpress и MPStats; doctor проверяет AliExpress, принимает алиасы и сохраняет причины отказов. Ошибки аргументов завершаются до сетевых запросов.
- MCP-пробы ограничивают ожидание, параллельно читают stderr и завершают созданные процессы. Stdio-проверка сверяет версию и полный состав источников.
- Compare extra all включает AliExpress. Исправлены примеры установки из checkout и GitHub Release wheels; добавлено руководство первого запроса.

## Статус живой проверки

Релиз следует правилу conditional go из release checklist. Офлайн-тесты и MCP-сессии проверяют программные контракты, но не доступность площадок с любой сети.

| Источник | Доказательства 19 сентября | Ограничение |
| --- | --- | --- |
| Wildberries | Selfcheck прошёл; поиск и карточка дали одинаковую цену выбранной строки | Визуальная сверка с сайтом не выполнена; полная живая приёмка не заявляется |
| Яндекс Маркет | Поиск вернул предложения; карточки отвечали пустой оболочкой | Карточка inconclusive; доступность не подтверждена |
| Ozon, AliExpress, Авито, Taobao, Мегамаркет, Lamoda, DNS, Ситилинк, Циан | Парсеры и контракты проверены офлайн | Живая проверка браузером не выполнена: CDP недоступен |
| Детский мир, MPStats | Офлайн-проверки | Живая проверка не выполнена |

Регрессии наличия и вариантов используют синтетические данные поддерживаемой нативной схемы, а не новые живые captures. Taobao остаётся в юанях и исключён из рублёвого ранжирования; это проверено офлайн.

## Проверка поставки

Перед тегом обязательны офлайн-тесты, покрытие не ниже 70%, эксплуатационные тесты, Ruff, форматирование, mypy трёх платформ, версии, fixture pins, wire budget и 16 настоящих stdio MCP-сессий. CI проверяет Python 3.12/3.13 на Ubuntu, Windows и macOS.

Поставка содержит 16 wheels и 16 sdists. Отдельный workflow собирает OCI-образ, проверяет initialize/tools-list/tools-call через Docker и затем публикует MCP Registry entry. Завершение workflows подтверждает публикацию; один тег не доказывает доступность образа или registry entry.

## English

This patch ships PRs #88–90: variant-preserving Yandex search, conservative WB/Ozon stock, shared card dispatch, explicit source selection, actionable diagnostics, bounded MCP probes, and complete standalone comparison dependencies. Tool parameter schemas remain compatible.

Live acceptance is partial. WB search and its follow-up card agreed, but no visual site verification was performed. Yandex search answered while cards served empty product shells. Other source access remains unverified for this release. Synthetic native-schema fixtures prove changed parser contracts, not marketplace availability.

Use the source checkout or matching GitHub Release wheels. The all comparison extra includes AliExpress; older v2.4.1 instructions retain its explicit-install workaround. These packages are not published to PyPI.

### Release-candidate evidence

- Offline suite: 1803 passed, 1 skipped, 5 live tests deselected; branch coverage 81.73%.
- Operational script suite: 56 passed, including stale/missing Docker-version rejection.
- All 16 local stdio servers returned 2.4.2; unified introspection confirmed 14 mounted sources.
- Built 16 wheels and 16 sdists. A clean environment installed compare-connector[all] from these wheels and imported all ten searchable sources.
- Ruff, formatting, mypy on host/win32/darwin, lockfile, 84 version declarations, fixture pins, stdout guard, test count, and existing wire budgets passed.

These are pre-publication results. Published artifacts are accepted only after their release workflows and read-back checks complete.
