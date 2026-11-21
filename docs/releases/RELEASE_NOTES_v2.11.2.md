# v2.11.2 — 2026-11-21

Патч-релиз. У `avito_card` появилась настройка, сколько фото объявления можно запросить: теперь не больше трёх, как было, а столько, сколько разрешит оператор, до десяти. Без настройки всё работает как раньше.

## Новое

- **Авито: число фото в карточке задаёт оператор** (#343). Настройка `AVITO_MAX_PHOTOS` (целое от 0 до 10, по умолчанию 3) задаёт, сколько фото объявления может приложить `avito_card(photos=N)`. Это же число стоит в схеме инструмента как максимум `photos`, поэтому агент не может попросить больше фото, чем инструмент скачает. Значение вне 0–10 или не целое сервер не принимает при запуске. Без настройки максимум остаётся 3.

## Известные ограничения

- В день выпуска на машине владельца с живыми страницами сверено только Авито, единственная площадка, которую меняет выпуск: поиск (50 из 50 объявлений с ценами) и первая карточка. С `AVITO_MAX_PHOTOS=10` карточка приняла `photos=4`, что при прежнем пределе 3 было бы ошибкой запроса.
- Само скачивание фото в тот день на машине владельца не прошло: сервер картинок Авито не ответил за 15 секунд (`photos_status: fetch_failed`). Это сетевой сбой этой машины, а не изменение выпуска: он не зависит от настройки и затронул бы и `photos=1`.
- Остальные площадки в этом выпуске не менялись и с живыми страницами не сверялись; их состояние на 2.11.1 описано в заметках к 2.11.1.

## Как обновиться

- В один клик для Claude Desktop: скачайте `ru-marketplace-mcp-compare-2.11.2.mcpb` со страницы релиза и откройте его в Claude Desktop.
- Через `uvx`: `uvx ru-marketplace-mcp@2.11.2 install <client> --write --force` (доступны `claude`, `claude-code`, `cursor`, `vscode`, `windsurf`, `antigravity`, `gemini`, `codex`, `dsh`).
- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.

## Состав

15 wheel и 15 sdist рабочих пакетов и бандл `ru-marketplace-mcp-compare-2.11.2.mcpb` на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.11.2`, запись в MCP Registry (`pypi` и `oci`) и пакет для DSH в npm.

---

## English

Patch release. `avito_card` gets a setting for how many listing photos may be requested: instead of a fixed three, as many as the operator allows, up to ten. Unset, everything works as before.

### New

- **Avito: the operator sets the card's photo count** (#343). `AVITO_MAX_PHOTOS` (an integer from 0 to 10, default 3) sets how many listing photos `avito_card(photos=N)` may attach. The same number is the advertised maximum of `photos` in the tool schema, so an agent cannot ask for more photos than the tool fetches. A value outside 0–10, or not an integer, is refused when the server starts. Unset, the maximum stays 3.

### Known limits

- On release day only Avito, the one source this release changes, was checked against live pages on the owner's machine: search (50 of 50 listings priced) and its first card. With `AVITO_MAX_PHOTOS=10` the card accepted `photos=4`, which the old cap of 3 would have refused as a bad request.
- The photo download itself did not complete on the owner's machine that day: Avito's image server did not answer within 15 seconds (`photos_status: fetch_failed`). This is a network fault of that machine, not a change in this release: it does not depend on the setting and would affect `photos=1` too.
- The other sources are unchanged in this release and were not checked live; their state on 2.11.1 is in the 2.11.1 notes.

### Upgrading

- One-click for Claude Desktop: download `ru-marketplace-mcp-compare-2.11.2.mcpb` from the GitHub Release page and open it in Claude Desktop.
- `uvx`: `uvx ru-marketplace-mcp@2.11.2 install <client> --write --force` (`claude`, `claude-code`, `cursor`, `vscode`, `windsurf`, `antigravity`, `gemini`, `codex`, `dsh`).
- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.

### Contents

15 workspace wheels and 15 sdists plus `ru-marketplace-mcp-compare-2.11.2.mcpb` on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.11.2`, the MCP Registry entry (`pypi` and `oci`), and the DSH bundle on npm.
