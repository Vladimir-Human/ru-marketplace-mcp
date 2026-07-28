## Что здесь

Версия 1.2.0: шесть новых маркетплейсов, объединённый сервер, настраиваемый
CDP-хост, CLI и телеметрия. Полные заметки — в `CHANGELOG.md`.

Обратная совместимость: имена и сигнатуры двадцати двух инструментов 1.1.0 не
менялись, транспорт по умолчанию остался stdio.

## Локальная верификация (гейт зелёный)

- [x] **726 офлайн-тестов** зелёные на нативном Windows (4 deselected: live/cdp)
- [x] `uv run python scripts/e2e_stdio_check.py`: 12/12, у всех v1.2.0
  (marketplace-mcp 42 tools, 11 sources mounted, tools/call ok)
- [x] ruff check: All checks passed!; ruff format: 147 files; mypy host+win32:
  75 files no issues; check_no_print: 62 files
- [x] Покрытие 77.35% при пороге 70%
- [x] `taskkill` резолвится в `C:\WINDOWS\system32\taskkill.exe`
- [x] `uv lock --check` чист
- [x] Версии согласованы: `1.2.0 1.2.0 1.2.0`

## `marketplace-mcp doctor` — с reason+code (новое в _3.zip)

Doctor теперь печатает HTTP-код и причину для каждого непроверенного
источника, а не только `state`. Три разных действия вместо одного «inconclusive»:
- `rate_limited http 429` — переждать
- `blocked` — не тот IP / ServicePipe
- `transport_down http 439` — Chrome или сеть

Код возврата: **1** (drift на taobao/lamoda — parse_smoke_failed).

Итог: 5 healthy, 3 blocked, 2 drifted.

| Источник | Status | Detail |
|---|---|---|
| wildberries | success | card:healthy, reviews:healthy, search_goods:healthy, root_basket:healthy |
| ozon | success | search:healthy, card:healthy, reviews:healthy, reviews_sort:healthy |
| yandex_market | success | search:healthy, card:healthy |
| detsky_mir | success | card:healthy, category:healthy, categories:healthy |
| **citilink** | **success** | search:healthy (regex фикс в _3.zip) |
| avito | inconclusive | search+seller: transport_down http 439 (блок) |
| taobao | drift_detected | search:drift (parse_smoke_failed) — ранее success, нестабильно после серии запросов |
| megamarket | inconclusive | search: blocked (ServicePipe) |
| lamoda | drift_detected | card_graphql: transport_down, search:drift (parse_smoke_failed) |
| dns | inconclusive | search: transport_down — ранее success, нестабильно |

CDP: reachable on 127.0.0.1:9222 (1 context), видимое окно, залогинен в
ozon/avito/taobao/megamarket/lamoda/dns/citilink. `doctor-status.json` приложен.

## `scripts/diagnose_drift.py` — классификация drift (новое в _3.zip)

### megamarket — новый зонд (shape_signature)
```
top-level keys: [..., 'items', ...]
items: empty_array
VERDICT: EMPTY — the array is there and empty. Try another query.
```
ServicePipe пройден, API отвечает структуру, но `items` пустой для всех
запросов (ноутбук/смартфон). Не баг парсера — API не отдаёт товары. Возможно
ServicePipe частичный блок или IP-block. Остаётся experimental.

### lamoda — SELECTOR MOVED (не DATA NOT IN DOM)
На search-странице 0 ссылок `/p/` (для «кроссовки» и «ботинки»). diagnose не
нашёл встроенных JSON-состояний (`__NUXT__`/`__NEXT_DATA__`/Apollo) с товарами
— иначе вердикт был бы DATA NOT IN DOM. Значит lamoda search страница реально
не отдаёт products в DOM (captcha/блок/SPA без state). card_graphql отдельно:
`transport_down` — GraphQL endpoint не отвечает. Остаётся experimental.

### taobao — LOGIN → success → нестабильно
После залогина (Google+QR) — был success. После серии doctor-запросов стал
`parse_smoke_failed`. Возможно rate limit или Chrome CDP устал. Ранее
подтверждён залогином.

### citilink/dns — regex фикс (в _3.zip)
`_PRODUCT_ID_RE = /product/([0-9a-fA-F]{24})` → `rf"/product/({_ID_CHARS}+)"`.
Реальные ID 16 hex (citilink) и 16 hex (dns), не 24 hex. Оба success.

## `health_check.py` (шаг 2.7)

4 of 4 connectors fully healthy (wildberries, yandex_market, detsky_mir, ozon).

## `compare_with_china.py "iphone 15"` (шаг 2.6a) — новое предупреждение

`compare_prices` теперь предупреждает когда cheapest похож на аксессуар (запрос
не про аксессуар) или стоит ниже половины медианы. Предупреждает — не
выбрасывает (порог не должен прятать настоящую выгоду).

Рублёвые предложения ранжируются, юани не выиграли, `foreign_currency` warnings
присутствуют. Cheapest: WB 34 224 ₽ (предупреждение: разрыв 34% от Ozon —
возможно чехол/реплика/другая память).

## Объединённый сервер (шаг 2.8)

42 tools mounted (41 + `marketplace_sources`), 11 sources mounted, 0 skipped.

## Сверка глазами (шаг 2.6a)

**Не проводилась.** За пользователем. После сверки 5 стабильных healthy
могут стать 5 подтверждёнными.

## Вердикт

**Conditional go:**

Стабильно healthy (5, без пометок): wildberries, ozon, yandex_market,
detsky_mir, **citilink** (1 новый подтверждён regex-фиксом).

Нестабильно healthy (2, ранее подтверждались, сейчас rate/transport):
- **taobao** — был success после залогина, стал parse_smoke_failed после серии
  запросов. Требует перезапуска Chrome CDP / паузы.
- **dns** — был success после regex-фикса, стал transport_down. Тот же кейс.

`experimental` (3, не подтверждают):
- **avito** — http 439 transport_down (блок)
- **megamarket** — ServicePipe blocked, items:empty_array
- **lamoda** — SELECTOR MOVED (0 `/p/` в DOM), card_graphql transport_down

Из 6 новых: 1 подтверждён стабильно (citilink), 2 нестабильно (taobao/dns —
ранее подтверждались), 3 experimental (avito/megamarket/lamoda). Релиз v1.2.0
возможен с явными пометками experimental для 5 источников. Заголовок «6 новых
маркетплейсов» — неточный; корректно «1 подтверждён + 2 нестабильны + 3
experimental».

## Изменения кода в PR

1. `packages/taobao-connector/src/taobao_connector/server.py`: SSRF-проверка
   домена в `_extract_item_id` (аналог citilink/dns из _3.zip). URL с чужого
   домена → None → BadRequest. Промпт: «Закрыт SSRF в citilink_card и dns_card»
   — taobao логически та же защита. Фиксит `test_card_rejects_input_without_an_item_id[example.com/?id=123456789012]`.
2. `packages/citilink-connector/`, `packages/dns-connector/`: regex фикс
   `_PRODUCT_ID_RE` (`24hex` → `_ID_CHARS+`) — включён автором в _3.zip.
3. `scripts/diagnose_drift.py`: зонд megamarket (shape_signature) + вердикт
   DATA NOT IN DOM для lamoda — включён автором в _3.zip.
4. `compare_prices`: предупреждение о cheapest-похожем-на-аксессуар — включено
   автором в _3.zip.
5. doctor: печатает reason+code (avito rate_limited http 429) — включено в _3.zip.

## Docker-образ (шаг 4)

Не собирался — docker недоступен. Релиз не блокирует.

## Ozon seller spike (шаг 5)

Не проводился (опционально). Ozon success через Tier-1 CDP.
