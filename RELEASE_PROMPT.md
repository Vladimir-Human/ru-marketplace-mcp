# Задача: выпустить ru-marketplace-mcp v1.1.0 через Pull Request

Репозиторий уже опубликован, v1.0.0 в проде. Ты выпускаешь минорную версию:
проверяешь локально то, что нельзя было проверить из песочницы, открываешь PR,
дожидаешься зелёного CI и ставишь тег.

Код писать не нужно. Одно исключение — необязательное, оно в шаге 5.

**Корневая папка проекта:** `<ПУТЬ_К_ПАПКЕ>`
(если путь не подставлен, спроси у пользователя абсолютный путь и не начинай без него)

**Машина:** Windows / PowerShell (для Linux/macOS замены команд отмечены отдельно)

---

## Что нового в 1.1.0

22 инструмента вместо 20. Полный список изменений — в `CHANGELOG.md`, здесь только
то, что влияет на проверку.

| Пакет | Сервер | Инструментов | Изменилось |
|---|---|---|---|
| `wb-connector` | `wb-mcp` | 9 | +`wb_questions`, +`wb_category_products` |
| `ozon-connector` | `ozon-mcp` | 4 | кэш, `OZON_PROXY`, адаптер сравнения |
| `yandex-connector` | `yandex-mcp` | 3 | дедупликация выдачи |
| `detmir-connector` | `detmir-mcp` | 4 | параметр `region` у всех инструментов |
| `compare-connector` | `compare-mcp` | 2 | адаптеры на типизированных моделях |

Обратная совместимость: имена и сигнатуры двадцати инструментов 1.0.0 не менялись.
Существующие конфиги MCP-клиентов работают без правок. Транспорт по умолчанию
остался stdio.

## Что уже проверено (повторять не нужно)

- 406 офлайн-тестов проходят; ruff и ruff format чисты; mypy чист на host, win32 и
  darwin; stdout-guard чист
- Покрытие ветвей 74.58% при пороге 70% в CI
- Live selfcheck: WB, Яндекс Маркет, Детский мир → `success`
- Оба новых инструмента WB проверены на живых данных: `wb_questions` отдал 185
  вопросов на эталонном SKU и корректно обработал товар без вопросов;
  `wb_category_products` вернул 100 товаров и честно отказал на шарде `blackhole`
- Параметр `region` Детского мира проверен живьём: 152 магазина в Москве, 37 в
  Петербурге, 2 в Хабаровске на одном и том же товаре
- HTTP-транспорт проверен: `initialize` через `POST /mcp` отдаёт корректный ответ;
  stdio без переменных окружения работает как раньше
- Сериализация всех 37 моделей ответов побайтово совпадает с 1.0.0

## Что НЕ проверено и требует твоей верификации

Те же два ограничения среды, что и в прошлый раз, плюс одно новое:

1. **Ozon целиком** — датацентровый IP. Tier-1 отдаёт 307-петлю и затем 403 от
   анти-бота, Tier-2 недоступен без твоего залогиненного Chrome. Из-за этого не
   проверены и новые `OZON_CACHE_TTL` / `OZON_PROXY`.
2. **Нативный Windows** — покрыт юнит-тестами через `PLATFORM_OVERRIDE` и
   `mypy --platform win32`, но реальный прогон за тобой.
3. **Docker-образ не собирался.** В песочнице не было docker. Dockerfile проверен
   разбором: все пути `COPY` существуют, стадии сходятся, оба базовых тега
   существуют в реестрах (проверено запросом манифеста). Но `docker build` никто
   не запускал — это шаг 4.

---

## Шаг 1. Предусловия

```powershell
python --version   # нужен 3.12+
uv --version       # если нет: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git --version
gh --version
gh auth status
docker --version   # для шага 4; если docker нет, шаг 4 пропускается осознанно
```

Работаешь от свежего состояния `main`:

```powershell
git switch main
git pull --ff-only
git switch -c release/v1.1.0
```

## Шаг 2. Локальная верификация

Это твоя основная работа. Из корневой папки:

```powershell
uv sync --all-packages
uv run pytest -q                              # ожидаемо: 406 passed
uv run pytest -q -m "not live and not cdp" --cov --cov-fail-under=70
uv run ruff check .                           # ожидаемо: All checks passed!
uv run ruff format --check .                  # ожидаемо: N files already formatted
uv run mypy packages/*/src                    # ожидаемо: Success: no issues found
uv run mypy --platform win32 packages/*/src   # ловит ошибки, видимые только на Windows
uv run python scripts/check_no_print.py       # ожидаемо: no stdout writes
```

**Если любая команда падает, остановись и сообщи вывод.** Не открывай PR.

### 2.1 Нативный Windows

```powershell
uv run pytest packages/ozon-connector packages/mcp-core -q
uv run python -c "from mcp_core.process import taskkill_cmd; print(taskkill_cmd())"
```

Ожидаемо: путь вида `C:\Windows\System32\taskkill.exe`, именно с обратными слешами.
Прямые слеши или путь из переменной окружения — регрессия, сообщи.

Обрати внимание: тесты `mcp_core.process` в 1.1.0 переехали из набора Ozon в
`mcp-core`, поэтому в команде выше два пакета.

### 2.2 Версии консистентны

```powershell
uv run python -c "import mcp_core, wb_connector.server as w; print(mcp_core.__version__, w.SERVER_VERSION)"
```

Ожидаемо: `1.1.0 1.1.0`. Тесты `test_server_version_matches_pyproject` в каждом
пакете проверяют то же самое, но лишняя проверка перед тегом дешёвая.

### 2.3 Ozon Tier-1 с домашнего IP

```powershell
uv run python -c "import asyncio; from ozon_connector.server import ozon_selfcheck; print(asyncio.run(ozon_selfcheck()).status)"
```

- `success` — Tier-1 работает, CDP не нужен
- `inconclusive` — Tier-1 блокируется, переходи к 2.4
- `drift_detected` — Ozon изменил формат. **Блокер: сообщи и не выпускай**

### 2.4 Ozon Tier-2 (Chrome CDP)

```powershell
.\scripts\start_chrome_cdp.ps1
```

Залогинься **только в ozon.ru**. Профиль отдельный (`%LOCALAPPDATA%\Chrome-Scraping`).
Банк и почту туда не заводи: отдельный профиль и есть основная мера безопасности.

```powershell
Test-NetConnection 127.0.0.1 -Port 9222     # TcpTestSucceeded : True
uv run python -c "import asyncio; from ozon_connector.server import ozon_selfcheck; print(asyncio.run(ozon_selfcheck()).status)"
```

Модель угроз — `docs/CDP_SETUP.md`.

### 2.5 Кэш Ozon (новое в 1.1.0)

Проверяется только там, где Ozon вообще отвечает. Второй запрос того же товара
должен вернуться из кэша:

```powershell
uv run python -c @'
import asyncio, time
from ozon_connector.server import _fetch_composer
async def main():
    for i in (1, 2):
        t = time.monotonic()
        status, _, tier = await _fetch_composer("/product/3015796642/", None)
        print(f"call {i}: status={status} tier={tier} {time.monotonic()-t:.2f}s")
asyncio.run(main())
'@
```

Ожидаемо: первый вызов `tier=curl_cffi` или `tier=cdp`, второй `tier=cache` и
заметно быстрее. Если Ozon блокирован, оба вызова будут неуспешными — это не
регрессия кэша, а отсутствие того, что можно кэшировать: кэшируются только удачные
ответы, потому что запомненная блокировка неотличима от настоящей.

### 2.6 Новые инструменты WB на живых данных

```powershell
uv run python -c @'
import asyncio
from wb_connector.server import wb_questions, wb_categories, wb_category_products
async def main():
    q = await wb_questions(imt_id=1002173489, limit=3)
    print(f"questions: total={q.total_available} returned={q.returned} answered={q.answered_count}")
    cats = await wb_categories(root="Электроника", max_depth=1)
    node = next(n for n in cats.items if n.shard and n.shard != "blackhole")
    cp = await wb_category_products(shard=node.shard, query=node.query)
    print(f"category {node.name!r}: count={cp.count} has_more={cp.has_more}")
asyncio.run(main())
'@
```

Ожидаемо: у вопросов непустой `total_available` и `answered_count` больше нуля; у
категории `count=100`. Если WB отдаёт `rate_limited` — подожди минуту, он
ограничивает частые запросы.

### 2.7 Регион Детского мира в одной сессии

```powershell
uv run python -c @'
import asyncio
from detmir_connector.server import detmir_card
async def main():
    for reg in ("RU-MOW", "RU-SPE", "RU-KHA"):
        r = await detmir_card(product_id=7081792, region=reg)
        print(f"{reg}: stores={r.product.store_count} price={r.product.price_rub}")
asyncio.run(main())
'@
```

Ожидаемо: `store_count` **разный** по городам. Если везде 0 — вернулась ровно та
ошибка, которую 1.1.0 исправляет: сообщи и не выпускай.

### 2.8 Полное живое сравнение цен

```powershell
uv run python examples/health_check.py
uv run python examples/price_check.py "стиральная машина узкая"
```

С российского IP и настроенным CDP ожидаются все четыре `success` и `Complete: True`.
Отдельно посмотри на предложения Ozon: в 1.1.0 переписан его адаптер, и до этого он
не мог вернуть ни одной цены. Если Ozon отвечает, у его предложений должны быть
заполнены `price_rub` и `rating_count`. Зафиксируй фактический результат.

### 2.9 HTTP-транспорт (новое в 1.1.0, необязательно)

```powershell
$env:MCP_TRANSPORT="http"; $env:MCP_HTTP_PORT="8765"
Start-Process -NoNewWindow uv -ArgumentList "run","wb-mcp"
Start-Sleep 5
curl.exe -s -i -X POST http://127.0.0.1:8765/mcp `
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" `
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
```

Ожидаемо: `200 OK`, `content-type: text/event-stream`, в теле `serverInfo.name` =
`wb-connector`. Останови процесс и **сними переменные окружения**, иначе следующие
шаги пойдут по HTTP:

```powershell
Get-Process uv | Stop-Process
Remove-Item Env:MCP_TRANSPORT, Env:MCP_HTTP_PORT
```

Подробности и предупреждения по безопасности — `docs/DEPLOYMENT.md`.

## Шаг 3. Проверь, что попадает в коммит

```powershell
git status --short          # .venv, __pycache__, Chrome-Scraping попасть НЕ должны
git diff --stat main
```

`uv.lock` **должен** быть в коммите: в 1.1.0 в dev-группу добавились `pytest-cov` и
`truststore`.

## Шаг 4. Docker-образ (если docker есть)

Единственное, что вообще не запускалось. Если docker недоступен — пропусти шаг и
скажи об этом в отчёте, релиз это не блокирует.

```powershell
docker build -t ru-marketplace-mcp:1.1.0 .
docker run --rm -e MCP_TRANSPORT=http -e MCP_HTTP_HOST=0.0.0.0 -p 127.0.0.1:8000:8000 ru-marketplace-mcp:1.1.0 wb-mcp
```

В другом окне повтори `curl` из шага 2.9 против порта 8000.

Если сборка падает, сообщи вывод — правки Dockerfile это допустимая работа по коду.
Помни ограничение: Tier-2 Ozon в контейнере не работает, потому что CDP-клиент
жёстко обращается к `127.0.0.1`, а внутри контейнера это сам контейнер.
`docs/DEPLOYMENT.md` описывает вариант с `network_mode: host`.

## Шаг 5. Необязательно: спайк реквизитов продавца Ozon

Единственная задача из плана 1.1.0, которую не удалось закрыть, и единственное
место, где можно писать код. Делай только если Ozon у тебя отвечает (шаг 2.3 или
2.4 дал `success`).

Что известно: путь `/seller/{slug}-{id}/` структурно верный, id продавца уже
приходит в `ozon_card` как `seller.link`. С датацентрового IP каждый запрос
заканчивался 403 от анти-бота — причём **уже работающий** путь `/product/{id}/`
падал точно так же, что и доказывает: блокируют IP, а не адрес.

Чего не известно: как в ответе лежат название юрлица, ОГРН и ИНН. Ни одного
успешного ответа получено не было, поэтому пути к полям не выдуманы, а просто
отсутствуют.

```powershell
uv run python -c @'
import asyncio, json
from ozon_connector.server import _fetch_composer, ozon_card
async def main():
    card = await ozon_card(sku_or_path="3015796642")
    link = getattr(card.seller, "link", None) if card.seller else None
    print("seller.link:", link)
    if not link:
        print("в карточке нет ссылки на продавца — сообщи это")
        return
    status, body, tier = await _fetch_composer(link, None)
    print("status:", status, "tier:", tier, "len:", len(body))
    if status == 200:
        states = json.loads(body).get("widgetStates", {})
        hits = [k for k in states if any(w in k.lower() for w in ("seller", "legal", "company", "requisite"))]
        print("подходящие виджеты:", hits)
        open("ozon_seller_sample.json", "w", encoding="utf-8").write(body)
        print("ответ сохранён в ozon_seller_sample.json")
asyncio.run(main())
'@
```

Если получишь 200: **не пиши инструмент на глазок**. Приложи
`ozon_seller_sample.json` к отчёту или к issue вместе со списком найденных виджетов.
Инструмент, уверенно возвращающий название чужого юрлица, хуже отсутствующего —
реквизиты смотрят ровно для того, чтобы отличить официальный магазин от похожего
перекупщика. Это тот же урок, что с поиском Детского мира: `docs/ANTI_BOT.md`.

Если снова 403 — так и запиши. Отрицательный результат с твоего IP тоже полезен.

## Шаг 6. Коммит и Pull Request

```powershell
git add -A
git commit -m "release: v1.1.0

22 tools across 5 stdio servers, up from 20. Technical debt paid down,
four functional gaps closed, two Ozon-path bugs fixed.

Backward compatible: the 20 v1.0.0 tool names and signatures are
unchanged, and stdio remains the default transport.

See CHANGELOG.md for the full notes."

git push -u origin release/v1.1.0
gh pr create --base main --head release/v1.1.0 --title "release: v1.1.0" --body-file PR_BODY.md
```

Где `PR_BODY.md` — временный файл (не коммить его):

```markdown
## Что здесь

Версия 1.1.0: технический долг, четыре закрытые дыры в функциональности, два
исправленных бага и два новых инструмента Wildberries. Полные заметки — в
`CHANGELOG.md`.

Обратная совместимость: имена и сигнатуры двадцати инструментов 1.0.0 не менялись,
транспорт по умолчанию остался stdio.

## Локальная верификация

- [ ] 406 тестов зелёные на нативном Windows
- [ ] ruff, ruff format, mypy (host + win32), stdout-guard чисты
- [ ] Покрытие выше порога 70%
- [ ] `taskkill` резолвится в `C:\Windows\System32\taskkill.exe`
- [ ] Ozon selfcheck: <результат и tier>
- [ ] Новые инструменты WB на живых данных: <результат>
- [ ] `store_count` Детского мира различается по городам: <результат>
- [ ] `health_check.py`: <сколько success>
- [ ] Docker-образ собран: <да / нет, docker недоступен>
```

## Шаг 7. Дождись зелёного CI

```powershell
gh pr checks --watch
```

CI прогоняет lint, mypy и тесты на Ubuntu/Windows/macOS × Python 3.12/3.13, плюс
отдельную job с порогом покрытия. Live- и CDP-тесты исключены намеренно: у CI нет
ни российского IP, ни браузера с логином.

**Если CI красный:**
- падение на конкретной ОС → `gh run view --log-failed`, сообщи вывод
- падение порога покрытия → сообщи фактический процент, **не понижай порог сам**
- ошибка сети до маркетплейса → значит какой-то тест тянет сеть, сообщи какой
- **не мержи и не ставь тег при красном CI**

## Шаг 8. Мерж и тег

Только после зелёного CI:

```powershell
gh pr merge --squash --delete-branch
git switch main
git pull --ff-only

git tag -a v1.1.0 -m "ru-marketplace-mcp v1.1.0"
git push origin v1.1.0
```

Тег `v*` запускает релизный workflow: он собирает wheels и sdist всех шести пакетов
и прикладывает их к GitHub Release. Проверь:

```powershell
gh run watch
gh release view v1.1.0
```

Если workflow не создал релиз автоматически, создай вручную и приложи артефакты из
`dist/`:

```powershell
uv build --all-packages
gh release create v1.1.0 --title "v1.1.0 — new tools, paid-down debt" --notes-file RELEASE_NOTES.md dist/*
```

Черновик заметок (в `RELEASE_NOTES.md`, тоже не коммить):

```markdown
22 tools across 5 stdio MCP servers, up from 20. Read-only, no credentials.

Backward compatible: every v1.0.0 tool name and signature is unchanged, and stdio
remains the default transport, so existing MCP client configs keep working.

## New

- `wb_questions` — buyer questions with seller answers. Reviews say what owning the
  product is like; questions clarify what it actually is, and the seller's reply is
  often the only public statement of a fact the listing omits.
- `wb_category_products` — the products behind the shard/query selectors
  `wb_categories` already returned and nothing consumed.
- Per-call `region` on every Detsky Mir tool, so one session can compare cities.
- Optional HTTP transport, a Docker image, and a `server.json` registry manifest.
- `WB_CACHE_TTL`, `WB_PROXY`, `OZON_CACHE_TTL`, `OZON_PROXY` — the docs promised
  `*_PROXY` everywhere while only two connectors had it.

## Fixed

- `detmir_card` sent no region filter at all while labelling responses with the
  configured region, so offline store counts were always 0.
- The compare connector's Ozon adapter read six field names the model does not
  declare, and would have failed validation on the ones it did hit.
- Duplicate products in Yandex search results.

## Quality

406 offline tests (up from 221), 74% branch coverage enforced in CI, stricter mypy
for the shared runtime, Dependabot, and a tag-triggered release workflow.

Full notes: CHANGELOG.md
```

## Шаг 9. Финальная проверка «как у пользователя»

```powershell
cd $env:TEMP
git clone https://github.com/<OWNER>/ru-marketplace-mcp.git fresh-check
cd fresh-check
uv sync --all-packages
uv run pytest -q -m "not live"
uv run python -c "import asyncio; from wb_connector.server import wb_selfcheck; print(asyncio.run(wb_selfcheck()).status)"
```

Ожидаемо: тесты зелёные, selfcheck `success`.

## Шаг 10. Обнови подключённый MCP-клиент

Если серверы уже подключены к твоему Claude Desktop / Claude Code / Cursor,
перезапусти клиент и попроси агента вызвать `wb_questions` и
`wb_category_products` — они появятся только после перезапуска. Конфиг менять не
нужно: команды запуска не изменились.

## Критерии успеха

- 406 тестов зелёные локально **на Windows**
- ruff, ruff format, mypy (host + win32), stdout-guard — чисто
- Покрытие выше 70%
- Версии `1.1.0` согласованы (шаг 2.2)
- `store_count` Детского мира различается по городам
- Ozon selfcheck `success` (любой tier); `inconclusive` без CDP допустимо,
  `drift_detected` — блокер
- CI зелёный на всех шести комбинациях ОС × Python
- PR смержен, тег `v1.1.0` и Release опубликованы, wheels приложены
- Свежий клон ставится и проходит тесты

## Не делай

- **Не запускай `uv run wb-mcp` в интерактивном терминале** без `MCP_TRANSPORT=http`
  — в stdio-режиме сервер повиснет в ожидании JSON-RPC на stdin.
- **Не мержи и не тегируй при красном CI.**
- **Не понижай порог покрытия**, чтобы сборка позеленела. Сообщи фактический процент.
- **Не переименовывай и не меняй сигнатуры существующих инструментов.** На них
  завязаны конфиги пользователей; 1.1.0 — только добавления.
- **Не делай HTTP-транспорт транспортом по умолчанию.** stdio по умолчанию — это то,
  что сохраняет работоспособность существующих конфигов.
- **Не биндь HTTP на `0.0.0.0`** без реверс-прокси с аутентификацией. У сервера нет
  своей аутентификации: порт и есть весь периметр.
- **Не пиши `ozon_seller` по догадке.** Если шаг 5 не дал 200 с реальными полями,
  инструмента быть не должно.
- **Не коммить `.venv`, `__pycache__`, `Chrome-Scraping`, `ozon_seller_sample.json`,
  `PR_BODY.md`, `RELEASE_NOTES.md`.**
- **Не удаляй `uv.lock`.**
- **Не логинься в CDP-профиль ничем, кроме маркетплейсов.**
- **Не убирай задержки между запросами** (`*_MIN_GAP`). Это вежливость к чужой
  инфраструктуре и защита от бана.
- **Не «исправляй» отсутствие поиска у Детского мира.** Инструмент там был написан,
  проверен живьём (на «лего» вернулись подгузники и коллаген) и удалён намеренно.
  Подробности — `docs/ANTI_BOT.md`.

## Отчёт

По завершении сообщи:

1. Результаты шага 2 по пунктам: тесты, покрытие, линтеры, Windows-пути, версии
2. Вердикт Ozon selfcheck и какой tier сработал; настраивал ли CDP
3. Результат кэша Ozon (шаг 2.5): появился ли `tier=cache` на втором вызове
4. Что вернули новые инструменты WB (шаг 2.6)
5. Различались ли `store_count` по городам (шаг 2.7) — это проверка исправленного бага
6. Вывод `health_check.py` и `price_check.py`; были ли у предложений Ozon цены
7. Собрался ли Docker-образ, или docker недоступен
8. Итог спайка Ozon seller (шаг 5), если делал: 403 или 200 плюс список виджетов
9. URL PR и релиза, статус CI по каждой комбинации ОС × Python
10. Любые отклонения от инструкции и как ты их решил

## Опционально, после релиза

**PyPI.** Пакеты собираемы, релизный workflow уже кладёт wheels в Release, но в PyPI
ничего не публикуется намеренно: имена `wb-connector` и подобные слишком общие и
почти наверняка заняты. Нужно решение по префиксу (например `ru-mcp-wb`), проверка
занятости имён и `uv publish` с токеном. Отдельная задача, не часть релиза.

**Реестр MCP.** В корне лежит `server.json` по официальной схеме (`2025-12-11`).
Подача в реестр и в каталоги вроде `punkpeye/awesome-mcp-servers`, glama.ai, mcp.so
— разовое действие после релиза. Уточни у пользователя, нужно ли.
