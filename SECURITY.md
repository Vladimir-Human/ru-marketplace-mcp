# Безопасность

[English version below](#security)

## Как сообщить об уязвимости

Пишите приватно через [Security Advisories](https://github.com/Vladimir-Human/ru-marketplace-mcp/security/advisories/new)
на GitHub, а не в публичный issue. Первый ответ обычно приходит в течение
нескольких дней.

В отчёте полезны: что получает атакующий, как воспроизвести и какой коннектор или
уровень транспорта задействован.

## Чего проект касается, а чего нет

**Секрет в проекте один, и тот необязательный.** Одиннадцать источников из
двенадцати не требуют ни ключей API, ни токенов, ни паролей: все настройки у них
эксплуатационные, то есть таймауты, задержки, регион и прокси. Исключение одно —
коннектор MPStats. Ему нужен `MPSTATS_MP_AUTH`, JWT вашей платной сессии. Вы
передаёте его через переменную окружения или `.env`; проект нигде его не хранит,
не пишет в логи и вырезает из текста ошибок вместе с прочими секретами. Без этой
переменной сервер MPStats поднимается и честно отвечает `auth_missing`, а
остальные одиннадцать работают как работали.

Весь доступ только на чтение. Одиннадцать источников читают публичные эндпоинты
каталога, которые дёргает официальный веб-клиент: ни в приватные, ни в
административные разделы запросов нет. MPStats устроен иначе. Это приватный API
браузерного плагина, доступный по вашей сессии, и потому единственное место, где
проект обращается в аккаунтную зону. Что это означает для вашего аккаунта,
описано в README.

## Единственная часть с реальным риском: уровень CDP

Семь источников читают через Chrome, который **вы** запустили и в котором залогинились
сами, по DevTools Protocol. Taobao, Мегамаркет, DNS и Ситилинк — только так,
анонимного уровня у них нет. Ozon и Авито уходят в браузер лишь когда анонимный
уровень получил отказ. Lamoda берёт через браузер поиск, карточка идёт анонимно.

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

Wildberries, Яндекс Маркет и Детский мир к CDP не обращаются вообще. Если вам хватает
их, уровень можно не включать — но тогда семь остальных источников читать нечем.

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
(в них содержится имя пользователя ОС) в видимые ошибки не попадают. С 1.2.0 вырезается
и `user:pass@` из URL: прокси настраивается строкой `http://user:pass@host:port`, и
раньше ошибка соединения уносила логин с паролем в лог и в ответ клиенту.

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

Условия маркетплейсов, как правило, запрещают неофициальный парсинг. К
одиннадцати источникам проект обращается только по публичным эндпоинтам каталога,
в намеренно вежливом темпе, для личных исследований.

MPStats требует отдельной оговорки: там вы рискуете оплаченным аккаунтом, а не
только доступом. Его оферта называет основанием для блокировки работу одного
аккаунта с двух и более IP-адресов или браузеров одновременно (п. 5.1.3–5.1.4), а
плагин в вашем браузере вместе с запущенным сервером это ровно оно. Вторым
основанием идёт темп чаще одного запроса в пять секунд (п. 5.1.1), поэтому
`MPSTATS_MIN_GAP` по умолчанию равен пяти секундам. Уменьшать его значит
приближать блокировку, при которой оплата не возвращается (п. 5.2). У сервиса
есть и официальный API: если аналитика нужна постоянно, он безопаснее.

За своё использование, включая соблюдение местного законодательства и условий
сервисов, отвечаете вы.

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

**There are no credentials anywhere in this project, with one optional exception.**
No API keys, no tokens, no passwords, no credential store, no `.env` requirement.
Every setting is an operational knob (timeouts, rate gaps, region, proxy). The one
exception is the optional MPStats connector's `MPSTATS_MP_AUTH`: a paid account JWT
you supply yourself via env. It is never written into code or stored by the project
— there is still nothing to leak.

All access is read-only. Eleven sources read the public catalog endpoints the official
web clients use, touching no authenticated or administrative area. MPStats is the
exception: a private browser-plugin API reached with your own session, and so the one
place this project enters an account-gated zone. The README explains what that means
for your account.

## The one part that carries real risk: the CDP tier

Seven sources run their fetches inside a Chrome instance **you** started and logged
into, over the DevTools Protocol. Taobao, Megamarket, DNS and Citilink work no other
way — they have no anonymous tier. Ozon and Avito fall back to the browser only after
the anonymous tier is refused. Lamoda splits the difference: search goes through the
browser, the product card does not.

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

Wildberries, Yandex Market and Detsky Mir never touch CDP. If those three cover your
needs, leave the tier off — but the other seven sources cannot be read without it.

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

**Error redaction.** Proxy credentials, bearer tokens, API keys and query-string
secrets are stripped from error text before it reaches a tool response, and absolute
profile paths (which contain the OS username) are kept out of user-visible errors. The
`user:pass@` case landed in 1.2.0: proxies are configured as `http://user:pass@host:port`,
so a connect failure used to carry the login into both the log and the client's error.

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
queries only public catalog endpoints for eleven of its sources, at a deliberately
polite rate, for personal research.

MPStats needs its own warning, because there you risk a paid account rather than just
access. Its offer makes grounds for blocking out of running one account from two or
more IPs or browsers at once (clauses 5.1.3–5.1.4 — the plugin in your browser plus a
running server is exactly that) and of a rate above one request per five seconds
(clause 5.1.1). `MPSTATS_MIN_GAP` therefore defaults to five seconds; lowering it
moves you toward a block, and a blocked account is not refunded (clause 5.2). The
service also has an official API, which is the safer route for sustained use.

You are responsible for your own use, including compliance with local law and the
relevant terms.

## Supported versions

The latest release receives security fixes. Report against `main` where possible.
