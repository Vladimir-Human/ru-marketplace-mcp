# v2.9.1 — 2026-11-06

Патч-релиз вдогонку к `v2.9.0`: во время начавшейся распродажи Avito («Чёрная пятница») поиск и карточки теперь возвращают реальную цену со скидкой, которую платит покупатель, а также доведены загрузка фото и разбор противоречивых статусов объявлений.

## Исправления

- **Цены со скидкой на Avito** (#206). Во время распродажи `avito_search` и `avito_card` отдают в `price_rub` цену со скидкой (а не зачёркнутую цену до акции), а рядом указывают прежнюю цену `old_price_rub` и размер скидки `discount_percent`. Если цифры скидки на странице не сходятся между собой, возвращается `price_rub=None` с предупреждением вместо догадки.
- **Загрузка фотографий в карточке Avito** (#205). Запросы за фото в `avito_card(photos=1..3)` теперь идут через общий ограничитель частоты запросов к площадке с учётом пауз после отказов, а если в объявлении меньше снимков, чем запрошено, статус явно сообщает `fewer_on_page`.
- **Противоречивые сигналы закрытия объявления** (#204). Если страница Avito одновременно содержит признаки открытого и снятого объявления, карточка помечается как `availability="unknown"`, а чужая закрытая карточка после редиректа больше не выдаётся за закрытие запрошенного объявления.

## Как обновиться

- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.
- Через `uvx`: `uvx ru-marketplace-mcp@2.9.1 install <client> --write --force`.

## Что проверено

Чтение скидочных цен в поиске и карточках Avito проверено на живой выдаче распродажи 6 ноября 2026 года, все 3438 офлайн-тестов, проверка покрытия и 32/32 stdio-сессии серверов зелёные.

## Благодарности

Спасибо [@Pegas-us](https://github.com/Pegas-us) за оперативное исправление распродажных цен и доработку карточки Avito (#204, #205, #206).

## Состав

16 wheel и 16 sdist на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.1` и запись в MCP Registry.

---

## English

Patch release following `v2.9.0`: during Avito's Black Friday sale, search and card tools now return the actual discounted price the buyer pays alongside the previous price and discount percentage, and photo pacing and contradictory closure checks have been refined.

### Fixes

- **Avito sale prices** (#206). During an Avito sale, `avito_search` and `avito_card` now return the discounted buyer price in `price_rub` instead of the pre-sale price, along with `old_price_rub` and `discount_percent`. If the discount numbers on the page do not add up, `price_rub` is `None` with a warning rather than a guess.
- **Avito photo pacing and status** (#205). Photo fetches in `avito_card(photos=1..3)` now go through the shared Avito request pacer (honoring backoff after refusals) and report `photos_status="fewer_on_page"` when the gallery has fewer photos than requested.
- **Contradictory closure signals** (#204). Cards with conflicting open and closed signals now report `availability="unknown"` instead of a false `not_found`, and another listing's closed page is no longer attributed to the requested item ID.

### Upgrading

- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.
- `uvx`: `uvx ru-marketplace-mcp@2.9.1 install <client> --write --force`.

### Verification scope

Sale price handling was verified against live Avito search results and cards on 2026-11-06, and all 3438 offline tests, coverage checks, and 32/32 stdio server sessions pass.

### Thanks

Thanks to [@Pegas-us](https://github.com/Pegas-us) for the fast Black Friday sale price fix and Avito card refinements (#204, #205, #206).

### Contents

16 wheels and 16 sdists on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.1`, and the MCP Registry entry.
