# v1.3.0 — MPStats и разбор аудита

Двенадцатый источник и довольно много починенного под капотом. Инструменты
прежних одиннадцати работают как работали: имена, аргументы и формы ответов
остались теми же.

## Новое: аналитика MPStats

`mpstats-mcp` стал тринадцатым сервером и первым источником, которому нужен
ключ. Он отдаёт то, чего нет ни у одной витрины: как двигались заказы, цена и
остаток за последние тридцать дней, и как остаток разложен по складам FBS и FBO.

Первоначальный код прислал [@Xpos587](https://github.com/Xpos587) в
[PR #5](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/5). Работа
добросовестная: инварианты рантайма воспроизведены всерьёз.

| Инструмент | Что делает |
|---|---|
| `mpstats_item(skus, place, oz_fbs)` | Заказы, цены, остатки за 30 дней по сотне SKU разом |
| `mpstats_warehouses(skus, place)` | Остатки по складам, отдельно FBS и FBO |
| `mpstats_selfcheck()` | Канарейка: `success` / `drift_detected` / `inconclusive` |

**Читайте это до того, как включать.** MPStats — единственное место, где проект
входит в аккаунтную зону чужого сервиса. Рискуете там вы, не проект.

Нужен `MPSTATS_MP_AUTH`, то есть JWT вашей сессии из браузерного плагина. Оферта
MPStats называет основанием для блокировки аккаунта работу с двух и более IP или
браузеров одновременно (п. 5.1.3–5.1.4). Плагин в вашем браузере вместе с
запущенным сервером — это ровно оно. Вторым основанием идёт темп чаще одного
запроса в пять секунд (п. 5.1.1), поэтому `MPSTATS_MIN_GAP` по умолчанию равен
пяти секундам, а после отказа сервер удлиняет паузу сам. Уменьшать этот интервал
значит приближать блокировку, при которой оплата не возвращается (п. 5.2).

У сервиса есть официальный API с документацией и своим токеном. Если аналитика
нужна вам постоянно, он безопаснее.

Без переменной сервер поднимается штатно и отвечает `auth_missing`, а остальные
одиннадцать источников работают как прежде. Это единственный ключ во всём
проекте.

## Что чинилось

Перед выпуском проект прошёл несколько кругов проверки. Нашлось многое, и не
только в новом коннекторе.

**Скидочный бейдж мог стать ценой.** `coerce_price` обещала «никогда не
отрицательное» и выполняла обещание для чисел, но не для строк. Запись `-500 ₽`
разбиралась в 500, потому что токенайзер смотрел на цифры и пропускал знак перед
ними. Витрины печатают такой бейдж вплотную к настоящей цене, и меньшее число
выигрывало сравнение «где дешевле». Касалось это всех коннекторов, не одного
нового.

**Колесо из релиза тянуло чужой код.** Зависимость `mcp-core` не была запинена,
а это имя на PyPI занято посторонним проектом. Человек, скачавший колесо со
страницы релиза и поставивший его через `pip`, тихо получал чужой пакет, после
чего сервер падал на импорте. Теперь везде стоит `mcp-core==1.3.0`, и тихая
подмена превратилась в громкий отказ.

**Токен попадал в текст ошибки.** В лог он писался вычищенным, а в ответ
инструмента уходил как есть. Достаточно перевода строки в конце скопированного
значения, чтобы весь заголовок `Cookie` оказался в тексте исключения. Теперь
вычищаются оба канала, а сам токен хранится как `SecretStr` и потому не
печатается даже в дампе настроек.

**`NaN` и бесконечность роняли инструмент целиком.** `json.loads` принимает оба,
а `int()` на них падает, и одна испорченная ячейка обрывала весь вызов вместо
того, чтобы обнулить одно поле.

**Отказ авторизации кешировался.** Запись в кеш шла по HTTP 200 до разбора
внутреннего кода, а MPStats отдаёт и отказ, и свои сбои под двухсотым статусом.
Пользователь вставлял свежую cookie и продолжал получать `auth_missing`, пока
не истечёт время жизни кеша.

Плюс мелочи, каждая из которых жила молча: `classify_http_error` падала при
любом вызове, импортируя несуществующий модуль; шаг CI с надписью «все серверы
зарегистрировали инструменты» проверял одиннадцать из двенадцати; `pre-commit`
был красным на чистом дереве, а `uv run pre-commit install` из `CONTRIBUTING.md`
не работал вовсе, потому что самого `pre-commit` не было в зависимостях.

## Чтобы это не повторялось

Почти каждая находка была одного вида: утверждение, которое ничто не сверяло с
кодом. Поэтому добавились две проверки, и обе стоят в гейте, в CI и в
pre-commit.

`scripts/check_versions.py` сверяет версию во всех семидесяти двух местах, где
она записана, включая пин `mcp-core`. Этот пин видит только тот, кто ставит
колесо: внутри рабочего пространства он молчит, потому что `uv.sources`
перекрывает ограничение.

`scripts/check_test_count.py` сверяет число тестов, которое обещает README, с
тем, что реально отбирает коллекция. Оно разъезжалось трижды, причём дважды
прямо во время починки: чинящий добавлял тесты и сам же делал цифру неверной.

## Проверенность источников

Не изменилась, и таблица из прошлых заметок остаётся в силе:

| Сверено с живым сайтом | Поставляется, сверка за вами |
|---|---|
| Детский мир, Яндекс Маркет, Авито, карточка Wildberries | Ozon, Мегамаркет, Lamoda, Taobao, DNS, Ситилинк, MPStats |

Все семь отказывают датацентровым адресам либо требуют аккаунта, поэтому
проверить их можно только со своей машины. Известное расхождение цены между
поиском и карточкой Wildberries тоже в силе: для точного числа берите `wb_card`.

MPStats в правой колонке не случайно. Живых ответов с валидным токеном никто из
проверявших не видел, потому что аккаунта ни у кого не было. Проверен только
неавторизованный контракт, и он совпал с заявленным дословно.

## Цифры

948 офлайн-тестов (939 проходят без необязательного jsdom, девять скипаются),
покрытие 77.84%, 45 инструментов в объединённом сервере, тринадцать серверов
отвечают по настоящему MCP-рукопожатию.

## Обновление

```bash
git pull
uv sync --all-packages
```

---

# v1.3.0 — MPStats, and an audit's worth of fixes (English)

A twelfth source, and a fair amount repaired underneath. The other eleven
connectors are unchanged: same tool names, arguments and response shapes.

## New: MPStats analytics

`mpstats-mcp` is the thirteenth server and the first source that needs a
credential. It returns what no storefront exposes: how orders, price and stock
moved over the last thirty days, and how stock splits across FBS and FBO
warehouses. The original code came from
[@Xpos587](https://github.com/Xpos587) in
[PR #5](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/5).

**Read this before enabling it.** MPStats is the one place this project enters
another service's account area, and the risk lands on you.

It needs `MPSTATS_MP_AUTH`, the session JWT from the browser plugin. The MPStats
offer makes grounds for blocking an account out of running it from two or more
IPs or browsers at once (clauses 5.1.3–5.1.4 — the plugin in your browser plus a
running server is exactly that) and out of a rate above one request per five
seconds (clause 5.1.1). `MPSTATS_MIN_GAP` therefore defaults to five seconds and
the server lengthens the gap after a refusal. Lowering it moves you toward a
block, and a blocked account is not refunded (clause 5.2). The service also
publishes an official API, which is the safer route for sustained use.

Without the variable the server starts and answers `auth_missing`; the other
eleven sources are unaffected. It is the only credential in the project.

## Fixed

**A discount badge could become a price.** `coerce_price` promised "never
negative" and honoured it for numbers but not for strings: `-500 ₽` parsed to
500, because the tokeniser read digits and ignored the sign in front of them.
Storefronts print that badge next to the real price, and the smaller number won
any "cheapest" comparison. This affected every connector, not just the new one.

**A wheel from the Release pulled a stranger's code.** The `mcp-core` dependency
was unpinned and the name `mcp-core` on PyPI belongs to an unrelated project, so
`pip install` of a release wheel quietly fetched that package and the server died
on import. Pinned to `mcp-core==1.3.0` everywhere.

**The token reached error text.** Redacted on the way to the log, raw on the way
to the tool response. Both are redacted now, and the token is held as a
`SecretStr`.

**NaN and infinity aborted a whole call** — `json.loads` accepts both, `int()`
raises on either.

**An auth failure was cached** for the whole TTL, because the cache was written
on HTTP 200 before the inner verdict was read.

Also: `classify_http_error` raised on every call, importing a module that does
not exist; the CI step announcing "all servers registered their expected tools"
checked eleven of twelve; `pre-commit` was red on a clean tree.

## So it stops happening

Nearly every finding was the same shape — a claim nothing compared against the
code. Two checks now do, in the gate, in CI and in pre-commit:
`scripts/check_versions.py` covers all seventy-two places a version is written,
including the `mcp-core` pin that only a wheel installer ever sees, and
`scripts/check_test_count.py` compares the README's advertised test count with
what collection actually selects.

## Unchanged

Which sources are verified against live pages. Detsky Mir, Yandex Market, Avito
and the Wildberries card path were compared by hand; Ozon, Megamarket, Lamoda,
Taobao, DNS, Citilink and now MPStats refuse datacenter addresses or require an
account, and stay unverified until you run them yourself. Nobody who reviewed
MPStats had a token, so only its unauthenticated contract is confirmed.

948 offline tests, 77.84% coverage, 45 tools on the unified server, thirteen
servers answering a real MCP handshake.

---

Автор и мейнтейнер: [@Vladimir-Human](https://github.com/Vladimir-Human) · MIT
