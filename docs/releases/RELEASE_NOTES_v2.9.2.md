# v2.9.2 — 2026-11-12

Патч-релиз по трём отчётам пользователей. `ozon_search` отдаёт цену и рейтинг числами, как `ozon_card`; инструменты сравнения больше не трогают площадки, отключённые в `MARKETPLACE_SOURCES`; `ozon_reviews` снова видит, к какому товару относится отзыв. Появились манифесты для каталогов клиентов.

## Исправления

- **Числа в `ozon_search`** (#226). `price` и `price_original` теперь числа в рублях, `rating` — число от 0 до 5, как в `ozon_card`. Раньше это были строки для показа вроде `"17 324 ₽"` и `"4.9"`. `rating_count` больше не теряется, когда в плитке число оценок записано без скобок или с подписью «оценок». Если значения нет или его не удалось разобрать, поле по-прежнему `null`.
- **`MARKETPLACE_SOURCES` в сравнении** (#225). Отключённые площадки не загружаются при старте и не показываются в `compare_sources`. `compare_verify_offer` и `decision_inspect` отвечают на них `bad_request`, так же как `compare_prices(sources=…)`. Без переменной поведение прежнее.
- **Привязка отзывов Ozon к товару** (#214). `ozon_reviews` принимает `itemId` отзыва и строкой из цифр. Раньше такие отзывы теряли `item_id`, и `own_reviews` молча показывал `0`. Если привязки нет ни у одного отзыва, ответ предупреждает `review_item_id_missing` и не считается здоровым.
- **Кнопки установки** (#212). Кнопки установки в Cursor и VS Code в README ведут на рабочие ссылки.

## Новое

- **Манифесты для каталогов клиентов** (#208, #209, #211, #213): Claude Code (`.claude-plugin/`), Codex, Cursor, Gemini CLI (`gemini-extension.json`), `.mcp.json`, Smithery, skills.sh и инструкция `llms-install.md`.
- **README** (#222, #223). Английский README вынесен в `README.en.md`, русский стал короче, описание инструментов по площадкам — в `docs/TOOLS.ru.md`.

## Как обновиться

- Если ваш код читает `ozon_search` напрямую и разбирал строки цены или рейтинга, уберите этот разбор: поля теперь числа. `compare_prices` это не затрагивает, он и раньше переводил цены в числа.
- Если клиент передаёт в `compare_verify_offer` или `decision_inspect` площадку, отключённую в `MARKETPLACE_SOURCES`, теперь придёт `bad_request`. Добавьте площадку в переменную или не передавайте её.
- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.
- Через `uvx`: `uvx ru-marketplace-mcp@2.9.2 install <client> --write --force`.

## Для мейнтейнеров

- Обновлены зависимости в `uv.lock`, версия uv в образах и actions в workflow (#217–#221).
- `GITHUB_TOKEN` в `ci.yml` ограничен правом `contents: read` (#224).
- `npm-publish.yml` работает и через OIDC Trusted Publisher, и через `NPM_TOKEN` (#210).

## Благодарности

Спасибо [@Runnin4ik](https://github.com/Runnin4ik) за подробные отчёты #225 и #226 и [@XaruStar](https://github.com/XaruStar) за отчёт #214.

## Состав

16 wheel и 16 sdist на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.2` и запись в MCP Registry.

---

## English

Patch release driven by three user reports. `ozon_search` returns price and rating as numbers, like `ozon_card`; the comparison tools no longer touch sources disabled in `MARKETPLACE_SOURCES`; `ozon_reviews` again knows which product a review belongs to. Client catalog manifests are added.

### Fixes

- **Numbers in `ozon_search`** (#226). `price` and `price_original` are now numbers in rubles and `rating` a number from 0 to 5, as in `ozon_card`. They used to be display strings such as `"17 324 ₽"` and `"4.9"`. `rating_count` is no longer lost when the tile shows the count without parentheses or with an «оценок» label. A missing or unparseable value is still `null`.
- **`MARKETPLACE_SOURCES` in comparison** (#225). Disabled sources are not loaded at startup and are not listed by `compare_sources`. `compare_verify_offer` and `decision_inspect` answer them with `bad_request`, as `compare_prices(sources=…)` does. Without the variable nothing changes.
- **Ozon review attribution** (#214). `ozon_reviews` accepts a review `itemId` given as a decimal string. Such reviews used to lose `item_id`, and `own_reviews` silently reported `0`. When no returned review is attributed, the answer warns `review_item_id_missing` and is not healthy.
- **Install buttons** (#212). The Cursor and VS Code install buttons in the README use working links.

### New

- **Client catalog manifests** (#208, #209, #211, #213): Claude Code (`.claude-plugin/`), Codex, Cursor, Gemini CLI (`gemini-extension.json`), `.mcp.json`, Smithery, skills.sh and the `llms-install.md` guide.
- **README** (#222, #223). The English README moved to `README.en.md`, the Russian one is shorter, and the per-source tool details moved to `docs/TOOLS.ru.md`.

### Upgrading

- If your code reads `ozon_search` directly and parsed the price or rating strings, drop that parsing: the fields are numbers now. `compare_prices` is unaffected; it already converted prices to numbers.
- If a client passes a source disabled in `MARKETPLACE_SOURCES` to `compare_verify_offer` or `decision_inspect`, it now gets `bad_request`. Add the source to the variable or stop passing it.
- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.
- `uvx`: `uvx ru-marketplace-mcp@2.9.2 install <client> --write --force`.

### For maintainers

- Dependencies in `uv.lock`, the uv version in the images and workflow actions are updated (#217–#221).
- `GITHUB_TOKEN` in `ci.yml` is limited to `contents: read` (#224).
- `npm-publish.yml` works with both OIDC Trusted Publisher and `NPM_TOKEN` (#210).

### Thanks

Thanks to [@Runnin4ik](https://github.com/Runnin4ik) for the detailed reports #225 and #226 and to [@XaruStar](https://github.com/XaruStar) for report #214.

### Contents

16 wheels and 16 sdists on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.9.2`, and the MCP Registry entry.
