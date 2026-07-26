# Участие в проекте

Спасибо за интерес. Проект читает неофициальные эндпоинты маркетплейсов, и это
определяет, что здесь считается хорошим кодом. Заметки ниже в основном про это.

[English version below](#contributing)

## Подготовка

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q
```

Можно поставить хуки, чтобы коммит падал сразу, а не в CI:

```bash
uv run pre-commit install
```

## Перед открытием pull request

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src
uv run pytest -q
uv run python scripts/check_no_print.py
```

CI прогоняет то же самое на Ubuntu, Windows и macOS против Python 3.12 и 3.13.

**Кросс-платформенный прогон mypy обязателен, если трогаешь платформозависимый
код.** На Linux mypy считает `os.killpg`, `os.getpgid` и `signal.SIGKILL`
существующими, поэтому ссылка, ломающая Windows, проходит ревью незаметно. Доставай
POSIX-функции через `getattr` и держи ветку платформы за подменяемой прослойкой,
чтобы тесты могли её проверить с любой ОС.

## Что важно в этой кодовой базе

**Никогда не выдумывать значение.** Отсутствующая цена — это `None`, не `0`. Ноль
вывел бы снятый с продажи товар в самые дешёвые: это худший класс баги в
ценовом инструменте. Используй `coerce_price` и `coerce_int` из
`mcp_core.resilience`, они возвращают `None` на неоднозначном вводе вместо догадки.

**Падать громко, а не правдоподобно.** Когда формат перестаёт совпадать, бросай
`parser_drift`. Уверенно неверный ответ хуже ошибки, потому что ошибку видно.

**Различать «отказали» и «поменяли».** `transport_down` значит, что нас
заблокировали. `parser_drift` — что данные изменили форму. Лечится это совершенно
по-разному, поэтому путать их нельзя.

**Никогда не писать в stdout.** Stdio-сервер MCP владеет stdout: случайный `print()`
ломает поток JSON-RPC, и на стороне клиента это выглядит загадочной ошибкой разбора.
Диагностика идёт через `log_event` (stderr) или методы `Context` из FastMCP.
Проверяется скриптом `scripts/check_no_print.py`.

**Проверять форму входных данных.** Значения, попадающие в путь URL или в выражение
фильтра, проверяются строгим шаблоном (только цифры, только слаг), а не
экранируются. Особенно это важно для CDP-уровня, который работает внутри
авторизованной сессии браузера.

**Писать описания полей для того, кто API не видел.** Их читает языковая модель,
решая, подходит ли инструмент под вопрос. Объясняй смысл, а не пересказывай имя.

**Если возможности нет в источнике, не делать инструмент, который её изображает.**
Разбор случая с поиском Детского мира — в [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

## Тесты

Каждый тест должен работать без сети. Подменяй слой запросов и проверяй тот
контракт, который видит агент: код ошибки, предупреждение, значения полей.

Для источников с HTML и SSR сохраняй настоящую страницу и обрезай её, а не выдумывай
разметку: тогда изменение структуры на стороне маркетплейса всё равно всплывёт.
Тесты, которым нужна сеть, помечай `@pytest.mark.live`, а браузерные —
`@pytest.mark.cdp`. CI исключает и те, и другие.

## Сообщения о сломанном эндпоинте

Эндпоинты ломаются, это ожидаемо и не значит, что отчёт плохой. Пожалуйста, приложи:

1. Коннектор и инструмент.
2. Вывод соответствующего `*_selfcheck`. Он отличает дрейф формата от блокировки.
3. Откуда идёшь: российский домашний IP, датацентр или VPN. Обычно это решающий
   фактор.
4. JSON ошибки, вычистив личные данные, если они там есть.

Вердикт `inconclusive` от selfcheck обычно означает гео-блокировку, а не баг в коде.
Что делает каждый источник и откуда — в [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

## Добавление маркетплейса

Читай [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md). Сначала прощупай источник и
принеси результаты в issue до написания кода: примерно треть кандидатов оказывается
нереализуемой, и лучше узнать это заранее.

## Что в область проекта входит, а что нет

Входит: чтение публичных данных каталога, работа над надёжностью, новые
маркетплейсы, прошедшие проверку реализуемости, улучшение документации для агентов.

Не входит: всё, что требует хранения учётных данных или аккаунта; операции записи
(оформление заказов, публикация отзывов); сервисы обхода капчи; массовый парсинг в
темпе, от которого защищают текущие ограничения вежливости.

---

# Contributing

Thanks for considering a contribution. This project reads unofficial marketplace
endpoints, which shapes what "good" looks like here. The notes below are mostly about
that.

## Setup

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp.git
cd ru-marketplace-mcp
uv sync --all-packages
uv run pytest -q
```

Optionally install the hooks so a commit fails fast rather than in CI:

```bash
uv run pre-commit install
```

## Before you open a PR

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src
uv run mypy --platform win32 packages/*/src
uv run pytest -q
uv run python scripts/check_no_print.py
```

CI runs the same checks on Ubuntu, Windows and macOS against Python 3.12 and 3.13.

**Run the cross-platform mypy pass if you touch anything platform-specific.** On
Linux, mypy resolves `os.killpg`, `os.getpgid` and `signal.SIGKILL` as present, so a
Windows-breaking reference passes review invisibly. Reach POSIX-only names through
`getattr`, and keep the platform branch behind a patchable seam so tests can exercise
it from any host.

## What this codebase cares about

**Never fabricate a value.** A missing price is `None`, never `0`. A zero would rank
a delisted item as the cheapest option, the single most damaging bug class in a price
tool. Use `coerce_price`/`coerce_int` from `mcp_core.resilience`; they return `None`
on ambiguous input rather than guessing.

**Fail loudly, not plausibly.** When a payload stops matching, raise `parser_drift`.
A confident wrong answer is worse than an error, because an error is diagnosable.

**Distinguish "refused" from "changed".** `transport_down` means we were blocked;
`parser_drift` means the data changed shape. They need completely different fixes, so
conflating them sends the reader down the wrong path.

**Never write to stdout.** A stdio MCP server owns stdout: a stray `print()` corrupts
the JSON-RPC stream and surfaces as a baffling client-side parse error. Use
`log_event` (stderr) or the FastMCP `Context` methods. Enforced by
`scripts/check_no_print.py`.

**Validate inputs by shape.** Values reaching URL paths or filter expressions are
checked against a strict pattern (digits, slug) rather than escaped. This matters
especially for the CDP tier, which runs inside an authenticated browser session.

**Write field descriptions for someone who cannot see the API.** They are what an LLM
reads when deciding whether a tool fits the question. Explain semantics, not names.

**If a capability does not exist upstream, do not ship a tool that pretends it
does.** See the Detsky Mir search case in [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

## Tests

Every test must run offline. Monkeypatch the fetch layer; assert the contract an
agent sees, meaning error codes, warnings and field values.

For HTML/SSR sources, capture a real page and trim it rather than inventing markup,
so upstream structural changes still surface. Mark network tests
`@pytest.mark.live` and browser tests `@pytest.mark.cdp`; CI excludes both.

## Reporting a broken endpoint

Upstream endpoints break; that is expected, not a defect in your report. Please
include:

1. The connector and tool.
2. Output of the relevant `*_selfcheck` (it distinguishes drift from a block).
3. Whether you are on a Russian residential IP, a datacenter IP, or a VPN. This is
   usually the deciding factor.
4. The error JSON, redacted if it contains anything personal.

`inconclusive` from a selfcheck usually means geo blocking rather than a code bug.
[docs/ANTI_BOT.md](docs/ANTI_BOT.md) covers what each source does from where.

## Adding a marketplace

See [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md). Probe the source first and
share the findings in the issue before writing code; roughly a third of candidates
turn out to be infeasible, and that is worth knowing early.

## Scope

In scope: read-only public catalog data, reliability work, new marketplaces that pass
the feasibility probe, better agent-facing documentation.

Out of scope: anything requiring stored credentials or an account; write operations
(placing orders, posting reviews); captcha-solving services; bulk scraping at a rate
these politeness limits are designed to prevent.
