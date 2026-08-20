# v1.2.1 — правки по итогам стороннего ревью

Патч к [v1.2.0](https://github.com/Vladimir-Human/ru-marketplace-mcp/releases/tag/v1.2.0).
Инструменты работают как раньше: их имена, аргументы и формы ответов не менялись.
Обновляться стоит, если вы разработчик и запускали тесты по инструкции из README.

Проект посмотрел человек со стороны, ничего о нём заранее не зная. Нашёл шесть
расхождений, все настоящие.

## Что чинилось

**`npm install jsdom` не работал так, как написано в README.** Часть тестов
прогоняет настоящий JS-экстрактор по разметке, снятой с живого сайта. Проверка
наличия jsdom искала модуль из корня репозитория и находила его, а сам прогон
шёл из временного каталога, где Node его уже не видел. В итоге девять тестов
падали там, где должны были честно скипнуться. Теперь путь к найденному модулю
передаётся прогону явно.

**Число офлайн-тестов в документации разошлось на три значения.** README писал
812, `docs/ARCHITECTURE.md` — 726, отчёт аудита содержал и 822, и 726. Верное
число 822 теперь стоит везде. Для проекта, который в CHANGELOG обещает «числа
приведены к измеренным», расхождение било по самому больному месту.

**Быстрый старт запускал живые тесты.** В README стояло `uv run pytest -q` с
подписью «сеть не нужна», а эта команда собирает четыре теста, которым сеть
нужна: у любого пользователя за пределами России первое знакомство с проектом
начиналось с красного теста Wildberries. В примере теперь тот же фильтр, что
использует CI.

**Шаг чек-листа проверял пустоту.** `pytest -m "cdp"` собирает ноль тестов:
маркер объявлен, но не стоит ни на одном тесте. Рядом было написано, что это
единственное место, где проверяются источники, требующие браузера. Зелёный
результат такой команды не значил ничего. Команда убрана, вместо неё сказано,
чем этот ярус проверяется на самом деле.

**Счёт заменяемых записей конфига был неверным в двух вариантах** — «десять» в
одном файле и «двенадцать» в другом. Верное число одиннадцать: серверов
двенадцать, и объединённый монтирует остальные одиннадцать.

**Абзац в CHANGELOG дублировался** в русской и английской секциях.

## Что нашлось уже в самом патче

Готовый патч проверил ещё один читатель, и нашёл в нём три дефекта.

**Версия не доехала до `__version__`.** Патч поднимал 1.2.0 → 1.2.1 в
четырнадцати `pyproject.toml`, но не в тринадцати `__init__.py`. Установленный
пакет с метаданными 1.2.1 сообщал бы `__version__ == "1.2.0"` — в релизе, весь
смысл которого в том, что числа сходятся. Причина простая: версия написана в
пятидесяти пяти местах, правится руками, и не сверял её никто.

Теперь сверяет `scripts/check_versions.py`. Корневой `pyproject.toml` —
эталон, всё остальное обязано совпасть; проверка стоит в гейте, в CI и в
pre-commit. Первым, что она нашла, был этот самый промах.

**Команда mypy из документации не работает на Windows.** `uv run mypy
packages/*/src` полагается на то, что звёздочку раскроет оболочка. Bash
раскрывает, PowerShell для нативных команд — нет, и mypy получает путь со
звёздочкой буквально. В CI на Linux зелено, у владельца на Windows красно.
Список файлов переехал в `[tool.mypy]`, команда стала просто `uv run mypy`.

**`npm install jsdom` оставляет два файла**, кроме `node_modules`:
`package.json` и `package-lock.json`. Проект не Node-овский, в репозиторий им
не надо — добавлены в `.gitignore`.

## Что изменилось в текстах

README получил раздел «Как это сделано»: код и документация писались с
ИИ-ассистентами, и там же перечислено, чем это проверено. Вопрос происхождения
текста всё равно виден в истории коммитов, и умалчивать о нём смысла нет.

Отчёт аудита прошёл редакторскую правку. Плотность тире упала с одного на 45
слов до одного на 180, повторяющаяся фигура «X, а не Y» убрана почти везде.
Числа, идентификаторы и вердикты остались нетронутыми.

## Что не изменилось

Проверенность источников прежняя, и таблица из заметок к v1.2.0 остаётся в силе:

| Сверено с живым сайтом | Поставляется, сверка за вами |
|---|---|
| Детский мир, Яндекс Маркет, Авито, карточка Wildberries | Ozon, Мегамаркет, Lamoda, Taobao, DNS, Ситилинк |

Все шесть отказывают датацентровым адресам, поэтому проверить их можно только
с домашнего IP и своего залогиненного Chrome. Известное расхождение цены
между поиском и карточкой Wildberries тоже в силе: для точного числа берите
`wb_card`.

## Обновление

```bash
git pull
uv sync --all-packages
```

---

# v1.2.1 — fixes from an outside review (English)

A patch on top of v1.2.0. Tool names, arguments and response shapes are
unchanged; upgrade if you are a developer who ran the tests as the README
described them.

Someone reviewed the project cold and found six real discrepancies.

**`npm install jsdom` did not work as documented.** The jsdom probe resolved the
module from the repository root, while the runner executed from a temp directory
where Node could not see it, so nine tests failed instead of skipping. The
resolved path is now passed to the runner.

**The offline test count disagreed with itself:** 812 in the README, 726 in
`docs/ARCHITECTURE.md`, both 822 and 726 in the audit report. The measured figure
is 822 and now appears everywhere.

**The quickstart ran live tests.** `uv run pytest -q`, labelled "no network
needed", collects four tests that need one, so a first run outside Russia began
with a failing Wildberries test. The example now uses the CI filter.

**A checklist step verified an empty set.** `pytest -m "cdp"` collects zero
tests, and the text beside it claimed this was the only place browser-dependent
sources get checked. The command is gone.

A second reader then reviewed the patch itself and found three more. The
version bump reached fourteen `pyproject.toml` files but not thirteen
`__version__` strings, so an installed 1.2.1 wheel would have reported 1.2.0 —
in the release whose whole point is that the numbers agree. A version string
lives in fifty-five places here and nothing was comparing them;
`scripts/check_versions.py` now does, in the gate, in CI and in pre-commit, and
it found this one. The documented `uv run mypy packages/*/src` also relies on
the shell expanding the glob, which PowerShell does not do for native commands:
the file list moved into `[tool.mypy]` and the command is now plain `uv run
mypy`. And `npm install jsdom` leaves a `package.json` and a
`package-lock.json` behind, both now ignored.

Also: the unified server replaces eleven config entries, not ten or twelve; a
CHANGELOG paragraph was duplicated in both languages; the README now states that
the code and documentation were written with AI assistants and lists what
verifies them; the audit report was copy-edited without touching any number,
identifier or verdict.

**Unchanged:** which sources are verified against live pages. Detsky Mir, Yandex
Market, Avito and the Wildberries card path were compared by hand; Ozon,
Megamarket, Lamoda, Taobao, DNS and Citilink refuse datacenter addresses and stay
unverified until you run them from your own machine.

---

Автор и мейнтейнер: [@Vladimir-Human](https://github.com/Vladimir-Human) · MIT
