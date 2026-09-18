# ru-marketplace-mcp — контекст проекта

uv-workspace, Python 3.12+. Четырнадцать пакетов: `mcp-core` — общий рантайм,
двенадцать коннекторов-серверов, `marketplace-connector` — объединённый монтаж и
CLI. Коннекторы читают **неофициальные** эндпоинты маркетплейсов, только чтение.

## Команды

```bash
uv sync --all-packages
uv run pytest -q -m "not live and not cdp"     # офлайн-набор, без сети
npm install jsdom                              # обязателен для тестов экстракторов
```

Полный гейт перед любым коммитом:

```bash
uv lock --check
uv sync --frozen --all-packages
uv run ruff check . ; uv run ruff format --check .
uv run mypy
uv run mypy --platform win32
uv run mypy --platform darwin
uv run pytest -q -m "not live and not cdp"
uv run python scripts/check_no_print.py
uv run python scripts/check_versions.py
uv run python scripts/check_test_count.py
uv run pytest -q -m "not live and not cdp" --cov --cov-fail-under=70
uv run python scripts/e2e_stdio_check.py
```

`mypy` берёт дерево из `[tool.mypy] files`, аргументы на командной строке не
нужны: glob `packages/*/src` не раскрывается в PowerShell.

## Инварианты

- Отсутствующее значение — `None`, никогда `0`. Ноль выведет снятый с продажи
  товар в самые дешёвые.
- Форма разъехалась → `parser_drift`. Уверенно неверный ответ хуже ошибки.
- `transport_down` = нас заблокировали; `parser_drift` = данные изменили форму.
  Лечатся по-разному, путать нельзя.
- Ни одного `print()`: stdio-сервер MCP владеет stdout. Диагностика — `log_event`
  (stderr) или методы `Context`.
- Входные значения проверяются по форме (цифры, слаг), а не экранируются.
- Цена — это число, привязанное к знаку валюты. Не минимум из чисел на плитке.
- Возможности нет в источнике — инструмента, изображающего её, тоже нет.
- Тесты работают офлайн. Сетевые — маркер `live`, браузерные — `cdp`, CI
  исключает оба.
- Для HTML/SSR фикстура — **снятая** страница, обрезанная. Выдуманная разметка
  запрещена.
- Успешный selfcheck доказывает, что транспорт ответил, и ничего не говорит о
  правильности данных.

## Среда этой машины

Российского резидентного IP нет, залогиненного Chrome нет. Тесты `live` и `cdp`
запускать нельзя, к маркетплейсам ходить нельзя. Сеть — только `uv` и `npm`.

@CONTRIBUTING.md
@docs/ARCHITECTURE.md
