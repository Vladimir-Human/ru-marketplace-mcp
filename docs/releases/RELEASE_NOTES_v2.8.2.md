# v2.8.2 — 2026-11-03

Патч-релиз с одним исправлением: предел ожидания навигации в браузере был зашит в коде на 20 секунд, а настройка `ALI_TIMEOUT` у AliExpress не читалась вообще. Страница, до которой `DOMContentLoaded` доходит примерно за 21.5 секунды, падала до разбора товаров. Новых инструментов нет, внешние зависимости в lock-файле остались на прежних версиях.

Исправление по [issue #190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190).

## Исправления

**Навигация получила настраиваемый предел**

- `open_page` принимает `navigation_timeout_ms`. Значение источника важнее `CHROME_NAV_TIMEOUT_MS`; если не заданы оба, остаются прежние 20 секунд для всех остальных источников.
- Принимаются только конечные положительные значения. Ноль, `NaN`, бесконечность и значение, которое не представимо в секундах raw-CDP, отвергаются до обращения к браузеру — отключить предел нельзя.
- Один и тот же предел получают оба транспорта. В raw CDP отправка `Page.navigate` теперь входит в тот же дедлайн, что и чтение событий.

**AliExpress перестал игнорировать собственную настройку**

- Поиск и стартовая страница карточки используют существующий `ALI_TIMEOUT` в секундах (45 по умолчанию). Ограничение относится к навигации, а не ко всему вызову инструмента.
- Не изменились: ожидание готовности страницы, проверки host, статуса основного документа и челленджа, закрытие только собственной вкладки, темп запросов, таксономия ошибок и внешние лимиты инструментов. Ресурсы anti-bot не блокируются, браузер, DNS и куки оператора не меняются.

## Как обновиться

1. Для checkout: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапустите серверы. Нужен Python 3.12 или новее.
2. Для установки через uvx: `uvx ru-marketplace-mcp@2.8.2 install <client> --write --force`. Команда должна соответствовать вашему клиенту; старые конфиги сами не меняются.
3. Если на вашей линии AliExpress отвечает медленно, задайте в окружении сервера `ALI_TIMEOUT=90` (секунды) и перезапустите сервер. Настройка читается при старте. Для `compare_prices` есть отдельный потолок на источник — `COMPARE_SOURCE_TIMEOUT` (45 секунд по умолчанию); он не менялся и при медленной навигации может потребовать увеличения.

## Что проверено

- 3241 тест пакетов прошёл, 2 пропущены, 5 исключены маркерами; 312 проверок скриптов прошли. Покрытие — 88,98% при пороге 80%.
- Все 16 серверов ответили в обоих вариантах MCP-протокола: 32/32 реальные stdio-сессии, каждый сервер отдаёт версию 2.8.2; единый профиль — 39 инструментов и 13 подключённых источников.
- Новая регрессия проходит через публичный MCP `aliexpress_search` с настоящей загрузкой настройки из окружения. На опубликованном 2.8.1 тот же сценарий падает с `Page.goto: Timeout 20000ms exceeded` до разбора страницы; на этой версии доходит до товаров и возвращает цены из сохранённой фикстуры.
- Гейты репозитория зелёные: 88 объявлений версии, ревизия lock-файла 5, no-print, счётчик тестов, tools-doc, provenance, wire-бюджет, mypy на host/win32/darwin, ruff.

## Границы проверки

Точный объём проверки этого релиза:

- Живые проверки площадок в этом раунде не выполнялись: на машине релиза не было запущенного браузера. Изменение касается только предела ожидания навигации, и статус источников, отмеченный в заметках 2.8.1, этим релизом не переоценивается.
- Скорость российских CDN, реальные куки и живая выдача AliExpress не измерялись: регрессия моделирует задержку готовности страницы управляемым браузерным вводом. Замеры автора issue (21,3–21,8 секунды до `DOMContentLoaded` с российского домашнего IP) взяты из его сообщения.
- Искусственного верхнего предела для заданного значения нет: оператор может поставить сколь угодно большое число. Потолки уровня инструмента (`COMPARE_SOURCE_TIMEOUT`, 90 секунд у selfcheck) продолжают действовать независимо.

## Для мейнтейнеров

Версия синхронизирована [штатным скриптом](https://github.com/Vladimir-Human/ru-marketplace-mcp/blob/main/scripts/bump_version.py) во всех 88 объявлениях. Lock-файл обновлён uv 0.13.6, ревизия 5; версии 17 пакетов рабочего пространства подняты до 2.8.2, внешние зависимости не тронуты. Перед тегом проходят проверки версий, покрытия, настоящих stdio-сессий и отдельный Image check обоих образов.

## Состав

Состав артефактов этой версии: 16 wheel и 16 sdist для GitHub Release, отдельный bundle `ru-marketplace-mcp` для PyPI (один wheel и один sdist), stdio-образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.8.2` и запись в MCP Registry. HTTP-образ также собирается в предрелизном Image check без публикации.

## Благодарности

[@HeckfyCU](https://github.com/HeckfyCU) — за подробный разбор и замеры в [issue #190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190): именно его измерения показали, где стоял предел ожидания страницы.

## English

This patch release fixes one defect: the browser navigation deadline was hard-coded at 20 seconds and AliExpress' `ALI_TIMEOUT` setting was never read, so a page that reached `DOMContentLoaded` after roughly 21.5 seconds failed before any extraction. No tools were added; registry dependency versions in the lockfile are unchanged. Fixes [#190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190).

### Fixes

- `open_page` takes an optional `navigation_timeout_ms`. An explicit source value wins over `CHROME_NAV_TIMEOUT_MS`; with neither set, other sources keep the previous 20-second default. Only finite positive values are accepted: zero, `NaN`, infinity and a value that is not representable in raw-CDP seconds are refused before any browser I/O, so the deadline cannot be switched off.
- The same budget reaches both transports; the raw `Page.navigate` send now shares the deadline with the event reads.
- AliExpress search and its card landing use the existing `ALI_TIMEOUT` seconds setting (45 by default). It bounds navigation, not the whole tool call. Page readiness, host, main-document status and challenge guards, owned-tab cleanup, request pacing, the error taxonomy and the outer tool limits are unchanged, and no anti-bot resource is blocked.

### Upgrading

Update a checkout, run `uv sync --all-packages --frozen`, and restart the servers. After publication, refresh a uvx-pinned client entry with `uvx ru-marketplace-mcp@2.8.2 install <client> --write --force`. Python 3.12+ is required. On a slow link, set `ALI_TIMEOUT=90` in the server environment and restart it; `compare_prices` keeps its own per-source ceiling `COMPARE_SOURCE_TIMEOUT` (45 seconds by default), which is unchanged.

### Validation

- Package tests: 3241 passed, 2 skipped, 5 deselected; infrastructure tests: 312 passed. Coverage: 88.98% against the 80% floor.
- All 16 servers completed both protocol eras: 32/32 real stdio sessions, every server reporting 2.8.2; the unified profile exposes 39 tools across 13 mounted sources.
- A new public-MCP regression loads the setting from the real environment. On the published 2.8.1 the same scenario fails with `Page.goto: Timeout 20000ms exceeded` before extraction; on this version it reaches the products and returns the captured prices.
- Repository gates are green: 88 version declarations, lockfile revision 5, no-print, documented test count, tools doc, provenance, wire budget, mypy on host/win32/darwin, ruff.

### Verification scope

- Live marketplace checks were not run in this round: no browser was available on the release machine. The change only alters a navigation deadline, and the source status reported in the 2.8.1 notes is not claimed to be better than it was.
- Russian CDN response times, real cookies and live AliExpress output were not measured; the regression models page-readiness delay through controlled browser input. The reporter's own measurement (21.3–21.8 seconds to `DOMContentLoaded` from a Russian residential IP) is taken from his report.
- There is no artificial upper bound on a configured value; tool-level ceilings (`COMPARE_SOURCE_TIMEOUT`, the 90-second selfcheck limit) still apply.

### For maintainers

The shared bump updates all 88 declarations. The lockfile was relocked with uv 0.13.6 at revision 5: the 17 workspace versions moved to 2.8.2 and no registry dependency changed. Version, coverage, real stdio session and Image check gates run before tagging.

### Thanks

[@HeckfyCU](https://github.com/HeckfyCU) for the detailed analysis and measurements in [issue #190](https://github.com/Vladimir-Human/ru-marketplace-mcp/issues/190): those measurements showed where the page-readiness deadline stood.

### Contents

Artifacts for this version: 16 wheels and 16 sdists on GitHub, one bundled wheel and sdist on PyPI, the versioned stdio OCI image and an MCP Registry entry. The HTTP image is also built by pre-tag Image check without publishing it.
