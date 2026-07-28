# Чек-лист выпуска релиза

Порядок, по которому выходит новая версия. Он существует потому, что коннекторы
читают неофициальные эндпоинты: тест проверяет, что парсер что-то вернул, и не
проверяет, что вернулось верное. Разница между этими двумя вещами и есть
содержание релизной проверки.

Английская версия — ниже.

---

## Критерии go / no-go

Не выпускать по ощущению «вроде работает». Только по списку.

**Go** — всё сразу:

- офлайн-гейт зелёный, покрытие выше порога 70;
- `e2e_stdio_check.py` даёт 12/12, у всех серверов версия релиза;
- `doctor` вернул `0`, либо `2` с понятным объяснением по каждому
  непроверенному источнику;
- по каждому источнику, который ответил, сверка глазами сошлась по цене и
  наличию;
- юани Taobao не выиграли рублёвое ранжирование;
- CI зелёный.

**Conditional go** — то же самое, но часть источников осталась непроверенной или
дрейфует. Тогда релиз возможен, если такие источники **явно помечены** в README
и заметках релиза и убраны из списка тех, про кого написано «работает».
Непроверенный источник под ярлыком рабочего — это и есть враньё, которого
проект избегает.

**No-go** — достаточно одного пункта:

- цена, валюта, наличие или продавец не совпали с сайтом;
- `doctor` вернул `1`;
- сравнение назвало полным результат, из которого источник молча выпал;
- юаневая цена попала в рублёвое ранжирование;
- чистая установка, сборка образа или CI красные.

---

## 1. Офлайн-гейт

Сеть не нужна, всё должно проходить за секунды.

```powershell
uv lock --check
uv sync --frozen --all-packages
uv run pytest -q -m "not live and not cdp"
uv run ruff check . ; uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src
uv run python scripts/check_no_print.py
uv run pytest -q -m "not live and not cdp" --cov --cov-report= --cov-fail-under=70
uv run python scripts/e2e_stdio_check.py
```

Если прогон занимает минуты вместо секунд — это дефект гигиены: значит тест
спит на живом пейсере или лезет в Chrome. Разбирать до релиза.

Часть тестов исполняет настоящий JS-экстрактор по снятой разметке и требует Node
с jsdom. Без него эта половина скипается, и покрытие экстракторов теряется:

```powershell
npm install jsdom
```

## 2. Консистентность версий

Одна версия во всех тринадцати `pyproject.toml`, в `server.json`, в
`SERVER_VERSION` каждого сервера и в `docker-compose.yml`. `e2e_stdio_check.py`
показывает версию каждого сервера — сверить глазами.

## 3. Живая проверка с машины оператора

Семь источников отказывают датацентровым адресам, поэтому CI их не проверяет.
Это единственное место, где они проверяются вообще.

```powershell
pwsh scripts\start_chrome_cdp.ps1     # Chrome с CDP на 127.0.0.1:9222
```

Автоматических тестов для этого яруса нет: маркер `cdp` объявлен в
`pyproject.toml`, но не стоит ни на одном тесте, и `pytest -m "cdp"` соберёт
ноль штук. Зелёный результат такой команды не значил бы ничего — проверка идёт
вызовами `*_selfcheck` и глазами.

По каждому источнику — его `*_selfcheck`. Трактовка ответа:

| Ответ | Что означает | Что делать |
|---|---|---|
| `success` | транспорт ответил, конверт разобран | идти дальше, но это ещё не правда данных |
| `drift_detected` | страница пришла, парсер не понял | чинить селекторы, это единственный случай для правки кода |
| `inconclusive` | не дали проверить: IP, капча, сессия | пометить источник непроверенным, в PASS не превращать |

Для разбора `drift_detected` есть `scripts/diagnose_drift.py`: он отвечает, в
каком из трёх состояний страница, и не даёт чинить селекторы вслепую.

## 4. Сверка глазами — то, что отличает данные от правды

Успешный `selfcheck` доказывает, что транспорт ответил, а не что цифра верна. У
DNS был ровно такой случай: после правки регулярки id `selfcheck` позеленел, а
название и цена у каждого товара остались пустыми.

По каждому источнику, который ответил, взять **два-три товара** и открыть их на
сайте в обычном браузере рядом. Сравнить:

- **цену** — ту, что видит незалогиненный покупатель, без карты площадки и без
  подписки. Если инструмент отдаёт цену по карте как обычную, это блокер: в
  `compare_prices` она обойдёт честные цены конкурентов;
- **наличие** — товар в наличии на сайте должен приходить с `in_stock: true`, а
  не с `false` из-за неразобранного количества;
- **продавца** — для Авито и Wildberries это отдельный сигнал доверия, и
  подставленное не то имя хуже пустого;
- **валюту Taobao** — юани в `price_native`, а `price_rub` обязан остаться
  `null`.

Расхождение по цене или наличию — блокер для этого источника. Остальные
выпускаются, а этот честно помечается непроверенным.

Отдельно — запрос, по которому найдётся и российский товар, и Taobao:

```powershell
uv run python examples/compare_with_china.py "iphone 15"
```

Юаневая строка не должна выиграть ранжирование и попасть в `cheapest`, а в
`warnings` обязана появиться строка `foreign_currency`. Если юани выиграли —
это P0, релиз останавливается.

## 5. Что не должно попасть в коммит

```powershell
git status --short
```

В индексе не должно быть: `.venv`, `__pycache__`, `.ruff_cache`, `.mypy_cache`,
`.coverage`, `doctor-status.json`, `.python-version`, архивов `*.zip` и профиля
Chrome. `doctor-status.json` — снимок одного прогона с одной машины: приложенный
к релизу, он однажды уже вступил в противоречие с текстом отчёта.

## 6. Заметки релиза

Написать `RELEASE_NOTES_<тег>.md` — например `RELEASE_NOTES_v1.3.0.md`. Workflow
релиза берёт файл с этим именем; если его нет, релиз выйдет с короткой
заглушкой.

В заметках обязательно указывать, **какие источники сверены с живыми страницами,
а какие нет**. Это не формальность: маркетплейс может открыть любой читатель, и
расхождение между обещанием и реальностью обнаруживается за минуту.

## 7. Ветка, PR, тег

Прямой пуш в `main` не используется.

```powershell
git checkout -b release/vX.Y.Z
git add -A
git commit
git push -u origin release/vX.Y.Z
gh pr create --base main --head release/vX.Y.Z --body-file RELEASE_NOTES_vX.Y.Z.md
```

Мерж только с зелёным CI. Затем:

```powershell
gh pr merge --squash --delete-branch
git checkout main ; git pull --ff-only origin main
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

Пуш тега запускает release workflow: он собирает 26 артефактов (13 wheel и 13
sdist) и прикладывает их к релизу.

## 8. Проверка после публикации

Установить релиз с нуля, как это сделает посторонний человек:

```powershell
git clone --depth 1 --branch vX.Y.Z https://github.com/Vladimir-Human/ru-marketplace-mcp fresh
cd fresh
uv sync --all-packages
uv run python scripts/e2e_stdio_check.py
```

Проверить страницу релиза глазами: текст на месте, таблица проверенных
источников отрисовалась, приложено 26 файлов.

---

# Release checklist (English)

The order a new version goes out in. It exists because the connectors read
unofficial endpoints: a test proves the parser returned something, not that what
it returned is true. The gap between those two is what release verification is
for.

## Go / no-go

**Go** — all at once: the offline gate is green and above the 70% coverage
floor; `e2e_stdio_check.py` reports 12/12 at the release version; `doctor`
returns `0`, or `2` with a clear account of every unverified source; every
source that answered was compared by eye on price and availability; Taobao's
yuan did not win a rouble ranking; CI is green.

**Conditional go** — the same, but some sources stayed unverified or drifted.
The release may ship if those are **explicitly labelled** in the README and the
release notes and removed from anything claiming they work. An unverified source
presented as a working one is the failure mode this project exists to avoid.

**No-go** — any one of: price, currency, availability or seller disagreed with
the site; `doctor` returned `1`; a comparison called a result complete while a
source had silently dropped out; a yuan price entered a rouble ranking; a clean
install, image build or CI is red.

## The steps

1. **Offline gate** — lock check, frozen sync, tests, ruff, mypy on host and
   win32, the no-print check, coverage floor, and a real stdio MCP session for
   all twelve servers. Seconds, not minutes; a slow run means a test is sleeping
   on the live pacer or reaching for Chrome. `npm install jsdom` to also run the
   extractor checks against captured markup.
2. **Version consistency** — one version across all thirteen `pyproject.toml`
   files, `server.json`, every `SERVER_VERSION`, and `docker-compose.yml`.
3. **Live checks from the operator's machine** — seven sources refuse datacenter
   addresses, so this is the only place they are exercised at all. Run each
   `*_selfcheck`: `success` means the transport answered, `drift_detected` means
   the page arrived and the parser did not understand it (the only case that
   calls for a selector fix), `inconclusive` means IP, captcha or session got in
   the way and the source stays unverified.
4. **Compare by eye** — a passing selfcheck is not proof the number is right.
   DNS once went green while every title and price came back empty. Take two or
   three products per source and read the site next to the tool: the price an
   anonymous buyer sees, availability, seller, and Taobao's currency
   (`price_native` in yuan, `price_rub` null).
5. **Keep the commit clean** — no `.venv`, caches, `.coverage`,
   `doctor-status.json`, `.python-version`, `*.zip` or Chrome profile.
6. **Write `RELEASE_NOTES_<tag>.md`** and say plainly which sources were
   verified against live pages and which were not. Any reader can open a
   marketplace and check.
7. **Branch, PR, green CI, squash merge, tag.** The tag push builds and attaches
   26 artifacts.
8. **Verify after publishing** — clone the tag fresh, sync, and run
   `e2e_stdio_check.py`, then read the release page.
