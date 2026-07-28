# Задача: выпустить ru-marketplace-mcp v1.2.0 через Pull Request

Репозиторий уже опубликован, v1.1.0 в проде. Ты выпускаешь минорную версию:
проверяешь локально то, что нельзя было проверить из песочницы, открываешь PR,
дожидаешься зелёного CI и ставишь тег.

Код писать не нужно, кроме отмеченных мест: спайк ozon_seller, селекторы DNS и
Ситилинка, если их selfcheck покажет дрейф, и Dockerfile, если сборка образа
упадёт на чём-то новом. Слой зависимостей в нём уже починен и проверен.

**Корневая папка проекта:** `<ПУТЬ_К_ПАПКЕ>`
(если путь не подставлен, спроси у пользователя абсолютный путь и не начинай без него)

**Машина:** Windows / PowerShell (для Linux/macOS замены команд отмечены отдельно)

---

## Что нового в 1.2.0

41 инструмент в 11 серверах вместо 22 в 5, плюс `marketplace_sources` в
объединённом сервере — в нём 42. Полный список изменений — в
`CHANGELOG.md`, здесь только то, что влияет на проверку.

| Пакет | Сервер | Инструментов | Изменилось |
|---|---|---|---|
| `avito-connector` | `avito-mcp` | 4 | новый: поиск, карточка, продавец |
| `taobao-connector` | `taobao-mcp` | 3 | новый: поиск и карточка, цены в юанях |
| `megamarket-connector` | `megamarket-mcp` | 3 | новый: мобильный API через CDP |
| `lamoda-connector` | `lamoda-mcp` | 3 | новый: карточки GraphQL, поиск CDP |
| `dns-connector` | `dns-mcp` | 3 | новый: DOM через CDP (Qrator) |
| `citilink-connector` | `citilink-mcp` | 3 | новый: DOM через CDP (Qrator) |
| `marketplace-connector` | `marketplace-mcp` | 42 | объединённый сервер (41 монтируется + `marketplace_sources`) + CLI install/doctor |
| `mcp-core` | — | — | `CHROME_CDP_HOST`, `probe_session()` |
| `compare-connector` | `compare-mcp` | 2 | адаптеры Avito и Taobao |

Обратная совместимость: имена и сигнатуры двадцати двух инструментов 1.1.0 не
менялись. Существующие конфиги MCP-клиентов работают без правок. Транспорт по
умолчанию остался stdio.

## Что уже проверено (повторять не нужно)

Перед этим релизом прошёл независимый аудит. Он не доверял прежним статусам и
перепроверял всё сам, так что список ниже — воспроизведённые факты, а не
пересказ прошлых отчётов.

- 726 офлайн-тестов проходят за ~8 секунд, покрытие выше порога 70
- ruff и ruff format чисты; mypy чист на host, win32 и darwin
- `uv lock --check` чист; сборка даёт 26 артефактов (13 wheel и 13 sdist)
- Все двенадцать серверов проходят настоящую MCP-сессию по stdio: спавн
  консольной команды, `initialize`, `tools/list`, `tools/call`
- Чистая установка wheel `marketplace-connector` поднимает 11 источников и 42
  инструмента
- Слой зависимостей Dockerfile воспроизведён отдельно и ставит все 13 пакетов
- Контрактные тесты пинают инварианты (None-not-zero, файрвол ≠ данные)
- Документация сверена с кодом: счётчики, список инструментов, переменные
  окружения, имена консольных команд

## Что НЕ проверено и требует твоей верификации

Ограничение среды одно, но оно решающее: **у песочницы не было ни российского
IP, ни Chrome с CDP, и ни один запрос к площадкам не отправлялся.** Всё, что
касается живых данных, помечено INCONCLUSIVE и намеренно не засчитано за
успех. Закрыть это можешь только ты.

1. **Ни один из десяти маркетплейсов не опрошен вживую.** Семь из них
   (Ozon, Авито, Taobao, Мегамаркет, Lamoda, DNS, Ситилинк) читают через
   залогиненный Chrome. Их парсеры написаны по задокументированным формам
   ответов и подтверждены фикстурами — то есть подтверждён парсер, а не
   источник. Твоя машина первая, где они могут отдать реальный каталог.
   Это основная работа шага 2 и главный критерий go/no-go.
2. **Селекторы DNS и Ситилинка исправлены по живой сессии** (реальные id
   оказались слагом с числом и 16-hex, а не 24-hex), но подтверждены одним
   прогоном на одной машине. Прогони их снова у себя.
3. **Нативный Windows** — покрыт юнит-тестами и `mypy --platform win32`, но
   реальный прогон за тобой.
4. **Docker-образ целиком не собирался** — в песочнице нет docker. Проверен
   только тот шаг, который раньше падал. Шаг 4.

## Что изменил аудит (влияет на то, что ты увидишь)

Не косметика — это меняет поведение, и если ты ждёшь старого, решишь, что
что-то сломано.

- **Появился инструмент `marketplace_sources`** в объединённом сервере: он
  показывает, что смонтировано и что пропущено с какой ошибкой. Поэтому в
  `marketplace-mcp` теперь 42 инструмента, а не 41.
- **`doctor` возвращает три разных кода:** `0` — всё здорово, `1` — дрейф
  парсера, `2` — проверить не удалось (блок, нет CDP, не тот регион). Раньше
  «ничего не проверили» возвращало `0`. Для тебя это значит: **двойка не
  блокер, единица блокер.**
- **`install` печатает реальный путь** к твоему checkout, а не заглушку
  `/path/to/...`. Подставлять руками больше нечего.
- **Мегамаркет теперь падает громко.** Ответ, разобранный в ноль товаров,
  поднимает `parser_drift` вместо тихого успеха. Если увидишь его на
  Мегамаркете — это работает как задумано, смотри реальный ответ.
- **У предложений в `compare_prices` появились `currency` и `price_native`.**
  Цена Taobao в юанях теперь доезжает до ответа; в ранжирование она
  по-прежнему не входит, и в `warnings` появляется строка `foreign_currency`.
- **Шесть коннекторов раньше представлялись версией `3.4.4`** (это версия
  FastMCP). Теперь все двенадцать отдают `1.2.0` — увидишь в шаге 2.0.
- **Закрыт SSRF** в `citilink_card` и `dns_card`: карточка теперь навигирует
  URL, собранный из своего домена, а не присланную строку. Если передашь
  ссылку на чужой хост, инструмент откажет — так и должно быть.
- **Запросы к одному источнику теперь разнесены во времени, и после отказа
  пауза удлиняется.** Восемь копий `_polite_wait` заменены общим `Pacer` в
  `mcp-core`. Это прямой ответ на то, что у тебя Taobao и DNS развалились
  после серии запросов подряд: раньше код не отличал успех от отказа и
  продолжал долбить в том же темпе. После нескольких отказов подряд в тексте
  ошибки появляется прямая рекомендация сменить адрес или перелогиниться.
- **Мегамаркет отличает разлогин от отсутствия товара.** Пустой `items` при
  пройденном ServicePipe теперь `inconclusive (not_authenticated)`, а не
  `drift`. Это меняет то, что ты увидишь в `doctor`: не «сломался парсер», а
  «залогинься в профиле». Пропавший массив по-прежнему `parser_drift`.

## Что аудит намеренно не чинил

Это решения, а не забытые пункты. Не «исправляй» их походя, не разобравшись.

- **`WbCardItem.in_stock` остался `bool`.** Когда Wildberries не сообщает
  количество, выходит `False`, то есть «нет в наличии», хотя правильный ответ
  «неизвестно». Правильный тип `bool | None`, но это ломает форму ответа
  инструмента из 1.1.0, а правило проекта разрешает такое только в мажорной
  версии. Пока добавлено предупреждение `stock_unknown` и в описании поля
  сказано читать `total_quantity is None`. **Смена типа — в 2.0, не сейчас.**
- **DNS и Ситилинк не отличают пустую выдачу от уехавшего DOM.** Оба поднимают
  `parser_drift` на нуле плиток, так что честный пустой поиск выглядит ошибкой.
  Чинить вслепую нельзя: нужно увидеть, какой разметкой площадки показывают
  «ничего не найдено», а это видно только из прошедшей proof-of-work сессии.
  **Если у тебя такая сессия есть — посмотри и почини, это твой шанс.**
- **Релевантность предупреждает, но не фильтрует.** `compare_prices` теперь
  говорит, когда самое дешёвое предложение похоже на аксессуар или стоит вдвое
  ниже медианы, — но всё равно его ранжирует. Отбрасывать строку по эвристике,
  подобранной на одном живом прогоне, значит рисковать спрятать настоящую
  выгоду. **Читай `warnings` и проверяй помеченное глазами.**
- **Ночная канарейка покрывает 3 источника из 11.** Маркер `live` есть только у
  Wildberries, Детского мира и Яндекса. Job больше не зелёный по умолчанию и
  печатает «3 из 11» в summary, так что цифра хотя бы видна.

## Что осталось только для твоей машины

Три вещи починены по схеме работающих реализаций, но живьём из песочницы не
проверяемы. Прогони и подтверди либо опровергни.

1. **Мегамаркет: поиск.** Тело запроса переписано целиком (`searchText`,
   `requestVersion: 10`, `limit`/`offset`), парсер читает вложенную схему
   (`goods`, `favoriteOffer`). Ожидание: `megamarket_selfcheck` даёт `success` с
   разобранными товарами. Если снова пусто — сними DevTools → Network с живой
   страницы поиска и сверь тело один в один: `diagnose_drift.py megamarket`
   печатает форму ответа без значений.
2. **Мегамаркет: карточка.** Endpoint сменён на `productCardMainInfo/get` с
   телом `{goodsId, merchantId: "0"}`. Форму ответа никто не видел — если
   карточка отдаст `parser_drift`, пришли форму из зонда, поправлю парсер.
3. **Lamoda: `card_graphql`.** В запрос вернулось published-имя
   `old_price_amount` вместо `old_price`, и блок `errors` теперь читается. Если
   снова inconclusive — в тексте ошибки будет дословный ответ сервера, он и
   скажет, какое поле не так.

Плюс сверка глазами по восьми здоровым источникам: два-три товара на источник,
сайт рядом, сравнить цену, наличие и продавца. Это единственное, что превращает
«парсер что-то вернул» в «парсер вернул правду», и его нельзя делегировать
обратно мне.

## Критерии go / no-go

Не выпускай по ощущению «вроде работает». Проверь по списку.

**Go** — всё сразу:
- офлайн-гейт зелёный, 726 тестов, покрытие выше 70;
- `e2e_stdio_check.py` даёт 12/12, везде `v1.2.0`;
- `doctor` вернул `0` или `2` с понятным объяснением по каждому непроверенному
  источнику;
- по каждому источнику, который ответил, сверка глазами сошлась по цене и
  наличию;
- юани Taobao не выиграли ранжирование;
- CI зелёный.

**Conditional go** — то же самое, но часть источников осталась непроверенной или
дрейфует. Тогда выпускай, **явно пометив их** `experimental` в README и
CHANGELOG, и убери из списка тех, про кого написано «работает». Непроверенный
источник под ярлыком рабочего — это и есть то, что аудит считает враньём.

**No-go** — достаточно одного:
- цена, валюта, наличие или продавец не совпали с сайтом;
- `doctor` вернул `1`;
- сравнение назвало полным то, где источник молча выпал;
- юаневая цена попала в рублёвое ранжирование;
- чистая установка, сборка образа или CI красные.

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
git switch -c release/v1.2.0
```

## Шаг 2. Локальная верификация

Из корневой папки:

```powershell
uv lock --check                                  # локфайл не разошёлся с манифестами
uv sync --frozen --all-packages
uv run pytest -q -m "not live and not cdp"      # ожидаемо: 726 passed
uv run pytest -q -m "not live and not cdp" --cov --cov-fail-under=70   # порог 70
uv run ruff check .                              # ожидаемо: All checks passed!
uv run ruff format --check .                     # ожидаемо: N files already formatted
uv run mypy packages/*/src                       # ожидаемо: Success: no issues found
uv run mypy --platform win32 packages/*/src      # ловит ошибки, видимые только на Windows
uv run python scripts/check_no_print.py          # ожидаемо: no stdout writes
```

**Если любая команда падает, остановись и сообщи вывод.** Не открывай PR.

### 2.0 Живая MCP-сессия по stdio

Делай это до всего остального, что требует сети: шаг ничего не грузит из
интернета и отвечает на вопрос «серверы вообще стартуют и говорят по
протоколу», отдельно от вопроса «площадки отвечают».

```powershell
uv run python scripts/e2e_stdio_check.py
```

Ожидаемо: `12/12 servers completed a real MCP session`, и у каждого сервера в
строке стоит `v1.2.0`. Если увидишь `v3.4.4` — коннектор не передал свою
версию в `FastMCP`, это регрессия правки из аудита.

Проверка не заменяется прогоном тестов: `list_tools()` внутри того же процесса
не читает `serverInfo` и не запускает консольную команду, поэтому именно этот
шаг поймал шесть серверов с чужой версией.

### 2.1 Нативный Windows

```powershell
uv run pytest packages/mcp-core packages/ozon-connector -q
uv run python -c "from mcp_core.process import taskkill_cmd; print(taskkill_cmd())"
```

Ожидаемо: путь вида `C:\Windows\System32\taskkill.exe`, именно с обратными
слешами. Прямые слеши или путь из переменной окружения — регрессия, сообщи.

### 2.2 Версии консистентны

```powershell
uv run python -c "import avito_connector, taobao_connector, megamarket_connector; print(avito_connector.__version__, taobao_connector.__version__, megamarket_connector.__version__)"
```

Ожидаемо: `1.2.0 1.2.0 1.2.0`. Корневой `pyproject.toml` и `server.json` тоже
должны говорить `1.2.0` — если в них ещё `1.1.0`, это допустимая правка по коду.

### 2.3 Существующие источники с домашнего IP

```powershell
uv run python -c "import asyncio; from wb_connector.server import wb_selfcheck; print(asyncio.run(wb_selfcheck()).status)"
uv run python -c "import asyncio; from yandex_connector.server import yandex_selfcheck; print(asyncio.run(yandex_selfcheck()).status)"
uv run python -c "import asyncio; from detmir_connector.server import detmir_selfcheck; print(asyncio.run(detmir_selfcheck()).status)"
```

Ожидаемо: `success`. `drift_detected` у любого — **блокер, сообщи и не выпускай**.

### 2.4 Запусти Chrome CDP и залогинься в площадки

```powershell
.\scripts\start_chrome_cdp.ps1
Test-NetConnection 127.0.0.1 -Port 9222     # TcpTestSucceeded : True
```

В открывшемся Chrome (профиль `%LOCALAPPDATA%\Chrome-Scraping`) залогинься
**только в нужных площадках**: ozon.ru, avito.ru, taobao.com, megamarket.ru,
lamoda.ru, dns-shop.ru, citilink.ru. Банк и почту туда не заводи. Модель угроз —
`docs/CDP_SETUP.md`.

### 2.5 Проверь каждый новый источник через его selfcheck

Это сердце релиза 1.2.0. Запусти selfcheck каждого нового коннектора:

```powershell
uv run python -c "import asyncio; from avito_connector.server import avito_selfcheck; print(asyncio.run(avito_selfcheck()).status)"
uv run python -c "import asyncio; from taobao_connector.server import taobao_selfcheck; print(asyncio.run(taobao_selfcheck()).status)"
uv run python -c "import asyncio; from megamarket_connector.server import megamarket_selfcheck; print(asyncio.run(megamarket_selfcheck()).status)"
uv run python -c "import asyncio; from lamoda_connector.server import lamoda_selfcheck; print(asyncio.run(lamoda_selfcheck()).status)"
uv run python -c "import asyncio; from dns_connector.server import dns_selfcheck; print(asyncio.run(dns_selfcheck()).status)"
uv run python -c "import asyncio; from citilink_connector.server import citilink_selfcheck; print(asyncio.run(citilink_selfcheck()).status)"
```

Читай вердикт так:
- `success` — источник работает, формат подтверждён. Зафиксируй.
- `inconclusive` — транспорт/сессия: источник заблокирован с твоего IP или
  Chrome не залогинен. Это ожидаемо для части источников и **не блокер**, но
  запиши, какой именно и почему.
- `drift_detected` — страница отрисовалась, но парсер её не понял. **Блокер**:
  формат сместился с момента написания. Сообщи, какой источник и что в
  `checks[*].notes`, и не выпускай этот источник.

Проверка `marketplace-mcp doctor` запускает все selfcheck разом плюс пробу
CDP-сессии — удобный способ увидеть всю картину одной командой:

```powershell
uv run marketplace-mcp doctor --status-file doctor-status.json
echo $LASTEXITCODE
```

Код возврата — это и есть вердикт, читай его буквально:

| Код | Что значит | Что делать |
|---|---|---|
| `0` | всё, что удалось проверить, здорово | можно выпускать |
| `1` | дрейф парсера хотя бы на одном источнике | **блокер**, чинить до релиза |
| `2` | дрейфа нет, но часть источников проверить не удалось | не блокер, но записать какие и почему |

Двойка — нормальный результат для первого прогона: она значит «источник
заблокирован, Chrome не залогинен или регион не тот», а не «сломано». Ноль при
непроверенных источниках был бы враньём, поэтому его больше не бывает.

Приложи `doctor-status.json` к отчёту.

### 2.5a Если selfcheck показал drift_detected

Не чини селектор по догадке. `drift_detected` значит только «страница
загрузилась, парсер её не понял», а причин три, и лечатся они по-разному:
стена челленджа, товары подгружаются позже нашего окна ожидания, либо реально
уехал шаблон ссылок. Спроси у страницы:

```powershell
uv run python scripts/diagnose_drift.py all
```

Скрипт открывает поисковую страницу в том же Chrome и возвращает структуру:
сколько на ней ссылок, сколько из них попадают в ожидаемый шаблон, какие
маршруты реально встречаются, какие классы повторяются чаще всего и нет ли
маркеров челленджа. Текст страницы он не читает — профиль залогинен, и
диагностике там делать нечего.

Вердикты и что они значат:

| Вердикт | Что делать |
|---|---|
| `SELECTOR MOVED` | реальный дрейф. Верхний маршрут в списке — новый шаблон ссылки. Правь `_SEARCH_EXTRACT_JS`, добавляй фикстуру |
| `WALL` | челлендж не пройден. Открой сайт руками в этом профиле, дождись прохода, повтори |
| `LOGIN` | сайту нужна сессия. Залогинься в профиле |
| `EMPTY` | площадка честно ничего не нашла. Смени запрос — селекторы ни при чём |
| `NOT DRIFT` | шаблон совпадает. Дело в теле JS, окне ожидания или лимите размера |

`EMPTY` на DNS или Ситилинке — это как раз известный дефект: они не отличают
пустую выдачу от уехавшего DOM. Если увидишь его, почини именно это, а не
селектор.

Ещё два вердикта появились после первого прогона:

| Вердикт | Что делать |
|---|---|
| `DATA NOT IN DOM` | товары на странице есть, но лежат в JSON-состоянии, а не в ссылках. Селектор искать бессмысленно — читай состояние или API за ним. Это случай Lamoda |
| `SHAPE MOVED` | только для Мегамаркета: ответ API разобран, но массива товаров под известными ключами нет. В выводе есть форма ответа — найди в ней массив и добавь ключ в `_parse_items` |

Мегамаркет отвечает JSON, а не HTML, поэтому у него отдельный зонд. Он ходит
тем же транспортом, что и коннектор, и печатает форму ответа без значений:

```powershell
uv run python scripts/diagnose_drift.py megamarket
```

Он сразу отделяет отказ ServicePipe по IP (code 7) от переименованного поля —
это разные болезни, и вторая лечится одной строкой в `_parse_items`.

### 2.5b Если источник inconclusive

`doctor` теперь печатает причину, а не только состояние:

```
avito  inconclusive  search:inconclusive (rate_limited http 429), seller:healthy
```

`rate_limited` — подожди 10–15 минут и повтори, это не блок. `blocked` с 403 —
IP не подходит площадке. `transport_down` — не поднялся Chrome или сеть.
Раньше все три печатались одним словом `inconclusive`, и отличить их было
нельзя.

### 2.6 Живые вызовы новых источников

Для каждого источника, чей selfcheck дал `success`, сделай один реальный
вызов, чтобы убедиться, что парсятся не только канарейки:

```powershell
uv run python -c @'
import asyncio
from avito_connector.server import avito_search
async def main():
    r = await avito_search("ноутбук lenovo", page=1)
    print(f"avito: {r.count} items, total={r.total_count}, tier={r.tier_used}")
    for it in r.items[:3]:
        print(f"  {it.price_rub} ₽  {it.title}  — {it.seller_name}")
asyncio.run(main())
'@
```

Повтори по аналогии для taobao (цены в юанях!), megamarket, lamoda, dns,
citilink. Если источник отвечает, у его предложений должны быть заполнены цена
(или честный `None` для бесценных) и заголовок. Зафиксируй фактический результат.

### 2.6a Сверка глазами — единственное, что отличает данные от правды

Тест проверяет, что парсер что-то вернул. Он не проверяет, что вернулось
верное. Это делаешь только ты и только руками.

По каждому источнику, который ответил, возьми **два-три товара** и открой их на
сайте в обычном браузере рядом. Сравни:

- **цену** — ту, что видит незалогиненный покупатель, без карты площадки и без
  подписки. Если инструмент отдаёт цену со скидкой по карте как обычную, это
  блокер: в `compare_prices` она обойдёт честные цены конкурентов;
- **наличие** — товар в наличии на сайте должен приходить с `in_stock: true`, а
  не с `false` из-за неразобранного количества;
- **продавца** — для Авито и Wildberries это отдельный сигнал доверия, и
  подставленное не то имя хуже пустого;
- **валюту Taobao** — в `price_native` должны быть юани, а `price_rub`
  обязан остаться `null`.

Расхождение хотя бы по цене или наличию — блокер для этого источника. Выпускай
остальные, а этот честно пометь `experimental` в README, но не оставляй его в
списке рабочих.

Отдельно прогони запрос, по которому найдётся и российский товар, и Taobao:

```powershell
uv run python examples/compare_with_china.py "iphone 15"
```

Юаневая строка не должна выиграть ранжирование и не должна попасть в
`cheapest`, а в `warnings` обязана появиться строка `foreign_currency`. Если
юани выиграли — это P0, останавливай релиз.

### 2.7 Полное живое сравнение цен

```powershell
uv run python examples/health_check.py
uv run python examples/price_check.py "стиральная машина узкая"
uv run python examples/compare_with_china.py "iphone 15"
```

С российского IP и настроенным CDP ожидается максимум `success` и
`Complete: True`. В `compare_with_china` рублёвые предложения ранжируются, а
Taobao отчитывается отдельно (юани не в ранжировании). Зафиксируй, сколько
источников ответило и какие оказались заблокированы.

### 2.8 Объединённый сервер

```powershell
uv run python -c @'
import asyncio
from marketplace_connector.server import mcp
async def main():
    tools = await mcp.list_tools()
    print(f"{len(tools)} tools mounted")
asyncio.run(main())
'@
```

Ожидаемо: `42 tools mounted` — 41 смонтированный плюс собственный
`marketplace_sources`.

Если меньше, не гадай по stderr, спроси сам сервер:

```powershell
uv run python -c @'
import asyncio
from marketplace_connector.server import marketplace_sources
r = asyncio.run(marketplace_sources())
print(f"смонтировано {r.mounted_count}: {r.mounted}")
print(f"пропущено {r.skipped_count}: {r.skipped}")
'@
```

`skipped` покажет имя источника и ошибку импорта, которая его убрала — почти
всегда это недостающая зависимость. Ожидаемо: пропущено 0.

## Шаг 3. Проверь, что попадает в коммит

```powershell
git status --short          # .venv, __pycache__, Chrome-Scraping, doctor-status.json попасть НЕ должны
git diff --stat main
```

`uv.lock` **должен** быть в коммите: в 1.2.0 добавились шесть новых пакетов с
их зависимостями.

## Шаг 4. Docker-образ и Chrome-сайдкар (если docker есть)

Единственное, что вообще не запускалось. Если docker недоступен — пропусти шаг
и скажи об этом в отчёте, релиз это не блокирует.

```powershell
docker build -t ru-marketplace-mcp:1.2.0 .
docker run --rm -e MCP_TRANSPORT=http -e MCP_HTTP_HOST=0.0.0.0 -p 127.0.0.1:8000:8000 ru-marketplace-mcp:1.2.0 wb-mcp
```

В другом окне повтори `curl` против порта 8000 (см. `docs/DEPLOYMENT.md`).

Затем проверь tier-2 через сайдкар — новое в 1.2.0. Раскомментируй сервис
`chrome` и `CHROME_CDP_HOST` в `docker-compose.yml`, затем:

```powershell
docker compose up -d chrome ozon
docker compose exec ozon uv run python -c "import asyncio; from ozon_connector.server import ozon_selfcheck; print(asyncio.run(ozon_selfcheck()).status)"
```

`CHROME_CDP_HOST=chrome` указывает клиенту на сайдкар вместо `127.0.0.1`. Если
сборка падает, сообщи вывод — правки Dockerfile это допустимая работа по коду.

## Шаг 5. Необязательно: спайк реквизитов продавца Ozon

Единственная задача из плана 1.1.0, оставшаяся открытой, и единственное место,
где можно писать код. Делай только если Ozon у тебя отвечает (шаг 2.5 дал
`success` или твой Chrome залогинен).

Что известно: путь `/seller/{slug}-{id}/` структурно верный, id продавца уже
приходит в `ozon_card` как `seller.link`. Чего не известно: как в ответе лежат
название юрлица, ОГРН и ИНН — ни одного успешного ответа не получено.

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
`ozon_seller_sample.json` к отчёту или к issue вместе со списком виджетов.
Инструмент, уверенно возвращающий название чужого юрлица, хуже отсутствующего.
Если снова 403 — так и запиши.

## Шаг 6. Коммит и Pull Request

```powershell
git add -A
git commit -m "release: v1.2.0

41 tools across 11 stdio servers (42 in the unified one), up from 22 across 5. Six new marketplaces
(Avito, Taobao, Megamarket, Lamoda, DNS, Citilink), a unified server, and a
configurable CDP host.

Backward compatible: the 22 v1.1.0 tool names and signatures are unchanged,
and stdio remains the default transport.

See CHANGELOG.md for the full notes."

git push -u origin release/v1.2.0
gh pr create --base main --head release/v1.2.0 --title "release: v1.2.0" --body-file PR_BODY.md
```

Где `PR_BODY.md` — временный файл (не коммить его):

```markdown
## Что здесь

Версия 1.2.0: шесть новых маркетплейсов, объединённый сервер, настраиваемый
CDP-хост, CLI и телеметрия. Полные заметки — в `CHANGELOG.md`.

Обратная совместимость: имена и сигнатуры двадцати двух инструментов 1.1.0 не
менялись, транспорт по умолчанию остался stdio.

## Локальная верификация

- [ ] 726 тестов зелёные на нативном Windows
- [ ] `uv run python scripts/e2e_stdio_check.py`: 12/12, у всех v1.2.0
- [ ] ruff, ruff format, mypy (host + win32), stdout-guard чисты
- [ ] Покрытие выше порога 70%
- [ ] `taskkill` резолвится в `C:\Windows\System32\taskkill.exe`
- [ ] Существующие источники selfcheck: <результат>
- [ ] Новые источники selfcheck: avito <..> taobao <..> megamarket <..> lamoda <..> dns <..> citilink <..>
- [ ] Живые вызовы новых источников: <результат>
- [ ] `compare_with_china.py`: <сколько success>
- [ ] Объединённый сервер: 42 tool mounted, `marketplace_sources` пропустил 0
- [ ] `doctor` код возврата: <0 / 2, какие источники не проверены>
- [ ] Сверка глазами по каждому CDP-источнику: <цена, наличие, продавец совпали>
- [ ] Taobao в сравнении: юани не выиграли ранжирование, есть `foreign_currency`
- [ ] Docker-образ и сайдкар: <да / нет, docker недоступен>
```

## Шаг 7. Дождись зелёного CI

```powershell
gh pr checks --watch
```

CI прогоняет lint, mypy и тесты на Ubuntu/Windows/macOS × Python 3.12/3.13, плюс
job с порогом покрытия и smoke-job с инвентарём инструментов (ожидаемо 41 в
отдельных серверах), плюс живая MCP-сессия по stdio на все двенадцать.
Live- и CDP-тесты исключены намеренно: у CI нет ни российского IP, ни браузера с
логином. Ночная live-job гоняет канарейки по расписанию, а не на push.

**Если CI красный:**
- падение на конкретной ОС → `gh run view --log-failed`, сообщи вывод
- падение порога покрытия → сообщи фактический процент, **не понижай порог сам**
- падение smoke-job → какой-то сервер не зарегистрировал ожидаемое число
  инструментов; сообщи, какой и сколько
- ошибка сети до маркетплейса → значит какой-то тест тянет сеть, сообщи какой
- **не мержи и не ставь тег при красном CI**

## Шаг 8. Мерж и тег

Только после зелёного CI:

```powershell
gh pr merge --squash --delete-branch
git switch main
git pull --ff-only

git tag -a v1.2.0 -m "ru-marketplace-mcp v1.2.0"
git push origin v1.2.0
```

Тег `v*` запускает релизный workflow: он собирает wheels и sdist всех пакетов и
прикладывает их к GitHub Release. Проверь:

```powershell
gh run watch
gh release view v1.2.0
```

Если workflow не создал релиз автоматически, создай вручную и приложи артефакты
из `dist/`:

```powershell
uv build --all-packages
gh release create v1.2.0 --title "v1.2.0 — six new marketplaces, unified server" --notes-file RELEASE_NOTES.md dist/*
```

Черновик заметок (в `RELEASE_NOTES.md`, тоже не коммить):

```markdown
41 tools across 11 stdio MCP servers, 42 in the unified one, up from 22 across 5. Read-only, no
credentials — challenge-gated sources read through your own Chrome.

Backward compatible: every v1.1.0 tool name and signature is unchanged, and
stdio remains the default transport, so existing MCP client configs keep working.

## New marketplaces

- **Avito** (`avito_*`) — classifieds search, item cards, seller reputation.
  Tier-1 impersonation falls back to your Chrome over CDP.
- **Taobao** (`taobao_*`) — search and cards, prices in yuan (CNY). Every read
  runs in your Chrome (signed mtop API).
- **Megamarket** (`megamarket_*`) — mobile JSON API via CDP (ServicePipe).
- **Lamoda** (`lamoda_*`) — anonymous GraphQL cards plus CDP-backed search.
- **DNS-Shop** and **Citilink** (`dns_*`, `citilink_*`) — DOM via CDP (Qrator).

## New infrastructure

- Unified `marketplace-mcp` server mounting every source as one namespaced
  toolset — one client config entry instead of ten.
- `CHROME_CDP_HOST` makes tier 2 work in Docker via a Chrome sidecar.
- Operator CLI: `marketplace-mcp install` writes client configs,
  `marketplace-mcp doctor` runs every selfcheck plus a CDP session probe.
- Nightly live CI canaries and offline contract tests.
```
