# v2.7.0 — 2026-10-30

Минорный релиз про установку и первый запуск: клиент настраивается одной командой, Chrome для браузерных источников запускается и показывается из CLI, Wildberries при отказе API сам читает страницу через Chrome. Инструменты, их параметры и поля ответов не меняются; существующие конфиги клиентов продолжают работать.

## Изменения

- **Установка одной командой.** `marketplace-mcp install <client> --write` сам пишет запись в конфиг Claude Desktop или Cursor: сливает её с остальными серверами, кладёт резервную копию `<файл>.bak-<метка>`, отказывает с кодом 2 при кривом JSON или чужой записи с тем же именем (снимается `--force`). Пути к `uv` и консольным командам абсолютные: десктопный клиент часто не видит PATH терминала. Для Claude Code печатается готовая строка `claude mcp add` (#144).
- **Профили.** `--profile compare|decision|full` выбирает сервер одной записи `marketplace`. Новые установки получают `compare`: 4 инструмента сравнения, около 2,2 тыс. токенов описаний инструментов на запрос против около 17,5 тыс. у `full`. Повторная установка без `--profile` сохраняет уже записанный профиль (#147). Замер по профилям — в [docs/TOOLS.md](../TOOLS.md), CI следит, чтобы он не устаревал (#149).
- **`marketplace-mcp setup`** ведёт от нуля до первого ответа: находит клиентов, пишет конфиг, запускает Chrome, прогоняет проверки `doctor` и предлагает `unblock` для закрытых источников. `--dry-run` ничего не пишет и не запускает (#147).
- **Chrome из CLI.** `browser status` показывает, отвечает ли CDP, версию, число вкладок и состояние окна; `browser show` выводит окно на экран; `browser start` запускает видимый Chrome (`--hidden` — за экраном). `unblock` открывает страницу проверки для каждого закрытого браузерного источника, ждёт, пока человек её пройдёт, и перепроверяет только эти источники. Ничего не решается и не обходится автоматически. Скрипты `start_chrome_cdp` больше не требуют закрыть обычный Chrome (#144, #146).
- **`doctor`** печатает под каждой нездоровой строкой один следующий шаг; `--help` печатает короткий usage (#144).
- **Wildberries: фолбэк на страницу.** В режиме `api` `wb_card` и `wb_search` при отказе API адресу (HTTP 401/403/498 или HTML-стена) один раз перечитывают запрос через отрисованную страницу в scraping-Chrome и помечают ответ предупреждением `dom_fallback`. 429, сетевые ошибки и дрейф парсера возвращают свою ошибку, как раньше. `WB_DOM_FALLBACK=0` выключает фолбэк (#145). Если и страница не прочиталась, ошибка называет отказ API — теперь и при сбое самого браузерного драйвера (#151). `wb_selfcheck` учитывает `WB_TRANSPORT=dom` (#143).
- **compare_prices** правильно считает цены Taobao в юанях в `source_outcomes[].detail` (#143).
- **Подготовка к PyPI.** Собирается один пакет `ru-marketplace-mcp` со всеми серверами; новый CI job ставит его обычным pip в чистое окружение и прогоняет настоящие stdio-сессии. Публикация выключена, пока владелец не зарегистрирует издателя на pypi.org (шаги — [DEPLOYMENT.md](../DEPLOYMENT.md#pypi-maintainer)). Запись, сделанная из `uvx`, указывает на закреплённый релиз, а не на временную папку кеша uv (#150).
- **Инструменты выпуска.** `scripts/bump_version.py` меняет версию во всех 88 местах; `.gitattributes` держит LF; workflow `Image check` собирает оба образа и прогоняет stdio-образ через MCP до тега; живые запросы идут через бюджет 40 запросов на прогон, 8 на площадку, 3 с между запросами; новый гейт `check_lock_revision.py` (#144, #148).
- **Аудит перед выпуском.** Код, архитектуру и закрытые PR перепроверили модели Sol и MiniMax, спорные места разобрала Gemini Argon, каждую находку воспроизвёл или отклонил Claude по коду (#153, #154). Исправлено:
  - `install --write` читает конфиг с BOM; конфиг не в UTF-8 — отказ с кодом 2 и подсказкой; резервная копия не затирается второй записью в ту же секунду; конфиг-симлинк остаётся симлинком; `--dry-run` печатает только свои записи и имена остальных; Ctrl-C в `setup` и `unblock` — код 130;
  - `start_chrome_cdp.ps1` брал другой профиль, если в пути есть пробел (`-AllowMainProfile`, имя пользователя Windows с пробелом), и выходил с кодом 0, когда CDP не поднялся; `start_chrome_cdp.sh` принимал чужой процесс на порту за Chrome; профиль `Chrome-Scraping-2` больше не путается с `Chrome-Scraping`;
  - `browser start --hidden` говорит «за экраном», только когда окно правда убрано; `CHROME_CDP_HOST` принимает IPv6;
  - Wildberries: `in_stock` — строго булево, предупреждение фолбэка говорит, что регион берётся из браузера, а не из `dest`; Taobao не принимает страницу входа за догружающуюся выдачу;
  - сборка: опубликованные по тегу workflow сверяют `mcp-publisher` с SHA-256, не оставляют токен на диске, не берут кэш и не публикуют один тег двумя запусками; `bump_version.py` видит закрепления версий в `docs/DEPLOYMENT.md` (88 мест вместо 85); бюджет живых запросов считает `window.open` и вкладки `unblock`, защищён блокировкой и называет запросы, которые не пустил.

## Обновление

- Python 3.12+, зависимости прежние, кроме согласованных минимумов: `playwright>=1.47` и `curl-cffi>=0.8` во всех пакетах (зафиксированные в lock-файле версии не менялись). Оператор обновляет checkout, выполняет `uv sync --all-packages --frozen` и перезапускает серверы.
- Существующие записи в конфигах клиентов работают как раньше. Чтобы перейти на одну запись с профилем: `marketplace-mcp install <client> --write --force`.
- Если Wildberries с вашего адреса отказывает, ничего настраивать не нужно: нужен запущенный scraping-Chrome. Прежнее поведение — `WB_DOM_FALLBACK=0`.

## Статус живой проверки

30 октября, одна машина и один адрес, Chrome оператора через CDP, два захода.

### Днём: Wildberries на коде до аудита

Проверялся код main после #150 (94ca3db); от релиза он отличается номерами версий, исправлением #151 (текст ошибки фолбэка, см. ниже) и исправлениями аудита (их проверял второй заход). Проверка целевая: v2.6.0 прошла живой прогон утром того же дня (12 из 13 источников), а с тех пор путь данных изменился только у Wildberries (фолбэк #145). Код остальных источников не менялся, их не перепроверяли.

Запросы к Wildberries шли двумя прогонами бюджета (до 8 запросов к площадке за прогон), второй — после 30-минутной паузы, всего 13 запросов:

| Время | Запросы | Что проверялось | Результат |
| --- | --- | --- | --- |
| 12:22 | 7 | `wb_selfcheck` | API `card.wb.ru` и `search.wb.ru` с этого адреса — HTTP 403, `transport_down`; отзывы, search-goods и basket — healthy, как в v2.6.0 |
| 12:53 | 3 | `wb_search` «iphone 15», режим `api` | API — 403, инструмент сам перешёл на страницу (`dom_fallback`); чтение страницы упало на этой машине: встроенный в Playwright `node.exe` не смог создать временную папку (EPERM). Ошибка пришла как повторяемый `transport_down`, но без упоминания отказа API — исправлено в #151 |
| 12:54 | 3 | тот же вызов, Playwright запущен с системным node (`PLAYWRIGHT_NODEJS_PATH`) | API — 403, `dom_fallback`, 40 товаров, у всех цена; первый — nm 1642036955, 37 885 ₽, в наличии: тот же товар и цена, что в прогоне v2.6.0 |

Сбой `node.exe` относится к этой машине (системный node в той же оболочке пишет во временную папку), а не к коду проекта.

**CLI.** `marketplace-mcp browser status` на этом Chrome: CDP отвечает, Chrome 156.0.8078.73, 9 вкладок, окно свёрнуто. `install --write`, `setup` и `unblock` проверены офлайн-тестами на временных файлах; живьём на конфигах этой машины их не запускали.

**Сверка глазами.** Цены Wildberries прочитаны из отрисованной страницы поиска; отдельной сверки каждой цифры с сайтом не было.

### Вечером: после аудита, код #153

23:18–23:23 по Москве, код ветки #153 (тот же, что в main fd466d9). Первый заход (27 запросов по журналу) упёрся в сбой этой машины: встроенный в Playwright драйвер не смог создать временную папку (EPERM), и браузерные загрузки до сайтов не дошли. Но запросы не через браузер ушли: Ozon 4, Детский мир 3, Яндекс Маркет 2, Авито 1, Lamoda 1. Повтор с временной папкой внутри рабочей копии и системным node начат новым журналом через минуту, поэтому Ozon за 6 минут получил 12 запросов при лимите 8 на прогон; пауза не меньше 3 секунд между запросами соблюдалась.

| Источник | Результат |
| --- | --- |
| Taobao, Мегамаркет, Lamoda, Ситилинк, AliExpress (поиск и карточка через `window.open`), Циан | healthy |
| Яндекс Маркет, Детский мир | healthy (первый заход) |
| Ozon | поиск и карточка healthy; отзывы не проверены: API отказал curl, на каждую проверку ушло по три запроса, и девятый не пустил бюджет. В отчёте это выглядело как `transport_down` — исправлено в #154 |
| Wildberries, `wb_search` «iphone 15» | API отказал (curl_cffi, затем httpx), инструмент сам перешёл на страницу: 40 товаров, у всех цена, `in_stock` у всех булево; первый — nm 1643388951, 41 153 ₽, в наличии |
| DNS, Авито | `transport_down` (DNS — три загрузки, включая прямой `dns_search`). Код этих источников с v2.6.0 не менялся, тот же транспорт работает у семи других; скорее всего, стена площадки в этот час. Глазами не проверяли: у браузера никого не было |

Всего во втором журнале 35 запросов из 40.

**Решение:** go. Изменённые аудитом пути (фолбэк Wildberries, догрузка Taobao, `window.open` AliExpress, транспорт Chrome) прошли живьём. Не проверены: отзывы Ozon, DNS и Авито (см. таблицу), MPStats (нет токена).

## Проверка поставки

- Офлайн: 2568 passed, 2 skipped, 5 live deselected; `scripts/` — 175 passed. Один пропуск — тест настоящего симлинка: Windows на этой машине не даёт их создавать (WinError 1314); тот же путь на всех ОС проверяет тест с имитацией симлинка.
- Ruff, форматирование, mypy, версия 2.7.0 в 88 местах, защита stdout, привязки фикстур (31), счётчик тестов (2570 в 7 местах), ревизия lock-файла, `docs/TOOLS.md` совпадает с замером.
- 32/32 настоящих stdio MCP-сессий на v2.7.0: каждый сервер в обоих протоколах.
- Lock-файл и `uv sync --frozen` проверены uv 0.13.6, той же версией, что в сборочном образе.
- Образы Docker: workflow `Image check` собирает `Dockerfile.stdio` и `Dockerfile` из релизной ветки на GitHub Actions без публикации и проводит настоящую MCP-сессию через `docker run -i`. До аудита (dacdfbe) он показал v2.7.0, 40 инструментов, 14 источников; прогон на последнем коммите ветки, после аудита, — в #152.
- PyPI-бандл: CI job `PyPI bundle installs` ставит wheel обычным pip в чистое окружение и прогоняет 32 stdio-сессии на установленных командах; на #150 он прошёл, прогон после аудита — в #152.

Поставка содержит 16 wheels и 16 sdists на GitHub Release. На PyPI ничего не публикуется: job публикации пропускается, пока владелец не настроит издателя.

## English

v2.7.0 is a minor release about installing and first use; tools, their arguments and response fields are unchanged, and existing client configs keep working.

`marketplace-mcp install <client> --write` writes the Claude Desktop or Cursor entry itself: it merges with the other servers, keeps a `<file>.bak-<stamp>` backup and refuses malformed JSON or a different entry of the same name unless `--force`. Paths to `uv` and console scripts are absolute because desktop clients often lack the terminal's PATH; Claude Code gets a ready `claude mcp add` line (#144). `--profile compare|decision|full` picks the server behind the single `marketplace` entry. New installs get `compare`: 4 comparison tools, about 2.2k tokens of tool descriptions per request versus about 17.5k for `full`; a reinstall without `--profile` keeps the profile already written (#147). Per-profile costs are measured in [docs/TOOLS.md](../TOOLS.md), which CI keeps current (#149). `marketplace-mcp setup` goes from nothing to a first answer: it finds clients, writes the config, starts Chrome, runs the `doctor` checks and offers `unblock` for blocked sources; `--dry-run` writes and starts nothing (#147).

Chrome from the CLI: `browser status`, `browser show`, `browser start` (visible unless `--hidden`), and `unblock`, which opens each blocked source's check page, waits for a human to pass it and rechecks only those sources; nothing is solved or bypassed automatically. The `start_chrome_cdp` scripts no longer demand closing everyday Chrome. `doctor` prints one next step under each unhealthy row; `--help` prints a short usage (#144, #146).

Wildberries: in `api` mode `wb_card` and `wb_search` re-read a request once through the rendered page in the scraping Chrome when the API refuses the address (HTTP 401/403/498 or an HTML wall), with a `dom_fallback` warning; 429, network errors and parser drift keep their own errors, and `WB_DOM_FALLBACK=0` restores the old behaviour (#145). If the page read then fails, the error names the API refusal, now also for browser-driver failures (#151). `wb_selfcheck` respects `WB_TRANSPORT=dom`, and `compare_prices` counts Taobao's yuan prices correctly in `source_outcomes[].detail` (#143).

PyPI is prepared but publishing is off: one `ru-marketplace-mcp` package with every server, checked by a CI job that installs it with plain pip and runs real stdio sessions; an entry written from `uvx` pins the release instead of uv's temporary cache. Owner steps are in [DEPLOYMENT.md](../DEPLOYMENT.md#pypi-maintainer) (#150). Release tooling: `scripts/bump_version.py` (88 version sites), LF line endings, an `Image check` workflow before tagging, a live request budget of 40 per run / 8 per marketplace / 3 s apart, and a lockfile-revision gate (#144, #148).

Pre-release audit (#153, #154): Sol and MiniMax reviewed the code, the architecture and the closed PRs, Gemini Argon arbitrated the hard cases, and Claude reproduced or rejected every finding in the code. Fixed:
- `install --write`: reads a BOM config, refuses a non-UTF-8 one with exit 2, never overwrites an earlier backup, keeps a symlinked config a symlink; `--dry-run` prints only its own entries and the names of the rest. Ctrl-C in `setup` and `unblock` exits 130.
- `start_chrome_cdp.ps1` started a different profile when the path had a space, and exited 0 when CDP never came up. `start_chrome_cdp.sh` took another listener for Chrome. `Chrome-Scraping-2` is no longer mistaken for `Chrome-Scraping`.
- `browser start --hidden` says off-screen only when the window really is; `CHROME_CDP_HOST` accepts IPv6.
- Wildberries: `in_stock` is a strict bool, and the fallback warning names the browser's region rather than `dest`. Taobao no longer takes a login page for a loading grid.
- Build:
  - the tag workflows check `mcp-publisher` against a pinned SHA-256, leave no token on disk, restore no cache and never publish one tag twice;
  - `bump_version.py` sees the `docs/DEPLOYMENT.md` pins;
  - the live budget counts `window.open` and `unblock` tabs, locks its ledger and names the requests it refused.

Upgrade: Python 3.12+; members now share `playwright>=1.47` and `curl-cffi>=0.8` (locked versions unchanged). Update the checkout, run `uv sync --all-packages --frozen` and restart the servers. To move to the single profile entry, run `marketplace-mcp install <client> --write --force`. If Wildberries refuses your address, a running scraping Chrome is all the fallback needs.

Live, 30 October, one address, the operator's Chrome over CDP, on commit 94ca3db (main after #150; the release adds version strings and the #151 error-text fix). Targeted: v2.6.0 passed a live run earlier the same day on 12 of 13 sources, and only Wildberries' data path changed since. Two budget runs, 13 Wildberries requests in total: `wb_selfcheck` showed the API hosts refusing this address (403) with reviews, search-goods and basket healthy; `wb_search` for "iphone 15" in `api` mode got a 403 and fell back by itself. The first attempt failed on this machine because Playwright's bundled `node.exe` could not create a temp folder (EPERM), and the error lacked the refusal note (fixed in #151); with Playwright pointed at the system node, the same call returned 40 results, all priced, the first nm 1642036955 at 37,885 ₽ in stock, matching the v2.6.0 run. `browser status` read this Chrome correctly. Prices were read from the rendered page and not compared one by one with the site. MPStats was not checked (no token).

Second live check, after the audit, 23:18–23:23 Moscow time, on the code of #153 (= main fd466d9):
- The first attempt (27 ledger requests) hit a local failure: Playwright's bundled driver could not create a temp folder (EPERM), so no browser load reached a site. Non-browser requests did go out: Ozon 4, Detsky Mir 3, Yandex Market 2, Avito 1, Lamoda 1.
- The rerun used a temp folder inside the checkout and the system node. It started on a fresh ledger a minute later, so Ozon got 12 requests in 6 minutes against a per-run cap of 8; the 3 s gap held.
- Healthy: Taobao, Megamarket, Lamoda, Citilink, AliExpress (search, and a card through `window.open`), Cian; Yandex Market and Detsky Mir in the first attempt.
- Ozon: search and card were healthy. Its reviews went unchecked: the API refused curl, each check cost three requests, and the budget refused the ninth. The report showed that as `transport_down`, fixed in #154.
- `wb_search` "iphone 15": the API refused (curl_cffi, then httpx) and the tool fell back to the page by itself. It returned 40 items, all priced, with `in_stock` a bool on every item; the first was nm 1643388951 at 41,153 ₽, in stock.
- DNS and Avito answered `transport_down`. Their code is unchanged since v2.6.0 and the same transport worked for seven other sources, so a marketplace wall at that hour is likely. Nobody was at the browser to check by eye.

35 of 40 requests were used in the second ledger. Decision: go. Every path the audit changed (WB fallback, Taobao loading, AliExpress `window.open`, the Chrome transport) passed live. Not checked: Ozon reviews, DNS, Avito and MPStats.

Offline: 2568 passed, 2 skipped (one is the real-symlink test, which this Windows machine cannot run; a simulated-link test covers the path on every OS); `scripts/` 175 passed; ruff, mypy, 88 version declarations, 31 fixture pins, test count 2570, lockfile revision, `docs/TOOLS.md` current; 32/32 real stdio sessions on v2.7.0; lock and frozen sync checked with uv 0.13.6. Docker images: the `Image check` workflow builds `Dockerfile.stdio` and `Dockerfile` from the release branch on GitHub Actions without pushing and runs a real MCP session over `docker run -i`; before the audit (dacdfbe) it reported v2.7.0, 40 tools, 14 sources, and the run on the branch's final commit is linked in #152. The PyPI bundle job installs the wheel with plain pip into a clean venv and runs 32 stdio sessions on the installed console scripts; it passed on #150, and the post-audit run is on #152. The release ships 16 wheels and 16 sdists on GitHub; nothing goes to PyPI until the owner sets up the publisher.
