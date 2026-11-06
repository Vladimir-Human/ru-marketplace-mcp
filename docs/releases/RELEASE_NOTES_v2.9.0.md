# v2.9.0 — 2026-11-06

Карточка и поиск Avito стали заметно полезнее для выбора б/у вещей: теперь видно резерв, условия сделки, проверки продавца, город с метро и, по запросу, до трёх фотографий товара. Заодно починили чтение карточек AliExpress без Playwright, листание отзывов Ozon и короткие имена площадок в инструментах сравнения.

## Что нового

- **Резерв на Avito** (#194). Карточка и поиск показывают, свободно объявление или уже зарезервировано другим покупателем. В поиске зарезервированные объявления по умолчанию остаются в выдаче, но их можно скрыть через `include_inactive=false`.
- **Условия сделки, продавец и гео на Avito** (#198). Карточка возвращает состояние вещи, характеристики, доступность доставки и безопасной сделки, отношение продавца к торгу, подтверждённые проверки профиля, стаж на площадке, просмотры, город и станции метро. Точный адрес и координаты отдаются только для компаний, у частных продавцов они скрыты.
- **Фото объявления по запросу** (#199, #201). Параметр `avito_card(photos=1..3)` прикладывает к ответу до трёх снимков активного объявления, чтобы агент видел внешний вид и комплектность вещи. По умолчанию фото выключены (`photos=0`) и не тратят лишние запросы и токены.

## Исправления

- **AliExpress и Chrome по raw CDP** (#192, #195). `aliexpress_card` теперь работает и без Playwright, открывая и аккуратно закрывая собственную вкладку товара, а навигация в новых вкладках больше не обрывается на стартовом `about:blank`.
- **Отзывы Ozon** (#193). `ozon_reviews` снова переходит по страницам с актуальной сортировкой площадки и не помечает ответ частичным, когда запрошенный лимит уже набран.
- **Короткие имена площадок в сравнении** (#197, #200). `compare_prices`, `compare_verify_offer` и `decision_inspect` принимают те же имена и сокращения (`wb`, `yandex`, `ym`, `ali`), что показывает `marketplace_sources`.
- **Закрытые объявления Avito** (#202). Снятые и удалённые объявления стабильно возвращают `not_found` с текстом площадки вместо ошибки парсера, а слова вроде «404» или «не найдено» в описании продавца больше не закрывают открытое объявление.
- **Офлайн-тесты без Node.js** (#196, #200) и обновление зависимостей (#182, #188).

## Как обновиться

- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.
- Через `uvx`: `uvx ru-marketplace-mcp@2.9.0 install <client> --write --force`.

## Что проверено

Новые сценарии Avito и чтение карточек AliExpress проверены на живых страницах 5–6 ноября 2026 года, все офлайн-тесты, проверка покрытия и реальные stdio-сессии всех 16 серверов зелёные. Полный живой обход остальных площадок в этом выпуске не проводился, их статус остаётся таким же, как в заметках к `v2.8.1`.

## Благодарности

Спасибо [@Pegas-us](https://github.com/Pegas-us) за всю серию доработок, живые снимки страниц и репорты (#192–#199, #201, #202).

## Состав

16 wheel и 16 sdist на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.0` и запись в MCP Registry.

---

## English

Avito search and cards now give agents enough context to evaluate second-hand listings: reservation status, deal and delivery terms, seller verifications, city and metro stations, and up to three opt-in listing photos. This release also fixes AliExpress card reads over raw CDP, Ozon review pagination, and marketplace aliases in comparison tools.

### What's new

- **Avito reservation status** (#194). Cards and search results show whether a listing is active or reserved. Reserved items stay in search results by default and can be filtered out with `include_inactive=false`.
- **Avito deal terms, seller checks, and location** (#198). `avito_card` now reports item condition, specs, delivery and safe-deal availability, bargain terms, verified seller badges, tenure, view counts, city, and metro stations. Street addresses and coordinates are returned only for companies and withheld for private sellers.
- **Opt-in Avito photos** (#199, #201). `avito_card(photos=1..3)` attaches up to three photos of an active listing so the agent can inspect item condition and completeness. Off by default (`photos=0`).

### Fixes

- **AliExpress and raw CDP** (#192, #195). `aliexpress_card` now works without Playwright via an owned popup tab, and raw-CDP tab navigation no longer trips on the initial `about:blank` load.
- **Ozon reviews** (#193). `ozon_reviews` paginates again with Ozon's current default sort and no longer marks a filled `limit` as partial.
- **Source aliases in comparison tools** (#197, #200). `compare_prices`, `compare_verify_offer`, and `decision_inspect` accept the names and short aliases (`wb`, `yandex`, `ym`, `ali`) listed by `marketplace_sources`.
- **Closed Avito ads** (#202). Withdrawn and removed ads reliably return `not_found` with Avito's message instead of `parser_drift`, and words like "404" in seller descriptions no longer close an open listing.
- **Offline tests without Node.js** (#196, #200) and dependency updates (#182, #188).

### Upgrading

- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.
- `uvx`: `uvx ru-marketplace-mcp@2.9.0 install <client> --write --force`.

### Verification scope

The Avito and AliExpress changes were verified against live pages on 2026-11-05/06, and all offline, coverage, and 32/32 stdio server gates pass. A full live sweep across the remaining marketplaces was not re-run for this release; their status remains as reported in `v2.8.1`.

### Thanks

Many thanks to [@Pegas-us](https://github.com/Pegas-us) for the pull requests, live page captures, and bug reports (#192–#199, #201, #202).

### Contents

16 wheels and 16 sdists on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.0`, and the MCP Registry entry.
