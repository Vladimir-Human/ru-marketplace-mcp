# Задача: опубликовать ru-marketplace-mcp v1.0.0 на GitHub

Ты публикуешь готовый, уже проверенный проект. Код писать не нужно. Задача —
проверить локально то, что нельзя было проверить из песочницы, и провести релиз.

**Корневая папка проекта:** `<ПУТЬ_К_ПАПКЕ>`
(если путь не подставлен, спроси у пользователя абсолютный путь и не начинай без него)

**Машина:** Windows / PowerShell (для Linux/macOS замены команд отмечены отдельно)

---

## Что это за проект

Монорепо из 5 MCP-серверов для российских маркетплейсов, 20 инструментов:

| Пакет | Сервер | Инструментов |
|---|---|---|
| `wb-connector` | `wb-mcp` | 7 |
| `ozon-connector` | `ozon-mcp` | 4 |
| `yandex-connector` | `yandex-mcp` | 3 |
| `detmir-connector` | `detmir-mcp` | 4 |
| `compare-connector` | `compare-mcp` | 2 |

Общий рантайм — `mcp-core`. Секретов в проекте нет вообще: ни ключей, ни токенов.

## Что уже проверено (повторять не нужно)

- 221 офлайн-тест проходят; ruff, ruff format и mypy полностью чисты
- Чистая установка с нуля без lockfile — работает
- Все 5 серверов проверены через реальный MCP stdio-протокол: handshake,
  `tools/list`, `tools/call` с живыми данными
- Live selfcheck: WB, Яндекс Маркет, Детский мир → `success`
- WB, Яндекс, Детский мир, кросс-сравнение цен — на живых данных

## Что НЕ проверено и требует твоей верификации

Одно ограничение среды, где готовился релиз: датацентровый IP и отсутствие
браузера с логином. Поэтому:

1. **Ozon Tier-1** (`curl_cffi`) — из датацентра отдаёт бесконечную петлю 307.
   На первой верификации с российского домашнего IP отработал `success`, то есть
   CDP в таких условиях не нужен вовсе. Подтверди на своём IP.
2. **Ozon Tier-2** (Chrome CDP) — не проверялся вообще: нужен твой залогиненный
   Chrome.
3. **Windows-специфичные пути** — покрыты юнит-тестами через `PLATFORM_OVERRIDE`
   и `mypy --platform win32`, но нативный прогон на Windows остаётся за тобой.

Шаг 2 ниже закрывает именно это.

### Исправлено по итогам первой верификации на Windows

Первый прогон этого промпта на Windows нашёл 3 упавших теста и 3 ошибки mypy, все
в `mcp_core/process.py`: `os.killpg`, `os.getpgid` и `signal.SIGKILL` отсутствуют на
Windows, а тесты пытались их подменить. Исправлено: POSIX-вызовы вынесены в
`kill_process_group()` с доступом через `getattr`, тесты подменяют эту функцию
вместо несуществующих атрибутов. В CI и в шаг 2 добавлен `mypy --platform win32`.
Именно его отсутствие скрыло ошибку: на Linux mypy считает эти атрибуты
существующими.

---

## Шаг 1. Предусловия

```powershell
python --version   # нужен 3.12+
uv --version       # если нет: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git --version
gh --version       # GitHub CLI; если нет — https://cli.github.com/
gh auth status     # если не залогинен: gh auth login
```

## Шаг 2. Локальная верификация (обязательно, это твоя основная работа)

Из корневой папки:

```powershell
uv sync --all-packages
uv run pytest -q                      # ожидаемо: 221 passed
uv run ruff check .                   # ожидаемо: All checks passed!
uv run ruff format --check .          # ожидаемо: N files already formatted
uv run mypy packages/*/src            # ожидаемо: Success: no issues found
uv run mypy --platform win32 packages/*/src   # ожидаемо: Success (ловит Windows-only ошибки)
uv run python scripts/check_no_print.py   # ожидаемо: no stdout writes
```

**Если любая из команд падает, остановись и сообщи вывод.** Не публикуй.

### 2.1 Проверка на нативном Windows

Здесь важен сам факт запуска на Windows, а не отдельный тест.

```powershell
uv run pytest packages/ozon-connector -q
uv run python -c "from mcp_core.process import taskkill_cmd, windows_system_dir; print(taskkill_cmd())"
```

Ожидаемо: путь вида `C:\Windows\System32\taskkill.exe`, именно с обратными
слешами. Если увидишь прямые слеши или путь из переменной окружения, сообщи:
это регрессия.

### 2.2 Ozon Tier-1 с домашнего IP

```powershell
uv run python -c "import asyncio; from ozon_connector.server import ozon_selfcheck; print(asyncio.run(ozon_selfcheck()).status)"
```

- `success` — Tier-1 работает с твоего IP, отлично
- `inconclusive` — Tier-1 блокируется, переходи к 2.3
- `drift_detected` — Ozon изменил формат ответа. **Это блокер: сообщи и не
  публикуй**, коннектор требует правки

### 2.3 Ozon Tier-2 (Chrome CDP)

```powershell
.\scripts\start_chrome_cdp.ps1
```

В открывшемся окне залогинься **только в ozon.ru**. Профиль отдельный
(`%LOCALAPPDATA%\Chrome-Scraping`). Банк и почту туда не заводи: это и есть
основная мера безопасности.

Проверь порт и повтори selfcheck:

```powershell
Test-NetConnection 127.0.0.1 -Port 9222     # TcpTestSucceeded : True
uv run python -c "import asyncio; from ozon_connector.server import ozon_selfcheck; print(asyncio.run(ozon_selfcheck()).status)"
```

Теперь ожидается `success`. Подробности и модель угроз — `docs/CDP_SETUP.md`.

### 2.4 Полное живое сравнение цен

```powershell
uv run python examples/health_check.py
uv run python examples/price_check.py "стиральная машина узкая"
```

С российского IP и настроенным CDP в `health_check` должны быть все четыре
`success`, а в `price_check` — `Complete: True` и предложения от нескольких
маркетплейсов. Зафиксируй фактический результат для отчёта.

> Если WB отдаёт `rate_limited`, это нормально: он ограничивает частые поиски.
> Подожди минуту и повтори.

## Шаг 3. Подготовка репозитория

Замени плейсхолдер `OWNER` на реальный GitHub-логин в четырёх файлах:

```powershell
$owner = "<ТВОЙ_GITHUB_ЛОГИН>"
foreach ($f in @("README.md","CHANGELOG.md","CONTRIBUTING.md","SECURITY.md","pyproject.toml")) {
  (Get-Content $f -Raw) -replace 'OWNER', $owner | Set-Content $f -NoNewline
}
Select-String -Path README.md,CHANGELOG.md,pyproject.toml -Pattern "OWNER"   # должно быть пусто
```

Linux/macOS: `sed -i "s/OWNER/$owner/g" README.md CHANGELOG.md CONTRIBUTING.md SECURITY.md pyproject.toml`

Проверь, что не попадёт лишнего:

```powershell
git init
git add -A
git status --short           # .venv, __pycache__, Chrome-Scraping попасть НЕ должны
git diff --cached --stat | Select-Object -Last 3
```

`uv.lock` **должен** быть в коммите. Он фиксирует воспроизводимый набор
зависимостей. Всё остальное лишнее отсекается `.gitignore`.

## Шаг 4. Первый коммит

```powershell
git branch -M main
git commit -m "feat: ru-marketplace-mcp v1.0.0

MCP servers for Russian marketplaces: Wildberries, Ozon, Yandex Market,
Detsky Mir, plus cross-marketplace price comparison. 20 tools across 5 stdio
servers over a shared runtime.

See CHANGELOG.md for the full release notes."
```

## Шаг 5. Публичный репозиторий и push

```powershell
gh repo create ru-marketplace-mcp --public --source=. --remote=origin --push `
  --description "MCP servers for Russian marketplaces — Wildberries, Ozon, Yandex Market, Detsky Mir + cross-marketplace price comparison. Read-only, no credentials."
```

Название можно поменять, если пользователь предпочтёт другое — тогда поправь и
ссылки в README/CHANGELOG.

Добавь темы для находимости:

```powershell
gh repo edit --add-topic mcp,model-context-protocol,wildberries,ozon,yandex-market,marketplace,russia,ecommerce,price-comparison,claude
```

## Шаг 6. Дождись зелёного CI

```powershell
gh run list --limit 5
gh run watch
```

CI прогоняет lint + mypy + тесты на Ubuntu/Windows/macOS × Python 3.12/3.13 и
smoke-проверку всех серверов. Live- и CDP-тесты исключены намеренно: у CI нет ни
российского IP, ни браузера с логином.

**Если CI красный:**
- падение на конкретной ОС → посмотри `gh run view --log-failed`, сообщи вывод
- ошибка сети до маркетплейса → в CI такого быть не должно, значит какой-то тест
  тянет сеть: сообщи, какой именно
- **не публикуй релиз с красным CI**

## Шаг 7. Тег и GitHub Release

Только после зелёного CI:

```powershell
git tag -a v1.0.0 -m "ru-marketplace-mcp v1.0.0"
git push origin v1.0.0

gh release create v1.0.0 --title "v1.0.0 — first public release" --notes @'
MCP servers for reading Russian marketplaces: **Wildberries, Ozon, Yandex Market,
Detsky Mir**, plus **cross-marketplace price comparison**.

Read-only. No credentials, no API keys, no account required.

## What is in it

20 tools across 5 stdio MCP servers sharing one runtime:

- **Wildberries** (7) — search, cards, reviews, **seller legal identity** (INN/OGRN), catalog tree
- **Yandex Market** (3) — multi-seller prices, **per-star rating distribution**, reviews
- **Detsky Mir** (4) — kids goods, offline store stock, category listings
- **Ozon** (4) — search, cards, reviews via TLS impersonation with a browser fallback
- **Compare** (2) — "where is this cheapest?" across every installed marketplace at once

## Highlights

- **Two prices, never conflated.** Yandex Market advertises a subscriber price 25-30%
  below its everyday one; both are reported separately and only the everyday price is ranked.
- **Partial results are labelled as such.** Marketplaces fail independently; each
  reports its own outcome, and `complete: false` says so plainly.
- **Seller identity.** `wb_seller` returns the registered legal entity and tax ids
  behind a storefront name.
- **No credentials anywhere.** The authenticated Ozon tier runs inside a Chrome you
  logged into yourself.

## Notable fix

`wb_search` returned pages where almost nothing had a price: it resolved ids through a
stale index that served delisted SKUs. It now reads `search.wb.ru` v9 directly — one
request instead of two, 100 priced results per page instead of 30 mostly-empty ones.

## Quality

219 offline tests; ruff, ruff-format and mypy clean; CI on Ubuntu/Windows/macOS
against Python 3.12 and 3.13.

Full notes: [CHANGELOG.md](CHANGELOG.md) · Feasibility findings for four marketplaces
that did not make the release: [docs/ANTI_BOT.md](docs/ANTI_BOT.md)
'@
```

## Шаг 8. Финальная проверка «как у пользователя»

Склонируй свежую копию в другую папку и убедись, что всё ставится с нуля:

```powershell
cd $env:TEMP
git clone https://github.com/<ТВОЙ_ЛОГИН>/ru-marketplace-mcp.git verify
cd verify
uv sync --all-packages
uv run pytest -q
uv run python -c "import asyncio; from wb_connector.server import wb_selfcheck; print(asyncio.run(wb_selfcheck()).status)"
```

## Шаг 9. Подключи к своему MCP-клиенту

```powershell
claude mcp add wildberries -- uv run --directory <ПУТЬ_К_ПАПКЕ> wb-mcp
claude mcp add yandex-market -- uv run --directory <ПУТЬ_К_ПАПКЕ> yandex-mcp
claude mcp add detsky-mir -- uv run --directory <ПУТЬ_К_ПАПКЕ> detmir-mcp
claude mcp add compare-prices -- uv run --directory <ПУТЬ_К_ПАПКЕ> compare-mcp
claude mcp add ozon -- uv run --directory <ПУТЬ_К_ПАПКЕ> ozon-mcp
```

Конфиги для Claude Desktop и Cursor — в README. После подключения перезапусти
клиент и попроси агента вызвать `wb_selfcheck`.

---

## Критерии успеха

- 221 тест зелёный локально **на Windows**
- ruff, ruff format, mypy, stdout-guard — чисто
- `taskkill` резолвится в `C:\Windows\System32\taskkill.exe`
- Ozon selfcheck = `success` (Tier-1 или Tier-2); `inconclusive` без CDP — допустимо,
  `drift_detected` — блокер
- Публичный репозиторий создан, `OWNER` нигде не осталось
- CI зелёный на всех шести комбинациях ОС × Python
- Тег `v1.0.0` и Release опубликованы
- Свежий клон ставится и проходит тесты

## Не делай

- **Не запускай `uv run wb-mcp` в интерактивном терминале** — сервер повиснет в
  ожидании JSON-RPC на stdin. Проверяй через MCP-клиент или `import`.
- **Не публикуй при красном CI или падающих тестах.**
- **Не коммить `.venv`, `__pycache__`, `Chrome-Scraping`** — `.gitignore` их
  отсекает, но проверь `git status` перед коммитом.
- **Не удаляй `uv.lock`** — он нужен для воспроизводимой установки.
- **Не добавляй никаких секретов.** Их в проекте нет и быть не должно; если решишь
  добавить прокси — только через переменную окружения, не в файл.
- **Не логинься в CDP-профиль ничем, кроме маркетплейсов.** Отдельный профиль — это
  и есть защита.
- **Не убирай задержки между запросами** (`*_MIN_GAP`). Это вежливость к чужой
  инфраструктуре и защита от бана.
- **Не «исправляй» отсутствие поиска у Детского мира.** Его API молча игнорирует
  текстовые фильтры и возвращает весь каталог, а сайтовый роут отдаёт 404 с промо-
  карусселью. Инструмент поиска там был написан, проверен на живых данных (на запрос
  «лего» вернулись подгузники и коллаген) и удалён намеренно. Подробности —
  `docs/ANTI_BOT.md`.

## Отчёт

По завершении сообщи:

1. Результаты шага 2 по пунктам (тесты, линтеры, Windows-пути)
2. Вердикт Ozon selfcheck и какой tier сработал; настраивал ли CDP
3. Вывод `health_check.py` — сколько коннекторов `success` с твоего IP
4. Был ли `Complete: True` в `price_check.py` и какие маркетплейсы ответили
5. URL репозитория и релиза
6. Статус CI по каждой комбинации ОС × Python
7. Любые отклонения от этой инструкции и как ты их решил

## Опционально, после релиза

**PyPI** (если пользователь захочет `uvx wb-mcp` без клонирования): пакеты уже
собираемы через hatchling. Нужно проверить уникальность имён на PyPI, собрать
`uv build` в каждом пакете и загрузить `uv publish` с токеном. Имена `wb-connector`
и подобные слишком общие — возможно, потребуется префикс.

**Каталоги MCP-серверов:** списки вроде `modelcontextprotocol/servers`,
`punkpeye/awesome-mcp-servers`, glama.ai, mcp.so. Стандартный путь — PR со строкой
описания в их README. Уточни у пользователя, нужно ли.
