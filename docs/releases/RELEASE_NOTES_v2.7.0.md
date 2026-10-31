# v2.7.0 — 2026-10-30

Этот релиз упрощает установку и первый запуск. Клиент теперь настраивается одной командой, Chrome для браузерных источников запускается и проверяется из CLI, а Wildberries при отказе API сам читает страницу сайта. Инструменты, их параметры и поля ответов не изменились, а старые конфиги клиентов работают как раньше.

## Что нового

**Настройка клиента**

- Пакет `ru-marketplace-mcp` есть на [PyPI](https://pypi.org/project/ru-marketplace-mcp/2.7.0/), в нём все серверы и CLI. Без checkout сервер ставится и подключается одной командой `uvx ru-marketplace-mcp setup` (#150).
- `marketplace-mcp install <client> --write` сам добавляет сервер в конфиг Claude Desktop или Cursor. Другие серверы и настройки остаются на месте, старый файл сохраняется рядом как `<файл>.bak-<метка>`. Если JSON испорчен или под тем же именем уже есть другая запись, команда ничего не меняет и выходит с кодом 2; заменить запись можно с `--force`. С `--dry-run` команда показывает, что запишет, и не печатает настройки других серверов. Для Claude Code печатается готовая строка `claude mcp add` (#144).
- Пути к `uv` и командам серверов пишутся полными: десктопный клиент часто не видит PATH терминала (#144).
- `--profile compare|decision|full` выбирает, какие инструменты увидит модель. Новые установки получают `compare`: 4 инструмента сравнения и около 2,2 тыс. токенов описаний на запрос вместо примерно 17,5 тыс. у `full`. Повторная установка без `--profile` оставляет прежний профиль. Замеры по профилям — в [docs/TOOLS.md](https://github.com/Vladimir-Human/ru-marketplace-mcp/blob/v2.7.0/docs/TOOLS.md) (#147, #149).
- `marketplace-mcp setup` проводит от пустой машины до первого ответа: находит клиентов, пишет конфиг, запускает Chrome, проверяет источники и для закрытых предлагает `unblock`. С `--dry-run` ничего не пишет и не запускает (#147).

**Chrome для браузерных источников**

- `marketplace-mcp browser status` показывает, отвечает ли Chrome, его версию, число вкладок и состояние окна. `browser show` выводит окно на экран. `browser start` запускает Chrome видимым, а с `--hidden` на Windows убирает окно за экран (#144, #146).
- `marketplace-mcp unblock` открывает страницу проверки каждого закрытого источника, ждёт, пока вы её пройдёте, и проверяет эти источники заново. Сам он ничего не проходит и не обходит (#146).
- Скрипты `start_chrome_cdp` больше не требуют закрыть ваш обычный Chrome и открывают окно видимым. Спрятать его можно ключом `-Hidden` в PowerShell, а на macOS — `--hidden` в shell (#144, #146).
- `doctor` под каждой проблемной строкой пишет, что сделать дальше; `--help` печатает короткую справку (#144).

**Wildberries**

- Если API Wildberries отказывает вашему адресу (HTTP 401, 403, 498 или страница-заглушка), `wb_search` и `wb_card` один раз читают тот же запрос со страницы сайта в Chrome. Это запасной путь, ответ помечается предупреждением `dom_fallback`. Цены и доставка тогда берутся для региона, выбранного в этом браузере, а не из `dest`. Ошибки 429, сети и разбора страницы приходят как раньше. Выключить запасной путь: `WB_DOM_FALLBACK=0` (#145, #153).
- Если не прочиталась и страница, ошибка всё равно называет отказ API (#151). `wb_selfcheck` учитывает `WB_TRANSPORT=dom` (#143).

**Прочее**

- `compare_prices` правильно показывает цены Taobao в юанях в `source_outcomes[].detail` (#143).
- Запись, созданная через `uvx`, ссылается на закреплённый релиз, а не на временную папку кеша uv (#150).

## Исправления

- Поиск Wildberries через страницу падал целиком, если у одной плитки было несколько цен без зачёркнутой. Теперь такая плитка приходит с `price_rub: null`, а предупреждение советует `wb_card`. Ошибка была с v2.5.3 (#153).
- Taobao: страница входа без товаров считалась выдачей, которая ещё грузится. Она ждала 10 секунд и отвечала `timeout`; теперь сразу приходит `parser_drift` (#153).
- `start_chrome_cdp.ps1` запускал Chrome не с тем профилем, если в пути был пробел (например, `-AllowMainProfile` или имя пользователя Windows с пробелом), и выходил с кодом 0, даже когда Chrome так и не ответил. Теперь путь передаётся целиком, а при неудаче код выхода 1 (#153).
- `start_chrome_cdp.sh` принимал за Chrome любой процесс на порту CDP. Теперь, если порт занят чужим процессом, скрипт выходит с кодом 2 (#153).
- Скрипты запуска и скрытие окна на Windows больше не путают профиль `Chrome-Scraping-2` с `Chrome-Scraping`, а скрытие окна теперь находит и процессы Edge (#153).
- `install --write` читает конфиг с BOM, отказывает понятной ошибкой, если файл не в UTF-8, не затирает прежнюю резервную копию и оставляет конфиг-симлинк симлинком. Ctrl-C в `setup` и `unblock` завершает их с кодом 130 (#153).
- `browser start --hidden` говорит «за экраном», только когда окно действительно убрано (#153).
- `CHROME_CDP_HOST` принимает адрес IPv6, `::1` или `[::1]` (#153).

## Как обновиться

1. Нужен Python 3.12 или новее. Обновите checkout и выполните `uv sync --all-packages --frozen`, затем перезапустите серверы. Пакеты теперь требуют `playwright>=1.47` и `curl-cffi>=0.8`; версии в lock-файле не изменились.
2. Старые записи в конфигах работают. Чтобы перейти на одну запись `marketplace` с профилем, выполните `marketplace-mcp install <client> --write --force`.
3. Для запасного пути Wildberries ничего настраивать не нужно, достаточно запущенного Chrome для источников (`marketplace-mcp browser start`). Прежнее поведение: `WB_DOM_FALLBACK=0`.

## Что проверено

- 2568 офлайн-тестов прошли, 2 пропущены. Все серверы ответили в настоящих MCP-сессиях через stdio, в обоих вариантах протокола.
- Пакет с PyPI установлен в чистое окружение, и все серверы из него тоже ответили через stdio.
- Образ Docker собран и проверен настоящей MCP-сессией: версия 2.7.0, 40 инструментов, 14 источников.
- Живая проверка 30 октября, с одного адреса. Ответили Ozon (поиск и карточка), Яндекс Маркет, Детский мир, Taobao, Мегамаркет, Lamoda, Ситилинк, AliExpress и Циан. Wildberries ответил через запасной путь: API отказал, поиск вернул 40 товаров, у всех есть цена.

Подробности проверок — в #152.

## Для мейнтейнеров

`scripts/bump_version.py` меняет версию во всех местах сразу, а `Image check` собирает и проверяет оба образа до тега. Живые запросы идут через бюджет: 40 за прогон, 8 на площадку, к одной площадке не чаще раза в 3 секунды. Запросы, которые бюджет не пустил, он теперь называет. Workflow публикации по тегу сверяют `mcp-publisher` с SHA-256, не оставляют токен на диске и не используют кеш (#144, #148, #153, #154). Публикация на PyPI включена: каждый новый тег `v*` выпускает и пакет на PyPI.

## Состав

16 wheels и 16 sdists на этой странице, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.7.0`, запись в MCP Registry и пакет `ru-marketplace-mcp` на PyPI (один wheel и один sdist со всеми серверами).

## English

This release makes installing and first use simpler. A client is configured with one command, the Chrome used by browser sources is started and checked from the CLI, and Wildberries reads the site's page by itself when its API refuses. Tools, their parameters and response fields are unchanged; existing client configs keep working.

### What's new

**Setting up a client**

- The `ru-marketplace-mcp` package is on [PyPI](https://pypi.org/project/ru-marketplace-mcp/2.7.0/) with every server and the CLI. Without a checkout, `uvx ru-marketplace-mcp setup` installs and wires it in one command (#150).
- `marketplace-mcp install <client> --write` adds the server to the Claude Desktop or Cursor config itself. Other servers and settings stay, and the old file is kept as `<file>.bak-<stamp>`. Malformed JSON or a different entry under the same name changes nothing and exits 2; `--force` replaces the entry. `--dry-run` shows what would be written and never prints other servers' settings. Claude Code gets a ready `claude mcp add` line (#144).
- Paths to `uv` and the server commands are written in full, because desktop clients often lack the terminal's PATH (#144).
- `--profile compare|decision|full` chooses which tools the model sees. New installs get `compare`: 4 comparison tools and about 2.2k tokens of descriptions per request instead of 17.5k for `full`. A reinstall without `--profile` keeps the profile. Measurements are in [docs/TOOLS.md](https://github.com/Vladimir-Human/ru-marketplace-mcp/blob/v2.7.0/docs/TOOLS.md) (#147, #149).
- `marketplace-mcp setup` goes from a bare machine to a first answer: it finds clients, writes the config, starts Chrome, checks the sources and offers to open the blocked ones. `--dry-run` writes and starts nothing (#147).

**Chrome for browser sources**

- `browser status` shows whether Chrome answers, its version, tab count and window state; `browser show` brings the window up; `browser start` starts Chrome visibly; on Windows, `--hidden` moves the window off-screen (#144, #146).
- `unblock` opens each blocked source's check page, waits for you to pass it and checks those sources again. It never passes or bypasses a check itself (#146).
- The `start_chrome_cdp` scripts no longer ask you to close your everyday Chrome and open the window visibly. `-Hidden` in PowerShell, or `--hidden` in the shell on macOS, hides it (#144, #146).
- `doctor` prints a next step under every problem row; `--help` prints a short usage (#144).

**Wildberries**

- When the Wildberries API refuses your address (HTTP 401, 403, 498 or a wall page), `wb_search` and `wb_card` read the same request once from the site's page in Chrome and mark the answer with a `dom_fallback` warning. Prices and delivery then follow the region chosen in that browser, not `dest`. 429, network and parsing errors come back as before. `WB_DOM_FALLBACK=0` turns the fallback off (#145, #153).
- If the page cannot be read either, the error still names the API refusal (#151). `wb_selfcheck` respects `WB_TRANSPORT=dom` (#143).

**Other**

- `compare_prices` shows Taobao's yuan prices correctly in `source_outcomes[].detail` (#143).
- An entry written from `uvx` points at the pinned release, not at uv's temporary cache (#150).

### Fixes

- Wildberries page search failed as a whole when one tile showed several prices without a struck one. Such a tile now comes back with `price_rub: null`, and the warning points at `wb_card`. The bug dated from v2.5.3 (#153).
- Taobao: a login page without items was taken for results still loading. It waited 10 seconds and answered `timeout`; it now returns `parser_drift` at once (#153).
- `start_chrome_cdp.ps1` started Chrome with the wrong profile when the path had a space (for example `-AllowMainProfile`, or a Windows user name with a space) and exited 0 even when Chrome never answered. The path is now passed whole, and a failure exits 1 (#153).
- `start_chrome_cdp.sh` took any process on the CDP port for Chrome. It now checks the port and refuses with exit 2 (#153).
- The launch scripts and window hiding on Windows no longer mistake `Chrome-Scraping-2` for `Chrome-Scraping`, and window hiding now also finds Edge processes (#153).
- `install --write` reads a config with a BOM, refuses a non-UTF-8 file with a clear error, never overwrites an earlier backup and keeps a symlinked config a symlink. Ctrl-C in `setup` and `unblock` exits 130 (#153).
- `browser start --hidden` says off-screen only when the window really is (#153).
- `CHROME_CDP_HOST` accepts an IPv6 address, `::1` or `[::1]` (#153).

### Upgrading

1. Python 3.12 or newer. Update the checkout, run `uv sync --all-packages --frozen` and restart the servers. Packages now require `playwright>=1.47` and `curl-cffi>=0.8`; the locked versions are unchanged.
2. Existing entries keep working. To move to one `marketplace` entry with a profile, run `marketplace-mcp install <client> --write --force`.
3. The Wildberries fallback needs no setup, only a running Chrome for the browser sources (`marketplace-mcp browser start`). `WB_DOM_FALLBACK=0` restores the old behaviour.

### What was checked

- 2568 offline tests passed, 2 skipped. Every server answered in real MCP sessions over stdio, in both protocol versions.
- The PyPI package was installed into a clean environment, and every server in it answered over stdio as well.
- The Docker image was built and checked with a real MCP session: version 2.7.0, 40 tools, 14 sources.
- Live check on 30 October, from one address. Ozon (search and card), Yandex Market, Detsky Mir, Taobao, Megamarket, Lamoda, Citilink, AliExpress and Cian answered. Wildberries answered through the fallback: the API refused, and the search returned 40 items, all with prices.

Details of the checks are in #152.

### For maintainers

`scripts/bump_version.py` changes the version everywhere at once, `Image check` builds and probes both images before the tag, and live requests go through a budget (40 per run, 8 per marketplace, at most one request to a marketplace every 3 seconds) that reports the requests it refused. The tag workflows check `mcp-publisher` against a pinned SHA-256, leave no token on disk and use no cache (#144, #148, #153, #154). PyPI publishing is on: every new `v*` tag also publishes the package to PyPI.

### Contents

16 wheels and 16 sdists on this page, the image `ghcr.io/vladimir-human/ru-marketplace-mcp:2.7.0`, an MCP Registry entry and the `ru-marketplace-mcp` package on PyPI (one wheel and one sdist with every server).
