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
- **Инструменты выпуска.** `scripts/bump_version.py` меняет версию во всех 85 местах; `.gitattributes` держит LF; workflow `Image check` собирает оба образа и прогоняет stdio-образ через MCP до тега; живые запросы идут через бюджет 40 запросов на прогон, 8 на площадку, 3 с между запросами; новый гейт `check_lock_revision.py` (#144, #148).

## Обновление

- Python 3.12+, зависимости прежние, кроме согласованных минимумов: `playwright>=1.47` и `curl-cffi>=0.8` во всех пакетах (зафиксированные в lock-файле версии не менялись). Оператор обновляет checkout, выполняет `uv sync --all-packages --frozen` и перезапускает серверы.
- Существующие записи в конфигах клиентов работают как раньше. Чтобы перейти на одну запись с профилем: `marketplace-mcp install <client> --write --force`.
- Если Wildberries с вашего адреса отказывает, ничего настраивать не нужно: нужен запущенный scraping-Chrome. Прежнее поведение — `WB_DOM_FALLBACK=0`.

## Статус живой проверки

30 октября, одна машина и один адрес, Chrome оператора через CDP. Проверялся код main после #150 (94ca3db); от релиза он отличается только номерами версий и исправлением #151 (текст ошибки фолбэка, см. ниже). Проверка целевая: v2.6.0 прошла живой прогон утром того же дня (12 из 13 источников), а с тех пор путь данных изменился только у Wildberries (фолбэк #145). Код остальных источников не менялся, их не перепроверяли.

Запросы к Wildberries шли двумя прогонами бюджета (до 8 запросов к площадке за прогон), второй — после 30-минутной паузы, всего 13 запросов:

| Время | Запросы | Что проверялось | Результат |
| --- | --- | --- | --- |
| 12:22 | 7 | `wb_selfcheck` | API `card.wb.ru` и `search.wb.ru` с этого адреса — HTTP 403, `transport_down`; отзывы, search-goods и basket — healthy, как в v2.6.0 |
| 12:53 | 3 | `wb_search` «iphone 15», режим `api` | API — 403, инструмент сам перешёл на страницу (`dom_fallback`); чтение страницы упало на этой машине: встроенный в Playwright `node.exe` не смог создать временную папку (EPERM). Ошибка пришла как повторяемый `transport_down`, но без упоминания отказа API — исправлено в #151 |
| 12:54 | 3 | тот же вызов, Playwright запущен с системным node (`PLAYWRIGHT_NODEJS_PATH`) | API — 403, `dom_fallback`, 40 товаров, у всех цена; первый — nm 1642036955, 37 885 ₽, в наличии: тот же товар и цена, что в прогоне v2.6.0 |

Сбой `node.exe` относится к этой машине (системный node в той же оболочке пишет во временную папку), а не к коду проекта.

**CLI.** `marketplace-mcp browser status` на этом Chrome: CDP отвечает, Chrome 156.0.8078.73, 9 вкладок, окно свёрнуто. `install --write`, `setup` и `unblock` проверены офлайн-тестами на временных файлах; живьём на конфигах этой машины их не запускали.

**Сверка глазами.** Цены Wildberries прочитаны из отрисованной страницы поиска; отдельной сверки каждой цифры с сайтом не было.

**Решение:** go. Перепроверен только Wildberries; остальные источники опираются на прогон v2.6.0 того же дня. MPStats не проверен (нет токена).

## Проверка поставки

- Офлайн: 2527 passed, 1 skipped, 5 live deselected; `scripts/` — 140 passed.
- Ruff, форматирование, mypy, версия 2.7.0 в 85 местах, защита stdout, привязки фикстур (31), счётчик тестов (2528 в 7 местах), ревизия lock-файла, `docs/TOOLS.md` совпадает с замером.
- 32/32 настоящих stdio MCP-сессий на v2.7.0: каждый сервер в обоих протоколах.
- Lock-файл и `uv sync --frozen` проверены uv 0.13.6, той же версией, что в сборочном образе.
- Образы Docker: workflow `Image check` собрал `Dockerfile.stdio` и `Dockerfile` из релизной ветки (dacdfbe; после неё менялись только документы, которых нет в контексте сборки) на GitHub Actions без публикации; настоящая MCP-сессия через `docker run -i`: v2.7.0, 40 инструментов, 14 источников.
- PyPI-бандл: CI job `PyPI bundle installs` на #150 поставил wheel обычным pip в чистое окружение, обе команды `--help` отработали, 32/32 stdio-сессии на установленных командах.

Поставка содержит 16 wheels и 16 sdists на GitHub Release. На PyPI ничего не публикуется: job публикации пропускается, пока владелец не настроит издателя.

## English

v2.7.0 is a minor release about installing and first use; tools, their arguments and response fields are unchanged, and existing client configs keep working.

`marketplace-mcp install <client> --write` writes the Claude Desktop or Cursor entry itself: it merges with the other servers, keeps a `<file>.bak-<stamp>` backup and refuses malformed JSON or a different entry of the same name unless `--force`. Paths to `uv` and console scripts are absolute because desktop clients often lack the terminal's PATH; Claude Code gets a ready `claude mcp add` line (#144). `--profile compare|decision|full` picks the server behind the single `marketplace` entry. New installs get `compare`: 4 comparison tools, about 2.2k tokens of tool descriptions per request versus about 17.5k for `full`; a reinstall without `--profile` keeps the profile already written (#147). Per-profile costs are measured in [docs/TOOLS.md](../TOOLS.md), which CI keeps current (#149). `marketplace-mcp setup` goes from nothing to a first answer: it finds clients, writes the config, starts Chrome, runs the `doctor` checks and offers `unblock` for blocked sources; `--dry-run` writes and starts nothing (#147).

Chrome from the CLI: `browser status`, `browser show`, `browser start` (visible unless `--hidden`), and `unblock`, which opens each blocked source's check page, waits for a human to pass it and rechecks only those sources; nothing is solved or bypassed automatically. The `start_chrome_cdp` scripts no longer demand closing everyday Chrome. `doctor` prints one next step under each unhealthy row; `--help` prints a short usage (#144, #146).

Wildberries: in `api` mode `wb_card` and `wb_search` re-read a request once through the rendered page in the scraping Chrome when the API refuses the address (HTTP 401/403/498 or an HTML wall), with a `dom_fallback` warning; 429, network errors and parser drift keep their own errors, and `WB_DOM_FALLBACK=0` restores the old behaviour (#145). If the page read then fails, the error names the API refusal, now also for browser-driver failures (#151). `wb_selfcheck` respects `WB_TRANSPORT=dom`, and `compare_prices` counts Taobao's yuan prices correctly in `source_outcomes[].detail` (#143).

PyPI is prepared but publishing is off: one `ru-marketplace-mcp` package with every server, checked by a CI job that installs it with plain pip and runs real stdio sessions; an entry written from `uvx` pins the release instead of uv's temporary cache. Owner steps are in [DEPLOYMENT.md](../DEPLOYMENT.md#pypi-maintainer) (#150). Release tooling: `scripts/bump_version.py` (85 version sites), LF line endings, an `Image check` workflow before tagging, a live request budget of 40 per run / 8 per marketplace / 3 s apart, and a lockfile-revision gate (#144, #148).

Upgrade: Python 3.12+; members now share `playwright>=1.47` and `curl-cffi>=0.8` (locked versions unchanged). Update the checkout, run `uv sync --all-packages --frozen` and restart the servers. To move to the single profile entry, run `marketplace-mcp install <client> --write --force`. If Wildberries refuses your address, a running scraping Chrome is all the fallback needs.

Live, 30 October, one address, the operator's Chrome over CDP, on commit 94ca3db (main after #150; the release adds version strings and the #151 error-text fix). Targeted: v2.6.0 passed a live run earlier the same day on 12 of 13 sources, and only Wildberries' data path changed since. Two budget runs, 13 Wildberries requests in total: `wb_selfcheck` showed the API hosts refusing this address (403) with reviews, search-goods and basket healthy; `wb_search` for "iphone 15" in `api` mode got a 403 and fell back by itself. The first attempt failed on this machine because Playwright's bundled `node.exe` could not create a temp folder (EPERM), and the error lacked the refusal note (fixed in #151); with Playwright pointed at the system node, the same call returned 40 results, all priced, the first nm 1642036955 at 37,885 ₽ in stock, matching the v2.6.0 run. `browser status` read this Chrome correctly. Prices were read from the rendered page and not compared one by one with the site. MPStats was not checked (no token). Decision: go.

Offline: 2527 passed, 1 skipped; `scripts/` 140 passed; ruff, mypy, 85 version declarations, 31 fixture pins, test count 2528, lockfile revision, `docs/TOOLS.md` current; 32/32 real stdio sessions on v2.7.0; lock and frozen sync checked with uv 0.13.6. Docker images: the `Image check` workflow built `Dockerfile.stdio` and `Dockerfile` from the release branch (dacdfbe; only documents outside the build context changed after it) on GitHub Actions without pushing, and a real MCP session over `docker run -i` reported v2.7.0, 40 tools, 14 sources. The PyPI bundle job on #150 installed the wheel with plain pip into a clean venv and completed 32/32 stdio sessions on the installed console scripts. The release ships 16 wheels and 16 sdists on GitHub; nothing goes to PyPI until the owner sets up the publisher.
