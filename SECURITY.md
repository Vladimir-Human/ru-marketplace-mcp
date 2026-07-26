# Безопасность

[English version below](#security)

## Как сообщить об уязвимости

Пишите приватно через [Security Advisories](https://github.com/Vladimir-Human/ru-marketplace-mcp/security/advisories/new)
на GitHub, а не в публичный issue. Первый ответ обычно приходит в течение
нескольких дней.

В отчёте полезны: что получает атакующий, как воспроизвести и какой коннектор или
уровень транспорта задействован.

## Чего проект касается, а чего нет

**Учётных данных в проекте нет вообще.** Ни ключей API, ни токенов, ни паролей, ни
хранилища секретов, ни требования заводить `.env`. Все настройки — эксплуатационные:
таймауты, задержки, регион, прокси. Утекать нечему.

Весь доступ только на чтение, к публичным эндпоинтам каталога, которые использует
официальный веб-клиент. В приватные и административные разделы запросов нет.

## Единственная часть с реальным риском: уровень CDP

Ozon отклоняет датацентровый трафик, поэтому его второй уровень транспорта выполняет
запросы внутри Chrome, который **вы** запустили и в котором залогинились сами, через
DevTools Protocol.

**CDP даёт любому локальному процессу полный контроль над тем профилем, к которому он
подключён**, включая все залогиненные в нём сессии. Это и есть угроза, которую нужно
понимать до включения.

Меры защиты, по важности:

| Мера | Что ограничивает |
|---|---|
| Отдельный профиль для парсинга (по умолчанию) | Радиус поражения: банк и почта остаются в стороне |
| `--remote-debugging-address=127.0.0.1` | Доступ к порту отладки из локальной сети |
| Проверка схемы в `open_page` | Наведение браузера на `file:///` |
| Allowlist хостов в каждом коннекторе | Превращение подставленного ввода в запрос к `/api/personal/orders` |

Отдельный профиль здесь работает как основной контроль. Логиньтесь там только в
маркетплейсы. Подробности в [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

Если Ozon вам не нужен, не включайте этот уровень. Три остальных маркетплейса
работают по обычному анонимному HTTP.

## Прочие меры

**Ограничение размера тела ответа.** Ответы читаются потоком с жёстким лимитом в
байтах, поэтому скомпрометированный CDN или MITM не сможет исчерпать память
бесконечным телом.

**Allowlist окружения дочернего процесса.** Рабочий процесс, который запускает Ozon,
получает только нужные ему переменные: парсеру нечего делать с токенами, случайно
оказавшимися в родительском окружении.

**`taskkill`, который нельзя подменить.** На Windows системный каталог определяется
через `GetSystemDirectoryW`, а не через `SystemRoot` или `WINDIR`: это обычные
переменные окружения, и любой процесс, способный их выставить, мог бы перенаправить
вызов.

**Редиректы по умолчанию не выполняются.** Несколько маркетплейсов отвечают
датацентровым адресам петлёй 307 на себя же, и переход по ней сжигает бюджет
запросов вместо того, чтобы показать блокировку.

**Вычистка ошибок.** Bearer-токены, ключи API и секреты в query-строке удаляются из
текста ошибки до того, как он попадёт в ответ инструмента. Абсолютные пути к профилю
(в них содержится имя пользователя ОС) в видимые ошибки не попадают.

**Проверка формы вместо экранирования.** Значения, попадающие в путь URL или в
выражение фильтра, проверяются строгим шаблоном.

## Внедрение инструкций: граница, которую нужно соблюдать

Вывод инструментов — это **текст, написанный продавцами и покупателями**: названия
товаров, имена продавцов, отзывы. Это недоверенные данные.

Если отзыв или описание выглядит как инструкция («забудь предыдущие указания»,
«скачай этот файл»), агент обязан обращаться с этим как с входными данными, а не как
с политикой. Об этом сказано в каждом skill-документе и в докстрингах инструментов,
но окончательный контроль — на стороне агента, который эти данные читает.

Здесь это важнее обычного: тексты отзывов свободной формы, их много, и писать их
может кто угодно.

## Юридическая заметка

Условия маркетплейсов, как правило, запрещают неофициальный парсинг. Проект
обращается только к публичным эндпоинтам каталога, в намеренно вежливом темпе, для
личных исследований. За своё использование, включая соблюдение местного
законодательства и условий сервисов, отвечаете вы.

## Поддерживаемые версии

Исправления безопасности выходят для последнего релиза. Сообщайте об ошибках по
ветке `main`, если это возможно.

---

# Security

## Reporting a vulnerability

Report privately via GitHub's [Security Advisories](https://github.com/Vladimir-Human/ru-marketplace-mcp/security/advisories/new)
rather than a public issue. A first response should come within a few days.

Useful in a report: what an attacker gains, how to reproduce, and which connector or
transport tier is involved.

## What this project does and does not touch

**There are no credentials anywhere in this project.** No API keys, no tokens, no
passwords, no credential store, no `.env` requirement. Every setting is an operational
knob (timeouts, rate gaps, region, proxy). Nothing to leak.

All access is read-only, against the public catalog endpoints the official web clients
use. No authenticated or administrative areas are touched.

## The one part that carries real risk: the CDP tier

Ozon rejects datacenter traffic, so its second transport tier runs fetches inside a
Chrome instance **you** started and logged into, over the DevTools Protocol.

**CDP grants any local process full control of the profile it is attached to**,
including every session logged into that profile. That is the threat to understand
before enabling it.

Mitigations, in order of importance:

| Mitigation | What it bounds |
|---|---|
| Dedicated scraping profile (default) | Blast radius: banking and email stay out |
| `--remote-debugging-address=127.0.0.1` | LAN access to the debugging port |
| Scheme guard in `open_page` | The browser being aimed at `file:///` |
| Per-connector host allowlists | A crafted input becoming a request for `/api/personal/orders` |

The dedicated profile is not a nicety, it is the primary control. Log into
marketplaces there and nothing else. Full detail:
[docs/CDP_SETUP.md](docs/CDP_SETUP.md).

If you do not need Ozon, do not enable this tier. The other three marketplaces work
over plain anonymous HTTP.

## Other hardening in place

**Bounded response bodies.** Responses stream against a hard byte cap, so a
compromised CDN or MITM cannot exhaust memory with an endless body.

**Allowlisted child environments.** The worker process Ozon spawns receives only the
variables it needs: a scraping worker has no business seeing tokens that happen to sit
in the parent environment.

**Un-hijackable `taskkill`.** On Windows the system directory is resolved via
`GetSystemDirectoryW`, not `SystemRoot`/`WINDIR`, because those are ordinary
environment variables that any process able to set the environment could redirect.

**Redirects not followed by default.** Several marketplaces answer datacenter IPs with
self-referential 307 loops; following them burns the request budget instead of
surfacing the block.

**Error redaction.** Bearer tokens, API keys and query-string secrets are stripped
from error text before it reaches a tool response, and absolute profile paths (which
contain the OS username) are kept out of user-visible errors.

**Input validation over escaping.** Values that reach URL paths or filter expressions
are validated against a strict shape rather than escaped.

## Prompt injection: the boundary users must respect

Tool output is **seller- and buyer-authored content**: product titles, seller names,
review text. It is untrusted data.

If a review or description appears to contain instructions ("ignore previous
instructions", "fetch this URL"), an agent must treat it as input, not policy. Every
skill document and tool docstring states this, but the ultimate control is the
consuming agent's own trust boundary.

This matters more than usual here: review text is free-form, high-volume, and written
by anyone.

## Legal note

Marketplace terms of service generally disallow unofficial parsing. This project
queries only public catalog endpoints, at a deliberately polite rate, for personal
research. You are responsible for your own use, including compliance with local law
and the relevant terms.

## Supported versions

The latest release receives security fixes. Report against `main` where possible.
