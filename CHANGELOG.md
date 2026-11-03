# Changelog

Здесь записаны все заметные изменения. Формат — по
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), нумерация версий — по
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Русский текст первый, английский — ниже в каждом разделе. Аудитория проекта
русскоязычная, и переводить для неё собственные заметки о релизе странно.

## [Unreleased]

## [2.8.2] - 2026-11-03

### Fixed
- AliExpress больше не игнорирует `ALI_TIMEOUT` при навигации поиска и стартовой
  страницы карточки: вместо жёстких 20 секунд используется заданный положительный
  конечный таймаут (по умолчанию 45 секунд). Общий `CHROME_NAV_TIMEOUT_MS` сохраняет
  20 секунд для остальных источников и применяется к Playwright и raw CDP;
  отправка raw `Page.navigate` входит в тот же дедлайн. Проверки страницы,
  закрытие собственных вкладок и внешние лимиты инструментов сохранены (#190).
- AliExpress search and card-landing navigation now honor `ALI_TIMEOUT` (45 seconds
  by default) instead of a fixed 20-second limit. The shared `CHROME_NAV_TIMEOUT_MS`
  keeps the 20-second default for other sources and applies to Playwright and raw
  CDP, including raw navigation send in its deadline. Page checks, owned-tab cleanup
  and outer tool limits remain unchanged (#190).

## [2.8.1] - 2026-11-03

### Fixed
- Предпубликационное ревью: некорректное значение предпочтительной цены Яндекса
  (включая bool/неконечное число/объект) больше не подменяется справочной ценой;
  фатально испорченная выдача удаляется из кэша и следующий вызов может восстановиться.
  Дедлайн raw-CDP охватывает отправку команды, doctor отвергает повреждённые под-проверки.
- Удалены рекламные tracking-параметры из сохранённой фикстуры Lamoda с честным
  обновлением sanitization/SHA-256; сама фикстура и её ценовые проверки сохранены.
  Уточнён PyPI-путь bundle, сокращены устаревшие машинные дневники GRACE и Docker context.
- Prepublication review: unusable preferred Yandex money no longer falls back to
  a reference price; fatal value-drift pages are evicted so the next call can recover.
  The raw-CDP deadline includes outbound send and doctor rejects malformed subchecks.
  Captured Lamoda advertising query fields were sanitized with updated provenance;
  regression data/prices remain, install metadata and stale operational notes are clarified.
- Сравнение больше не отбрасывает основной товар из-за составного названия
  комплектного аксессуара: «зарядное устройство», «кабель USB-C» или
  «чехол силиконовый» перед «в комплекте», а также определения после явного
  `with` / «в комплекте:». Разбор ограничен тремя известными словами на
  вхождение; другой некомплектный аксессуар, б/у, пунктуация и связи «для/с»
  не теряют смысл. Исходный минимум цены сохраняется.
- Comparison no longer drops the main product because a bundled accessory has
  a noun complement or modifier, such as a charging device, USB-C cable or
  silicone case, including modifiers following an explicit bundle prefix.
  Each occurrence admits at most three recognized words; separate accessories,
  used conditions, punctuation and relationship clauses remain significant.
  Raw cheapest and offer rows are unchanged.
- MCP-гайды теперь совпадают с ответами инструментов: списки типизированных полей
  строятся из моделей сериализации, включая реальные алиасы и обёртку `result`.
  Описаны `cheapest_comparable`, проверка цены, пагинация отзывов Ozon, DOM-поля
  WB и площади/типы Cian. Уточнены условные цены Wallet/Plus, неизвестные значения
  и частичные ответы; челлендж AliExpress обозначен как `challenge_required`,
  а метаданные — как `_meta` у самостоятельных карточек и `card.meta` у инспекторов.
- MCP guides now match tool replies: typed field lists come from serialization
  models, including actual aliases and the `result` envelope. They cover
  `cheapest_comparable`, price verification, Ozon review pagination, WB DOM fields
  and Cian areas/types. Wallet/Plus conditions, unknown values and partial replies
  are explicit; AliExpress challenges use `challenge_required`, and metadata is
  `_meta` on standalone aliased cards but `card.meta` in inspectors.

- `marketplace-mcp doctor` больше не прячет поломку своей же проверки под «не удалось проверить». Упавшая selfcheck-обёртка, неизвестный статус, неизвестный источник и ошибка вызова теперь дают код 4 (жёсткий сбой), и ночной drift-алерт на нём краснеет. Код 2 по-прежнему означает признанную блокировку источника и тревоги не поднимает, дрейф остаётся кодом 1, а отказ live-бюджета показывается как `budget_refused`, а не как здоровье.
- `marketplace-mcp doctor` no longer hides a broken check of its own behind "could not be judged". A crashed selfcheck wrapper, an unknown status, an unknown source and a usage error now exit 4 (hard failure), and the nightly drift alarm turns red on it. Exit 2 still means an acknowledged blocked source and raises no alarm, drift stays exit 1, and a live-budget refusal is shown as `budget_refused`, never as healthy.

- Яндекс больше не выдаёт выдуманную цену. Строка вроде `1 999 2 999` — это два
  числа, склеенные пробелом-разделителем тысяч, — раньше читалась как 19992999.0:
  сумма, которой нет, ещё и выигрывала сравнение как самая дешёвая. Теперь все
  денежные поля Яндекса читаются общим безопасным парсером, неоднозначная сумма
  даёт `price_rub: null` и предупреждение `price_unparseable` с текстом, который
  не удалось прочитать, а обычные группировки (`3 983`) и дробные цены
  (`1 999,50`) остаются ценами. Так же читается и зачёркнутая цена до скидки
  (`price_old_rub`, `price_before_discount_rub`): если её не удалось прочитать,
  поле пустое, а предупреждение называет эту цену и её текст. Карточку с
  нечитаемой обычной ценой Яндекс больше не называет карточкой только с ценой Plus.
- Карточка Яндекса больше не выдаёт цену подписки за обычную цену. Обычная цена
  (`price_rub`) берётся только из явно заданной regular-цены; карточка без неё
  честно говорит, что обычная цена неизвестна (`price_rub: null` и
  предупреждение `no_everyday_price`), вместо того чтобы одолжить сумму «с
  Плюсом». `compare_verify_offer` такую карточку не подтверждает: сумма по
  подписке не выдаётся за обычную цену, а дельта считается от явно заданной
  regular-цены.
- Страница с капчей больше не попадает в кэш. Ответ с HTTP 200, который оказался
  челленджем (или страницей, которую парсер не понял), выбрасывается из кэша, и
  следующий вызов инструмента снова идёт к Яндексу, а не повторяет стену до конца
  срока жизни записи. Обычные страницы кэшируются как раньше.
- Пустой результат Яндекса с явным баннером «ничего не найдено» — честный ответ,
  а не проблема с данными. `meta.healthy` остаётся `true`, сравнение считает
  источник ответившим и завершённым (`complete: true`, ноль предложений).
  Пострадавшая пустая страница по-прежнему нездорова и оставляет сравнение
  неполным; проверка дрейфа для страницы без баннера не отключена.
- Сравнение больше не отбрасывает товар как аксессуар или б/у из-за случайной
  подстроки в названии (`Suitcase`, `стеклокерамика`, `unused`). Маркеры читаются
  по границам слов и фраз без учёта регистра; комплектный аксессуар и явно
  отрицанное состояние (`не б/у`) не мешают товару стать `cheapest_comparable`.
  Настоящие незапрошенные чехлы и б/у по-прежнему исключаются из этого выбора,
  но остаются в общем списке предложений.
- Ответ MCP для `wb_search` теперь совпадает с объявленной схемой: найденные
  товары и `no_results` приходят в SDK-обёртке `result`, а типизированные отказы
  остаются ошибками MCP. Компактер схем больше не выключает обёртку, поэтому
  `tools/list` и `tools/call` не показывают разные формы ответа. Инструменты без
  обёртки сохраняют прежнюю форму.

- Yandex no longer quotes a made-up price. A string like `1 999 2 999` — two
  numbers glued by the thousands-separator space — used to read as 19992999.0, a
  sum nobody charges that also won comparisons as the cheapest offer. Every
  Yandex money field is now read with the shared safe parser: an ambiguous
  amount gives `price_rub: null` plus a `price_unparseable` warning naming the
  text it could not read, while ordinary groupings (`3 983`) and fractional
  prices (`1 999,50`) stay prices. The struck-through pre-discount price
  (`price_old_rub`, `price_before_discount_rub`) is read the same way: when it
  cannot be read, the field is null and the warning names that price and its
  text. A card whose everyday price cannot be read is no longer called a
  Plus-only card.
- A Yandex card no longer passes the subscriber price off as the everyday one.
  The everyday price (`price_rub`) comes from the explicit regular price only; a
  card without one says the everyday price is unknown (`price_rub: null` and a
  `no_everyday_price` warning) instead of borrowing the Plus amount.
  `compare_verify_offer` refuses to certify such a card: a Plus amount is never
  confirmed as the everyday price, and the delta is measured against the
  explicit regular price.
- A captcha page is no longer cached. An HTTP-200 answer that turned out to be a
  challenge (or a page the parser could not read) is dropped from the cache, so
  the next tool call reaches Yandex again instead of replaying the same wall for
  the whole TTL. Normal pages are still cached.
- An empty Yandex result with an explicit "nothing found" banner is an honest
  answer, not a doubt about the data. `meta.healthy` stays `true`, and the
  comparison counts the source as answered and complete (`complete: true`, zero
  offers). A damaged empty answer stays unhealthy and leaves the comparison
  incomplete; the drift check for a page without the banner is not weakened.
- Comparison no longer drops a product as an accessory or used item because of
  an accidental title substring (`Suitcase`, `стеклокерамика`, `unused`). Markers
  now match word/phrase boundaries case-insensitively; a bundled accessory or an
  explicitly negated condition (`не б/у`) does not disqualify the product from
  `cheapest_comparable`. Genuine unrequested cases and used items still lose that
  slot, but remain in the full offer list.
- The MCP reply for `wb_search` now matches its advertised schema: found items
  and `no_results` arrive in the SDK's `result` envelope, while typed refusals
  remain MCP errors. Schema compaction no longer disables that envelope, so
  `tools/list` and `tools/call` agree on the response shape. Tools without SDK
  wrapping keep their existing shape.
Транспорт и ошибки больше не теряют то, что знает оператор и агент: адрес
websocket собирается одинаково для IPv4 и IPv6, команда CDP имеет абсолютный
дедлайн, диагностика в успешном ответе вычищается, подсказки восстановления
следуют за способом установки, а тип ошибки коннектора доходит до вызывающего
вместе со своими полями. Данные и контракты тоже перестают вводить в
заблуждение: счётчик отзывов не становится правдоподобным неверным числом,
число выдачи равно числу возвращённых товаров, ограничения инструментов видны
в его схеме, инвентарь источников учитывает настроенный режим, а обрезанные
предупреждения сообщают, сколько их потеряно.

The transport and the errors no longer lose what the operator and the agent
need: the websocket URL is built the same way for IPv4 and IPv6, a CDP command
has an absolute deadline, a diagnostic inside a success answer is scrubbed, the
recovery hints follow the install mode, and a connector's error reaches the
caller with its own fields. The data and the contracts no longer mislead
either: a review counter is not a plausible-but-wrong number, the number of
results equals the number of items returned, a tool's limits are visible in its
schema, the source inventory follows the configured mode, and cut warnings say
how many were lost.

- «Сырой» CDP-фолбэк больше не строит невалидный адрес для IPv6: хост `::1`
  даёт `ws://[::1]:9222/...`, а не `ws://::1:9222/...`, который вообще не
  разбирается. Один сборщик netloc обслуживает и сокет браузера, и сокет
  страницы; IPv4, `host.docker.internal`, обычное имя хоста и удалённый CDP
  сохраняют прежний вид, и своя вкладка по-прежнему закрывается при любой
  неудаче подключения и при отмене.
- У команды CDP теперь абсолютный дедлайн, а не лимит ожидания ответа. Chrome
  шлёт события всем подключённым клиентам, поэтому прежний таймаут, который
  перезапускался на каждом чужом событии, превращал 80 мс в несколько минут:
  поток посторонних событий больше не продлевает ожидание. Правило действует и
  для команд подключения (`Target.createTarget`, `Page.enable`,
  `Network.enable`, `Runtime.enable`), и для закрытия своей вкладки, а не
  только для `evaluate`; ответы команд и события с URL фрейма по-прежнему
  сопоставляются как раньше.
- Диагностика внутри успешного ответа вычищается до того, как её сохранить или
  вернуть. Причина пропуска источника в `marketplace_sources`, статус CDP и
  окна браузера и подробность в отчёте doctor больше не выдают наружу текст
  исключения с токеном или с учётными данными прокси — раньше лог это чистил, а
  ответ нет. Класс исключения, имя источника и безопасная часть сообщения
  остаются читаемыми: диагностика не теряется, секрет не утекает.
- Подсказки восстановления следуют за способом установки. В checkout они
  называют локальные `scripts/` и `docs/`, установленный пакет — консольную
  команду, которую он действительно ставит, а запуск через `uvx` — закреплённый
  релиз `uvx ru-marketplace-mcp@<версия>`. Ссылки в подсказках закреплены на
  теге релиза и ведут на файлы именно этого релиза: пакет из PyPI больше не
  отправляет оператора к файлу, которого в пакете нет.
- Тип ошибки коннектора больше не теряется в общем обработчике. «Занято» (409)
  и пауза `retry_after_s`, полный реестр удерживаемых страниц, истёкший
  дедлайн и провайдер доходят до вызывающего — и через исходный инструмент, и
  через сравнение источников. Неожиданная ошибка остаётся обычным
  `transport_down`: без выдуманного кода и без утечки учётных данных.

- The raw CDP fallback no longer builds an invalid URL for an IPv6 host: `::1`
  now gives `ws://[::1]:9222/...` instead of `ws://::1:9222/...`, which cannot
  be parsed at all. One netloc builder serves both the browser socket and the
  page socket; IPv4, `host.docker.internal`, a plain host name and a remote CDP
  endpoint keep the spelling they had, and the owned tab is still closed after
  every attach failure and after a cancellation.
- A CDP command now has an absolute deadline instead of an inactivity limit.
  Chrome broadcasts its events to every attached client, so the old timeout,
  which restarted on each foreign event, turned 80 ms into minutes: a stream of
  unrelated events no longer extends the wait. The rule covers the attach
  commands (`Target.createTarget`, `Page.enable`, `Network.enable`,
  `Runtime.enable`) and the owned-target cleanup, not only `evaluate`; matching
  responses and frame URL events still correlate exactly as before.
- A diagnostic inside a success answer is scrubbed before it is stored or
  returned. The reason a source was skipped in `marketplace_sources`, the CDP
  and browser-window status answers and the doctor's per-source detail no
  longer carry the text of an exception with a token or proxy credentials in it
  — the log path scrubbed that text, the answer path did not. The exception
  class, the source name and the harmless part of the message stay readable:
  the diagnosis survives, the secret does not.
- The recovery hints follow the install mode. In a checkout they name the local
  `scripts/` and `docs/`; an installed package names the console script it
  really ships, and a `uvx` run names the pinned `uvx ru-marketplace-mcp@<version>`
  release. Every link a hint carries is pinned to the release's tag and points at
  a file of that release, so the PyPI package no longer sends the operator after
  a file it does not contain.
- A connector's error type no longer gets lost in a generic handler. "Busy"
  (409) with its `retry_after_s`, a full retained-page registry, a deadline and
  the provider now reach the caller — both through the native source tool and
  through the source comparison. An unexpected failure stays the ordinary
  `transport_down`: no invented taxonomy, no leaked credentials.
- Проверка wire-бюджета больше не пропускает бесконечные лимиты. Бюджет
  baseline из `1e999` превращался в `inf`, проверка «положительное число» его
  принимала, а дельта роста становилась `nan` — сравнение `nan > лимит` всегда
  ложно, — поэтому гейт в режиме проверки пропускал инструмент, выросший
  примерно на миллион токенов. Теперь любой бюджет, потолок и порог
  (`--max-token-regression-percent`, `--max-latency-ms`, `--timeout`) читаются
  как конечное число больше нуля: `bool`, `NaN`, `inf` и строка отвергаются с
  понятным сообщением и кодом возврата 1, и проверяется весь файл baseline, а
  не только профили текущего прогона. Обычный замер, явное создание baseline
  и штатная проверка против `work/performance/wire-baseline.json` работают как
  прежде.
- The wire-budget check no longer accepts infinite limits. A baseline budget of
  `1e999` became `inf`, the positive-number check accepted it, and the growth
  delta became `nan` — `nan > limit` is always false — so check mode passed a
  tool that had grown by about a million tokens. Every budget, ceiling and
  threshold (`--max-token-regression-percent`, `--max-latency-ms`, `--timeout`)
  is now read as a finite number above zero: a boolean, `NaN`, `inf` and a
  string are refused with a clear message and exit code 1, and the whole
  baseline file is checked, not only the profiles of the current run. The
  ordinary measurement, the explicit baseline creation and the check against
  the committed `work/performance/wire-baseline.json` keep working.
- Гейт provenance теперь проверяет и полноту набора, а не только те пины,
  которые сумел прочитать. Sidecar без `sha256`, с битым JSON или без парной
  фикстуры — и фикстура без sidecar — раньше давали «checked 0: ok» и код 0.
  Теперь каждый sidecar обязан разбираться, нести пригодный pin SHA-256 и
  описывать ровно одну существующую фикстуру, а каждая фикстура в
  `packages/*/tests/fixtures` — иметь свой sidecar; иначе проверка падает с
  кодом 1. Открытые пробелы перечислены в датированном списке исключений с
  причиной: восемь sidecar без pin («pin missing since capture» — происхождение
  съёмки не выдумывается) и пять фикстур, которые не съёмки, а ручные модели
  или сгенерированный снимок контракта. Исключение закрывает ровно один
  дефект, а закрытое исключение сообщает о себе, чтобы его удалили. Корректные
  31 pin остаются корректными при любых переводах строк.
- The provenance gate now checks the completeness of the set, not only the pins
  it could read. A sidecar without a `sha256`, a broken JSON, an orphan sidecar
  and a fixture without a sidecar all used to give "checked 0: ok" and exit 0.
  Now every sidecar must parse, carry a usable SHA-256 pin and describe exactly
  one fixture that exists, and every fixture under `packages/*/tests/fixtures`
  must have its own sidecar — otherwise the check fails with exit 1. The open
  gaps are listed in a dated exception list with a reason: the eight sidecars
  without a pin ("pin missing since capture" — no capture provenance is
  invented) and the five fixtures that are hand-modelled or a generated
  contract snapshot rather than captures. An exception covers one defect only,
  and a resolved exception says so, so its entry can be deleted. The 31
  correct pins stay correct under any line-ending convention.
- Счётчик отзывов больше не превращается в правдоподобное неверное число.
  Ozon отдаёт его текстом («1.2K отзывов»), и разбор склеивал все цифры подряд:
  «1.2K отзывов» становилось двенадцатью — и ответ оставался здоровым. Теперь
  метка отзывов отделяется, а число разбирается строго: «1 200 отзывов» и «1200»
  дают 1200, а знак, диапазон, два числа и форма с множителем («1.2K») не
  называют одного счётчика и дают пусто. Правило одно и то же для исходного
  инструмента и для сравнения источников, поэтому счётчик не может быть
  правильным с одной стороны и неверным с другой.
- Число найденного в `ozon_search` теперь равно числу возвращённых товаров.
  Страница из сорока плиток отвечала `count=40`, а `items` содержал первые
  тридцать, и обрезка не была сказана вслух. Теперь `count` равен
  `len(items)`, разобранное число приходит отдельным полем `parsed_count`, флаг
  `truncated` и предупреждение называют обрезку, а проверка здоровья
  смотрит ровно на те товары, которые получил агент. Полная короткая выдача
  остаётся полной и здоровой.
- Ограничения партий и закрытые перечисления переехали из описания в схему
  инструмента. `wb_card` больше не принимает по объявленной схеме пустой
  список, ноль или 101 идентификатор: границы партии, положительность
  элементов и закрытые наборы значений (площадка MPStats, число комнат у
  Cian) видны в `tools/list`, и такой вызов отвергается схемой до запроса к
  сети. Серверные проверки и их понятные ошибки остались, а граничные
  значения, которые схема принимает, по-прежнему выполняются.
- Инвентарь источников теперь учитывает настроенный режим Wildberries.
  При `WB_TRANSPORT=dom` (и при обычном режиме API с включённым откатом в DOM)
  `marketplace_sources` больше не говорит, что браузер не нужен, — а именно
  так выглядел инвентарь, когда `wb_search` уходил в CDP. Три вопроса
  разделены: нужен ли браузер обязательно, может ли он понадобиться и какой
  маршрут выбран сейчас; ответы следуют за настройками источника и не открывают
  браузер.
- Диагностические предупреждения больше не режутся молча. Двенадцать
  предупреждений типизированного ответа источника приходили десятью — без
  единого слова об обрезке, и предупреждение, объясняющее нездоровый ответ,
  могло исчезнуть за информационными заметками. Список остаётся ограниченным,
  но последняя запись называет число подавленных предупреждений — и в исходном
  инструменте, и в сравнении источников; настоящие предупреждения
  заполняют бюджет первыми, а вердикт о здоровье считается по полному списку.

- A review counter no longer becomes a plausible-but-wrong number. Ozon renders
  it as display text ("1.2K отзывов") and the parser used to glue every digit
  together, so "1.2K отзывов" became twelve and the answer stayed healthy. The
  review label is now split off and the number is parsed strictly: "1 200
  отзывов" and "1200" give 1200, while a sign, a range, two numbers and a
  magnitude form ("1.2K") name no single counter and give nothing. The rule is
  the same for the native tool and for the source comparison, so a counter
  cannot be right on one side and wrong on the other.
- The number found by `ozon_search` is now the number of items returned. A page
  of forty tiles answered `count=40` while `items` carried the first thirty,
  and the cut was never said out loud. `count` now equals `len(items)`, the
  parsed total arrives in its own `parsed_count` field, and a `truncated` flag
  and a warning name the cut; the health check runs on exactly the items the
  agent receives. A complete short answer stays complete and healthy.
- Batch limits and closed enumerations moved from prose into the tool schema.
  `wb_card` no longer accepts an empty list, a zero or 101 ids as its declared
  schema: the batch bounds, the positivity of the items and the closed value
  sets (the MPStats marketplace, the Cian room counts) are visible in
  `tools/list`, and such a call is refused by the schema before any request
  goes out. The server-side checks and their clear errors stay, and the
  boundary values the schema accepts still run.
- The source inventory now follows the configured Wildberries mode. With
  `WB_TRANSPORT=dom` — and in the ordinary API mode with the DOM fallback on —
  `marketplace_sources` no longer claims that no browser is involved, which is
  exactly what it used to say while `wb_search` went through CDP. Three
  questions are separated: is the browser unavoidable, may it be used, and
  which route is taken now; the answers follow the source's own settings and
  open no browser.
- Diagnostic warnings are no longer cut silently. Twelve warnings of a typed
  source reply arrived as ten, without a word about the cut, and a warning that
  explains an unhealthy answer could disappear behind informational notes. The
  list stays bounded, but the last entry names the number of suppressed
  warnings — in the native tool and in the source comparison — real warnings
  fill the budget first, and the health verdict is computed from the full list.

## [2.8.0] - 2026-11-01

Честные ответы агенту: одно значение `healthy`, отказы и проверки — типизированные ошибки с подсказкой, что делать, условные цены и другие размеры не выигрывают сравнение, неоднозначный текст цены даёт `null`; дрейф виден в предупреждениях инструментов; новый `marketplace_health`; справочник инструментов в MCP-ресурсах; секреты вычищаются из ошибок и логов.

Honest answers for the agent: one meaning of `healthy`, refusals and challenges as typed errors with a next step, conditional prices and other sizes never win a comparison, ambiguous price text becomes `null`; drift shows in the tools' own warnings; a new `marketplace_health`; the tool reference moves into MCP resources; secrets are redacted in errors and logs.

### Added

- Справочная половина описаний инструментов переехала из `tools/list` в MCP
  resources: у каждого инструмента есть ресурс `guide://<источник>/<инструмент>`
  с его секциями `## Return Format` и `## Error Format` — тем же текстом, слово
  в слово, — а общий справочник кодов (`marketplace://errors`) собирается из
  `ErrorCode` и классов ошибок, а не переписывается руками. Описание инструмента
  оставляет ядро (что инструмент отвечает и инварианты, влияющие на решение) и
  одну строку со ссылкой на свой гайд, поэтому половинки не могут разойтись.
  Профиль, монтирующий коннекторы, отдаёт каждый гайд один раз и
  `marketplace://errors` ровно один раз: `mcp_core.resources.mount_child` прячет
  копию дочернего сервера в листинге родителя, не отбирая её у самого
  коннектора, — иначе импорт объединённого сервера ломал бы отдельный
  `wb-mcp` в том же процессе. Это смена канала по требованию владельца, а не
  ослабление: `test_tool_docstring_sections.py` теперь требует ядро и парный
  ресурс вместо обеих секций в докстринге.
- Ядро описания сохранило не только первый абзац, но и инварианты, меняющие
  решение агента: отсутствующее значение — `null`, а не `0` (`price_rub`,
  `price_cny`, остатки MPStats); пустая выдача, неполный результат и
  неизвестный SKU — не ошибка, а здоровый ответ или `partial=True`; цена по
  подписке не участвует в ранжировании; `price_unit` у Cian говорит, что
  значит сумма; карточка Taobao не читает Tmall; для снимка страницы нужно
  владение handoff. Формулировки сжаты до одной строки перед ссылкой на гайд,
  сам гайд по-прежнему несёт исходный текст слово в слово.
- `scripts/mcp_wire.py` меряет всю цену профиля, а не только `tools/list`:
  читает `instructions` из ответа на `initialize`, пишет рядом с `wire_tokens`
  поле `instructions_tokens` и печатает обе величины и их сумму. Там же —
  абсолютный потолок профиля из `ceilings` в `work/performance/wire-baseline.json`
  (сумма инструментов и instructions): относительный гейт видит только рост
  относительно baseline, который обновляют руками. В режиме проверки отсутствие
  положительного потолка у проверяемого профиля — отказ, как и отсутствие
  baseline или бюджета; `--update-baseline` записывает потолок новому профилю
  (+5 % к замеру) и не трогает уже выставленный, потому что поднять потолок —
  решение, а не побочный эффект замера.
- The reference half of tool descriptions moved out of `tools/list` into MCP
  resources: every tool has a `guide://<source>/<tool>` resource holding its
  `## Return Format` and `## Error Format` sections — the same text, word for
  word — and one shared error-code reference (`marketplace://errors`) is built
  from `ErrorCode` and the connector error classes instead of being copied by
  hand. The description keeps the core (what the tool answers and the invariants
  that change the decision) plus one line naming its guide, so the two halves
  cannot drift apart. A profile that mounts connectors serves each guide once and
  `marketplace://errors` exactly once: `mcp_core.resources.mount_child` hides a
  child's copy from the parent's listing without taking it away from the
  connector, because deleting it made importing the unified server break a
  standalone `wb-mcp` in the same process. A channel change by the owner's
  order, not a weakening: `test_tool_docstring_sections.py` now requires the
  core plus the paired resource instead of both sections in the docstring.
- The core kept more than the first paragraph: the invariants that change what
  the agent concludes stay in the description, compressed to one line before the
  guide pointer — a missing value is `null`, never `0` (`price_rub`,
  `price_cny`, MPStats stock); an empty, partial or unknown-SKU result is not an
  error but a healthy response or `partial=True`; a subscription price never
  ranks; Cian's `price_unit` says what the amount means; a Taobao card cannot
  read a Tmall listing; a page snapshot needs ownership of the handoff. The
  guide still carries the original text word for word.
- `scripts/mcp_wire.py` measures a profile's whole cost, not only `tools/list`:
  it reads `instructions` from the initialize result, records
  `instructions_tokens` beside `wire_tokens`, and prints both plus their sum. It
  also checks an absolute per-profile ceiling from `ceilings` in
  `work/performance/wire-baseline.json` (tools plus instructions), because the
  relative gate only sees growth against a baseline that is updated by hand. In
  check mode a checked profile without a positive ceiling fails, like a missing
  baseline or budget; `--update-baseline` records a ceiling for a new profile
  (5% above the measurement) and leaves one already written alone, since raising
  a ceiling is a decision rather than a side effect of measuring.

- Дрейф структуры страницы виден в самом ответе инструмента: коннекторы, у
  которых есть реестр обязательных семейств ключей (avito, lamoda, taobao,
  citilink, dns), сверяют разобранный payload с этим реестром в общем
  хелпере `mcp_core.resilience.shape_drift_warning`, и потеря целого семейства
  даёт одно компактное предупреждение на вызов —
  `shape_drift: <источник>: missing <семейство>` — в существующем
  `meta.warnings` ответа (healthy становится false). Без новых сетевых
  запросов: полный payload оплачивает только само сравнение. Поведение CLI
  doctor не изменилось; yandex не покрыт — его парсер выдаёт все ключи
  безусловно, и его shape_reference прямо говорит, что охрана по наличию
  ключей там никогда не сработала бы.

- Parser drift is now visible in the tool's own response: the connectors
  that ship a required-key-family registry (avito, lamoda, taobao, citilink,
  dns) compare the parsed payload against it through the shared
  `mcp_core.resilience.shape_drift_warning`, and a whole family vanishing
  adds one compact warning per call — `shape_drift: <source>: missing
  <family>` — to the response's existing `meta.warnings` (healthy flips to
  false). No extra network requests: a complete payload costs only the
  comparison itself. CLI doctor behaviour is unchanged; yandex is not
  covered — its parser emits every item key unconditionally, and its
  shape_reference says outright that a key-presence guard would never fire
  there.

- Structural drift is now visible in six more tool responses: aliexpress,
  cian, detmir, megamarket, ozon and wb gained a `shape_reference` — the
  shape fingerprint of their search output (and card, where a captured
  fixture exists), measured by the real parser over the fixture — plus the
  same `shape_drift: <source>: missing <family>` warning through the shared
  `mcp_core.resilience.shape_drift_warning`. Where the parser emits every
  parsed key unconditionally (cian, detmir, megamarket, wb), the check runs
  on the raw envelope, against the alias families the parser binds through;
  ozon guards the one conditionally-emitted parser-critical key, the tile
  url (losing `action.link` silently strips navigation from every result).
  Their selfcheck canaries use the same registry. Yandex remains uncovered
  — its shape_reference documents why a key-presence guard would never fire
  there.

- Дрейф структуры виден в самом ответе ещё у шести коннекторов: aliexpress,
  cian, detmir, megamarket, ozon и wb получили свой `shape_reference` —
  отпечаток формы выдачи (и карточки, где есть захваченный фиксстур),
  измеренный настоящим парсером по фиксстуре, — и то же предупреждение
  `shape_drift: <источник>: missing <семейство>` через общий
  `mcp_core.resilience.shape_drift_warning`. Там, где парсер выдаёт все
  ключи безусловно (cian, detmir, megamarket, wb), проверка идёт по сырому
  конверту, по тем alias-семействам, через которые парсер биндится; у ozon
  охраняется единственный условно излучаемый критичный ключ — url тайла
  (без `action.link` каждый результат молча теряет навигацию). Их
  selfcheck-канарейки используют тот же реестр. Yandex по-прежнему не
  покрыт — его shape_reference объясняет, почему охрана по наличию ключей
  там никогда не сработала бы.

- У агента появился один read-only взгляд на транспорт: `marketplace_health`
  отвечает, жив ли Chrome на настроенном CDP-адресе (версия браузера и число
  вкладок, если отвечает), какие хосты остывают в брейкере и сколько ждать,
  какие челлендж-вкладки удержаны, и какую страницу открыть человеку, чтобы
  разблокировать каждый смонтированный источник, — по тем же статическим
  таблицам, которыми пользуется CLI. Инструмент ничего не тратит: не
  запускает Chrome, не создаёт и не закрывает вкладки, не ходит на площадки;
  он только читает `/json/version` и `/json/list` с таймаутом около двух
  секунд, а сломанная часть попадает в ответ (`cdp.reachable=false` рядом с
  `error`), а не в исключение. `budget_snapshot()` и `handoff_diagnostics()`
  перестали быть тестовым API: их видит агент (у удержанной вкладки — только
  имя папки профиля, без пути с именем пользователя ОС, и URL без query и
  фрагмента). У `marketplace_sources`
  появилось аддитивное поле `needs_cdp` — отсортированный список
  смонтированных источников, которые могут читаться через Chrome; полный
  список полей обоих инструментов лежит в их гайдах, докстринги не выросли.

- The agent gets one read-only transport-health view: `marketplace_health`
  answers whether Chrome is alive on the configured CDP address (browser
  version and page-target count when it is), which hosts sit in breaker
  cooldown and for how long, which challenge pages are retained, and which page
  a human opens to unblock each mounted source — from the same static tables
  the CLI uses. The tool spends nothing: it starts no Chrome, opens and closes
  no tab, and never calls a marketplace; it only reads `/json/version` and
  `/json/list` with a timeout of about two seconds, and a broken part lands in
  the answer (`cdp.reachable=false` beside `error`) instead of an exception.
  `budget_snapshot()` and `handoff_diagnostics()` are no longer test-only API:
  the agent sees them (a retained lease shows only the profile folder name, not
  the path with the OS user name, and its URL without query or fragment). `marketplace_sources` gained the additive `needs_cdp`
  field — the sorted mounted sources that can be read through Chrome. The full
  field lists of both tools live in their guides, so the docstrings did not
  grow.
- У проекта появился один реестр источников — `mcp_core.sources`: каноническое
  имя и алиасы, пакет, модуль, консольный скрипт, префикс инструментов, имя в
  таблице монтирования, ключ в конфиге клиента, selfcheck, карточный инструмент,
  число инструментов, уровень доступа, CDP и логин, валюта, текстовый поиск,
  страница разблокировки, признаки «стена — это логин» и «умеет diagnose_drift»,
  гейт монтирования и строка-подсказка для конфига. Из него выведены
  `KNOWN_SOURCES` и алиасы, таблица возможностей, таблицы CLI (конфиг и
  selfcheck), карта страниц `unblock`, список источников сравнения с карточными
  инструментами и валютной развилкой, счётчики инструментов в stdio-проверке и
  обе таблицы CI. Точки, которые не могут импортировать Python (`pyproject.toml`,
  `server.json`, а также литералы `mounts`, `SEARCHABLE` и таблиц
  `scripts/diagnose_drift.py`, которые разбирают существующие тесты), остались
  литералами, но новый тест сверяет каждую с реестром: пакет без записи и запись
  без пакета теперь падают. Одиннадцатый источник стоит одной записи, а не
  пятнадцати правок в пятнадцати местах. Первая же сверка нашла потерянную
  строку: в `server.json` не было cian.
- Общая сборка коннекторов переехала в `mcp_core.kit` и лежит там по одному
  разу: вызов пейсинга, хвост ошибки отказа (подсказка пейсера плюс обрезанная
  деталь), проверка хоста при разборе id товара, потоковое чтение с лимитом тела
  и отрисовка страницы через CDP с разбором JSON. `chrome_cdp` импортируется
  внутри `render_json`, а `curl_cffi` в mcp-core вообще не попадает: коннектор
  передаёт свою точку входа сам, поэтому тест, патчащий `cffi.get` в своём
  модуле, бьёт по тому же шву, что и раньше. Миграция не меняет ни поведение, ни
  провод: у каждого коннектора остаётся своя обёртка с тем же именем, которое
  патчат его тесты, а имена инструментов, докстринги, параметры, коды ошибок и
  тексты сообщений не тронуты. dns и citilink, которые совпадали почти целиком,
  делят общую часть и оставляют у себя только факты о своей площадке.

- One registry of sources now lives in `mcp_core.sources`: canonical name and
  aliases, package, module, console script, tool prefix, unified mount name,
  per-source client config key, selfcheck, card tool, tool count, access tier,
  CDP and login needs, currency, text search, unblock page, the login-wall and
  drift-probe flags, the mount gate and the config note. Derived from it:
  `KNOWN_SOURCES` and the aliases, the capability table, the CLI tables (config
  and selfcheck), the `unblock` page map, compare's source list with its card
  tools and foreign-currency split, the stdio check's tool counts and both CI
  tables. The sites that cannot import Python (`pyproject.toml`, `server.json`,
  and the `mounts`, `SEARCHABLE` and `scripts/diagnose_drift.py` literals
  existing tests parse) stay literal, but a new parity test ties each of them to
  the registry: a package without a record, or a record without a package, now
  fails. A sixth source costs one record instead of fifteen edits in fifteen
  places. The first comparison found a missing row: `server.json` had no cian
  entry.
- The shared connector assembly moved into `mcp_core.kit`, once: the pacing
  call, the refusal-error tail (the pacer's rotation hint plus a bounded
  detail), the host check when parsing a product id, the streamed read with its
  body cap, and the paced CDP render-and-parse. `chrome_cdp` is imported inside
  `render_json`, and curl_cffi never enters mcp-core at all: the connector
  passes its own entry point, so a test that patches `cffi.get` on its module
  still hits the seam it always did. The migration changes no behaviour and no
  wire bytes: every connector keeps its own wrapper under the name its tests
  patch, and tool names, docstrings, parameters, error codes and message texts
  are untouched. dns and citilink, which matched almost completely, share the
  mechanism and keep only their own site facts.

- `scripts/mcp_wire.py` теперь меряет и то, что платит модель, а не только весь
  `tools/list`: рядом с `wire_tokens` появилась величина `model_tokens` — оценка
  компактного `{"name", "description", "input_schema"}`, который запрос к
  провайдеру действительно несёт (у каждого инструмента и по профилю в
  `work/performance/wire-baseline.json`), тогда как `outputSchema`, аннотации и
  `_meta` остаются в клиенте. Поэтому рост описания за счёт сжатия схемы вывода
  больше не проходит гейт: относительная проверка
  `--max-token-regression-percent` считается по обеим величинам, каждая против
  своего значения в baseline, и падение называет виновника
  (`model_tokens_regression`). Baseline без `model_tokens` в режиме проверки
  падает так же, как без `wire_tokens`, а в режиме замера читается по-прежнему
  мягко. `--update-baseline` пишет обе величины сам, вручную файл править не
  нужно.
- `scripts/mcp_wire.py` now measures what the model pays too, not only the whole
  `tools/list`: beside `wire_tokens` it records `model_tokens`, the estimate of
  the compact `{"name", "description", "input_schema"}` a provider request
  really carries (per tool and per profile in
  `work/performance/wire-baseline.json`), while `outputSchema`, annotations and
  `_meta` stay in the client. A description growing behind a shrinking output
  schema therefore no longer passes the gate: the relative
  `--max-token-regression-percent` check applies to both measures, each against
  its own baseline value, and a failure names which one regressed
  (`model_tokens_regression`). A baseline without `model_tokens` fails in check
  mode exactly like one without `wire_tokens` and keeps the permissive reading
  in a measurement run. `--update-baseline` writes both measures itself, so the
  file needs no hand edit.

### Changed

- Публикация на PyPI включена: владелец настроил издателя 31 октября, и v2.7.0
  вышла на PyPI ручным запуском на своём теге. README и `docs/QUICKSTART.md`
  показывают установку без checkout (`uvx ru-marketplace-mcp setup`),
  `docs/DEPLOYMENT.md` и чеклист релиза описывают публикацию как включённую, а
  проверка после публикации смотрит страницу PyPI и
  `uvx ru-marketplace-mcp@X.Y.Z setup --dry-run`.
- Заметки к релизу v2.7.0 переписаны короче; подробности проверок остались в #152.
- Ошибка `challenge_required` стала честной для агента: флаг `retryable` остаётся
  `true` по документированному контракту, но добавлены `retryable_now=false`
  (сейчас повтор не поможет), `user_action` с настоящей командой
  `marketplace-mcp unblock <source>` или удержанной вкладкой и
  `retry_when=after_user_confirmation`. Сообщения Taobao, Lamoda и WB больше не
  обещают видимую вкладку, когда `CHROME_CHALLENGE_HANDOFF_S` её не удерживает, а
  WB называет свою ручную команду, потому что `marketplace-mcp unblock` его не
  покрывает.
- Открытый breaker хоста в бюджете CDP-навигации теперь доходит до агента как
  временный отказ, а не как общий транспортный сбой: `open_page` переводит
  `HostRefusingError` в существующий код `rate_limited` (`retryable=true` по
  документации), несёт остаток паузы в поле `retry_after_s`, хост — в `provider`,
  а сообщение говорит «paused for N s after M refusals». Раньше ни один коннектор
  не ловил это исключение: широкие обработчики превращали его в `transport_down`
  без паузы, а Avito проглатывал в `except Exception`, и агент повторял сразу
  вместо ожидания. Отображение одно и общее — в `open_page`; широкие обработчики
  на CDP-пути (Avito, Cian, Ozon) просто пропускают готовый `ToolError` дальше.
- Пауза хоста больше не проглатывается деградирующими обработчиками Megamarket.
  Адресная привязка, каталог-редирект и url/parse намеренно не фатальны для
  обычных сбоев эндпоинтов (слабый поиск лучше никакого, и это закреплено
  тестами), но временный отказ `rate_limited` с `retry_after_s` теперь проходит
  сквозь них как есть. Раньше обработчик адресной привязки проглатывал паузу и
  навсегда кэшировал вердикт «адрес не разрешён», из-за чего поиск и после паузы
  шёл без адреса доставки. Ошибки без паузы (например, «не авторизован» из
  профиля) сохраняют прежнее не фатальное поведение; путь карточки и раньше
  доносил паузу без искажений и не менялся. Для уже существующих защит Cian и
  Ozon добавлены отдельные офлайн-тесты (поиск и карточка), для Megamarket —
  тесты поиска и карточки.
- Унифицированный сервер (`marketplace-mcp`) больше не монтирует инструменты
  `mpstats_*`, когда в окружении нет непустого `MPSTATS_MP_AUTH`: каждый вызов
  всё равно вернул бы `auth_missing`, а две схемы стоили около 971 токена
  (≈6 % профиля) на каждом запросе. Источник попадает в `skipped` с причиной
  «токен не задан», `doctor` по-прежнему показывает его выключенным, а
  отдельный `mpstats-mcp` сохраняет прежнее поведение и отвечает `auth_missing`.

- PyPI publishing is on: the owner set up the publisher on 31 October, and v2.7.0
  went out to PyPI by a manual run on its tag. README and `docs/QUICKSTART.md`
  show the install without a checkout (`uvx ru-marketplace-mcp setup`);
  `docs/DEPLOYMENT.md` and the release checklist describe publishing as on, and
  the after-publication step checks the PyPI page and
  `uvx ru-marketplace-mcp@X.Y.Z setup --dry-run`.
- The v2.7.0 release notes are rewritten shorter; the check details stay in #152.
- The `challenge_required` error contract is now honest for the agent: `retryable`
  stays `true` per the documented contract and the body gains `retryable_now=false`
  (a plain retry now cannot pass), `user_action` naming the real
  `marketplace-mcp unblock <source>` command or the retained tab, and
  `retry_when=after_user_confirmation`. Taobao, Lamoda and WB messages no longer
  promise a visible tab when `CHROME_CHALLENGE_HANDOFF_S` keeps none, and WB names
  its own manual step because `marketplace-mcp unblock` does not cover it.
- An open host breaker in the CDP navigation budget now reaches the agent as a
  temporary refusal instead of a generic transport failure: `open_page` maps
  `HostRefusingError` to the existing `rate_limited` code (`retryable=true` as
  documented), carries the remaining pause in `retry_after_s`, the host in
  `provider`, and says "paused for N s after M refusals" in the message. No
  connector caught the exception before: broad handlers relabelled it
  `transport_down` with no pause, and Avito swallowed it in an `except Exception`,
  so the agent retried at once instead of waiting. The mapping is one shared
  place — `open_page`; the broad handlers on the CDP path (Avito, Cian, Ozon)
  just let the finished `ToolError` through.
- Megamarket's degrade handlers no longer swallow the host pause. Address
  resolution, the catalog redirect and url/parse are deliberately non-fatal for
  ordinary endpoint failures (a weaker search beats none, and tests pin that),
  but a `rate_limited` refusal with `retry_after_s` now passes through them
  unchanged. Before, the address handler swallowed the pause and cached a
  "no address" verdict for the process lifetime, so searches kept running
  addressless even after the cooldown. Non-pause errors (say, "not
  authenticated" from the profile endpoint) keep the existing non-fatal
  degrade; the card path already carried the pause intact and is unchanged.
  The existing Cian and Ozon guards gained dedicated offline tests (search and
  card each), and Megamarket got search and card tests.
- The unified server (`marketplace-mcp`) no longer mounts the `mpstats_*` tools
  when no non-empty `MPSTATS_MP_AUTH` is set: every call would return
  `auth_missing`, while the two schemas cost about 971 tokens (~6% of the
  profile) on every request. The source is reported in `skipped` with a
  missing-token reason, `doctor` still shows it as off, and the standalone
  `mpstats-mcp` keeps its behaviour and answers `auth_missing`.

### Security

- Секреты вычищаются на выходе самими функциями, а не только там, где автор
  вызова вспомнил про `_redact`: `log_event` прогоняет через правила `redact.py`
  имя события и все строки в полях, включая вложенные в списки и словари, а
  `raise_tool_error` — сообщение и строковые поля ToolError. Явные вызовы
  `_redact` остались и безвредны, потому что повторная чистка ничего не меняет;
  нестроковые значения не трогаются. Новых правил нет: формы токена, куки и
  учётных данных в URL те же.

- Значения секретных переменных окружения вычищаются целиком: правила
  `redact.py` теперь заменяют и точные текущие значения `MPSTATS_MP_AUTH` и
  `MCP_HTTP_AUTH_TOKEN` (реестр имён — в mcp-core), даже когда значение не
  похоже ни на одну известную форму. Значения читаются в момент чистки, а не
  при импорте; пустые и короче 6 символов игнорируются, а из двух
  пересекающихся значений сначала заменяется более длинное. Замена идемпотентна.
  Прокси-переменные не тронуты: их учётные данные закрывает прежнее правило
  для URL, а хост остаётся читаемым намеренно.

- `avito_card` открывает страницы в залогиненном Chrome оператора, поэтому
  полный URL принимается только с путём страницы объявления — `/<id>`,
  `/<slug>_<id>` или `/<город>/<категория>/<slug>_<id>` — и открывается ровно
  этот путь, без query и фрагмента. Путь вида `/<город>/<категория>/…` с
  первым сегментом из служебных маршрутов Avito (`api`, `profile`, `account` и
  т. п.) тоже отклоняется. Раньше путь из входного URL сохранялся
  целиком, и путь с «хвостом» идентификатора после личного префикса (например,
  `/profile/x_1234567890`) уходил в браузер оператора. Теперь любой другой
  путь (`/profile`, `/account`, `/orders`, `/favorites`, `/api/...`, путь
  после идентификатора) отклоняется как `bad_request` до навигации.

- Secrets are scrubbed on the way out by the functions themselves, not only where
  a caller remembered `_redact`: `log_event` runs the `redact.py` rules over the
  event name and every string in its fields, including strings nested in lists and
  dicts, and `raise_tool_error` does the same for the message and string fields of
  the ToolError. The explicit `_redact` calls stay and are harmless because a
  second pass changes nothing; non-string values are left alone. No new rules:
  the token, cookie and URL-credential shapes are unchanged.

- Secret environment-variable values are scrubbed whole: the `redact.py` rules
  now also replace the exact current values of `MPSTATS_MP_AUTH` and
  `MCP_HTTP_AUTH_TOKEN` (the name registry lives in mcp-core), even when the
  value matches no known shape. Values are read at redaction time, not import
  time; empty and shorter-than-6 values are ignored, and of two overlapping
  values the longer one is replaced first. The replacement is idempotent.
  Proxy variables are untouched: their credentials are covered by the existing
  URL rule and the host is deliberately kept readable.
- `avito_card` открывает страницы в залогиненном Chrome оператора, поэтому
  полный URL принимается только с путём страницы объявления — `/<id>`,
  `/<slug>_<id>` или `/<город>/<категория>/<slug>_<id>` — и открывается ровно
  этот путь, без query и фрагмента. Путь вида `/<город>/<категория>/…` с
  первым сегментом из служебных маршрутов Avito (`api`, `profile`, `account` и
  т. п.) тоже отклоняется. Раньше путь из входного URL сохранялся
  целиком, и путь с «хвостом» идентификатора после личного префикса (например,
  `/profile/x_1234567890`) уходил в браузер оператора. Теперь любой другой
  путь (`/profile`, `/account`, `/orders`, `/favorites`, `/api/...`, путь
  после идентификатора) отклоняется как `bad_request` до навигации.

- `avito_card` opens pages in the operator's logged-in Chrome, so a full URL
  is accepted only with an item-page path — `/<id>`, `/<slug>_<id>` or
  `/<city>/<category>/<slug>_<id>` — and exactly that path is opened, without
  query or fragment. A `/<city>/<category>/…` path whose first segment is one of
  Avito's own routes (`api`, `profile`, `account` and the like) is refused too. Previously the input URL's path was kept as-is, so a
  path with an item-id tail after a personal prefix (for example,
  `/profile/x_1234567890`) reached the operator's browser. Any other path
  (`/profile`, `/account`, `/orders`, `/favorites`, `/api/...`, a path
  continuing past the id) is now refused as `bad_request` before navigation.
### Fixed
- Ночная канарейка больше не падает с ложным «дрейф или блок», когда бюджет
  живых запросов WB уже потратил шаг doctor (он теперь идёт первым на том же
  журнале): тест, запрос которого бюджет отказал, корневой `conftest.py`
  отмечает пропущенным с причиной от бюджета. Канарейка печатает причины
  пропусков, а сводка при пропусках пишет «PASS with skips», а не «все тесты
  прошли». Лимиты бюджета не менялись.
- `coerce_price` отказывает и на множителях заглавными буквами (`15 ТЫС. ₽`,
  `2 Млн ₽`): раньше ловилось только `тыс`/`млн` строчными.

- `avito_card` больше не принимает путь товара с обратным слешем или знаком
  процента. `https://www.avito.ru/profile\x_1234567890` и
  `/profile%2Fx_1234567890` проходили проверку формы — регулярка видит в них
  обычные символы, — не попадали под запрет служебных разделов и открывались в
  браузере оператора как `/profile/x_1234567890`: Chromium считает `\`
  разделителем пути в http(s), а сервер раскодирует `%2F` и `%5C`. Теперь
  такой путь отклоняется с `bad_request` до навигации, как и остальные
  не-товарные адреса; обычные слаги вида
  `/moskva/telefony/iphone_15_1234567890` работают как раньше.

- Отказы девяти мест AliExpress (`_unwrap_extract`, карточка через popup,
  челлендж x5sec) и оба отказа `mcp_core.kit.render_json` уходят агенту как
  общий JSON ошибки (`raise_tool_error`), а не голым текстом: раньше
  `str(ToolError(TransportDownError(...)))` было просто сообщением — без кода,
  без `retryable` и полей челленджа, — а превью страницы в 200 символов в
  тексте ошибки не вычищалось. Челлендж x5sec теперь `challenge_required`
  (`requires_user_action: true`, `retryable_now: false`, `user_action` с
  ручным шагом), как у x5sec на Taobao, а не `transport_down`, на который
  агент повторял бы запрос впустую. Не-JSON выдача экстрактора в
  `render_json` теперь тоже `parser_drift`, а не голый `JSONDecodeError`.

- `run_server` ставит на каждом транспорте (stdio и HTTP) middleware
  `ToolErrorRedactionMiddleware`. FastMCP сам превращает неожиданное исключение
  внутри инструмента в `ToolError("Error calling tool ...: <текст исключения>")`,
  и это происходит вне обработчиков коннектора: `build_client(...)` в yandex и
  detmir стоит вне `try`, поэтому `ValueError` на прокси `htp://user:pass@host`
  уносил учётные данные прямо в ответ. Middleware перевыпускает `ToolError` с
  вычищенным текстом (сохраняя `__cause__`), пропускает остальные исключения без
  изменений и не укорачивает сообщение — уже вычищенный JSON ошибки доходит до
  агента байт в байт.

- Ozon больше не отдаёт сырой текст апстрима. Тела страниц в `last_error`
  успешного `ozon_reviews` и исключение Tier-1 в `ctx.debug` проходят через
  `_redact`: раньше токен из тела ошибки попадал и в ответ агенту, и в лог
  клиента.

- `avito_card` no longer accepts an item path containing a backslash or a
  percent sign. `https://www.avito.ru/profile\x_1234567890` and
  `/profile%2Fx_1234567890` passed the shape check — the regex reads those as
  ordinary characters — missed the service-route deny-list and opened in the
  operator's browser as `/profile/x_1234567890`: Chromium treats `\` as a path
  separator in http(s) URLs and a server decodes `%2F` and `%5C`. Such a path is
  now refused with `bad_request` before any navigation, like the other
  non-item addresses; ordinary slugs such as
  `/moskva/telefony/iphone_15_1234567890` keep working.

- Nine AliExpress failure sites (`_unwrap_extract`, the popup card path and the
  x5sec challenge) and both `mcp_core.kit.render_json` refusals now reach the
  agent as the shared error JSON (`raise_tool_error`) instead of plain text:
  `str(ToolError(TransportDownError(...)))` used to be the bare message, with no
  code, no `retryable` and no challenge fields, and the 200-character page
  preview in it was never redacted. The x5sec challenge is now
  `challenge_required` (`requires_user_action: true`, `retryable_now: false`,
  a `user_action` naming the manual step), like Taobao's x5sec, instead of a
  `transport_down` the agent would retry in vain. A non-JSON extract in
  `render_json` is now `parser_drift` too, not a bare `JSONDecodeError`.

- `run_server` installs `ToolErrorRedactionMiddleware` on every transport
  (stdio and HTTP). FastMCP itself turns an unexpected exception inside a tool
  into `ToolError("Error calling tool ...: <exception text>")`, outside the
  connector's own handlers: `build_client(...)` sits outside the `try` in yandex
  and detmir, so a `ValueError` on a `htp://user:pass@host` proxy carried the
  credentials straight into the answer. The middleware re-raises a `ToolError`
  with the redacted text (keeping `__cause__`), passes every other exception
  through unchanged and never truncates the message, so an already-redacted
  error JSON reaches the agent byte-identical.

- Ozon no longer returns raw upstream text. Page bodies quoted in the
  `last_error` of a successful `ozon_reviews` and the Tier-1 exception logged
  through `ctx.debug` go through `_redact`: a token in an upstream error body
  used to reach both the agent's answer and the client log.

- Дрейф формы WB больше не срабатывает на законной странице со снятыми с
  продажи товарами: у неё у всех товаров `"sizes": []`, а ценовое семейство
  живёт внутри `sizes[]`, поэтому общий матчер
  `mcp_core.resilience.shape_drift_warning` объявлял семейство потерянным.
  Теперь пустой контейнер (сигнатура пишет его как `<путь>:empty_array`) не
  считается потерей ключа внутри себя — искать там нечего. Правило узкое:
  работает только для вложенного массива (не для конверта выдачи, так что
  пустой `items: []` рядом с заполненным `goods[]` дрейф не прячет) и только
  если этот массив пуст у всех элементов; записи `sizes`, которые есть, но
  потеряли ключ цены, остаются дрейфом, в том числе на смешанной странице.
- Avito: реестр `SEARCH_REQUIRED_FAMILIES` получил алиасы третьего конверта
  `data.items[]`, который `_parse_search_items` уже читает. Раньше ответ,
  отданный только через `data.items`, разбирался, но все семейства читались как
  потерянные — ложная тревога; потерянное семейство внутри этого конверта
  по-прежнему называется своим именем.
- Заголовки `shape_reference.py` у aliexpress, citilink, dns, lamoda и taobao
  теперь прямо говорят, по какому payload идёт проверка семейств: это выход
  самого экстрактора, где каждый ключ выдаётся на каждой плитке, поэтому дрейф
  разметки площадки приходит к агенту пустыми значениями и ловится
  `validate_items` (`no_prices_on_page`, `title_missing`), а пропавший ключ
  значит, что экстрактор перестал его выдавать. У aliexpress исправлено
  обратное утверждение, будто экстрактор выдаёт ключи условно.
- Ночной job: шаг doctor с аварийным сигналом идёт ПЕРЕД канарейкой. Оба шага
  пишут в один журнал живых запросов (`.live-budget.json` в корне репозитория,
  8 запросов на площадку за прогон, ни один шаг его не сбрасывает), поэтому
  канарейка, идя первой, тратила бюджет WB на свои live-тесты, `wb_selfcheck`
  у doctor'а получал отказ, читался как inconclusive (2) и job оставался
  зелёным при настоящем дрейфе WB. Сигнал важнее: канарейка остаётся
  информационной и зелёной, как и была.
- `scripts/live_check.py` отдаёт код 4 на собственные ошибки использования и на
  неудачную установку бюджета вместо прежней двойки, а ночной job валит шаг на
  4. Двойка у doctor'а значит «источники не удалось проверить, остаёмся
  зелёными» — правильно для заблокированной площадки, но сломанный вызов
  `live_check` не должен прятаться в том же коде.
- The nightly canary no longer fails with a false "drift or block" when the
  doctor step (now first on the same ledger) has already spent WB's live
  budget: the root `conftest.py` reports a test whose request the budget
  refused as skipped with the budget's reason. The canary prints skip reasons
  and its summary says "PASS with skips" instead of "all live tests passed".
  The budget caps are unchanged.
- `coerce_price` refuses magnitude words in any case (`15 ТЫС. ₽`, `2 Млн ₽`);
  only lowercase `тыс`/`млн` were caught before.
- WB shape drift no longer fires on the legitimate page whose products are all
  delisted: every product carries `"sizes": []` and the price family lives
  inside `sizes[]`, so the shared matcher
  `mcp_core.resilience.shape_drift_warning` reported the family as lost. An
  empty container (the signature writes it as `<path>:empty_array`) is no
  longer read as a lost key inside itself — there is nothing in it to lose.
  The rule is narrow: only a nested array counts (not the result envelope, so
  an empty `items: []` next to a filled `goods[]` does not hide drift), and
  only when it is empty on every element; `sizes` entries that exist without
  a price key stay drift, on a mixed page too.
- Avito: `SEARCH_REQUIRED_FAMILIES` gained the aliases of the third envelope
  `data.items[]`, which `_parse_search_items` already read. A payload served
  only through `data.items` parsed fine but every family read as missing — a
  false alarm; a family actually lost inside that envelope is still named.
- The `shape_reference.py` headers of aliexpress, citilink, dns, lamoda and
  taobao now state what the family check runs on: the extractor's own output,
  where every key is emitted on every tile, so marketplace markup drift reaches
  the agent as empty values caught by `validate_items` (`no_prices_on_page`,
  `title_missing`) while a vanished key means the extractor stopped emitting
  it. AliExpress carried the opposite claim that the extractor emits keys
  conditionally; that is corrected.
- The nightly job now runs the doctor alarm step BEFORE the canary. Both steps
  book into one live-request ledger (`.live-budget.json` at the repository
  root, 8 requests per source per run, neither step resets it), so a canary
  running first spent WB's budget on its live tests, the doctor's
  `wb_selfcheck` was refused, read inconclusive (2) and the job stayed green on
  real WB drift. The alarm has priority; the canary stays informational and
  green as before.
- `scripts/live_check.py` returns 4 for its own usage errors and a failed
  budget install instead of the old 2, and the nightly job fails the step on 4.
  The doctor's 2 means "sources could not be judged, stay green" — right for a
  blocked marketplace, but a broken `live_check` invocation must not hide in
  the same code.
- `coerce_rating` больше не выдаёт рейтинг из счётчика отзывов: строка,
  содержащая число, сгруппированное по тысячам пробелом (обычным, неразрывным
  U+00A0, узким неразрывным U+202F или тонким U+2009), теперь даёт `None` и
  с буквами, и без них. Раньше правило требовало букв, поэтому голое
  «1 299» превращалось в выдуманный рейтинг 1.0. Текст без букв с запятой не
  тронут: «4,9» остаётся рейтингом, а «4,500» читается как раньше.
- `coerce_rating` no longer turns a review counter into a rating: a string
  containing a number grouped by thousands with a space (plain, no-break
  U+00A0, narrow no-break U+202F or thin U+2009) is now None with or without
  letters. The old rule required letters, so a bare '1 299' came back as a
  fabricated 1.0 rating. Letter-free comma text is untouched: '4,9' stays a
  rating and '4,500' keeps its old reading.
- `server.json` получил потерянную строку cian: список серверов для реестра
  MCP-серверов перечислял четырнадцать площадок из пятнадцати, и cian в нём
  отсутствовал. Теперь этот список сверяется с реестром источников тестом.

- `server.json` gained its missing cian row: the server list for the MCP
  registry named fourteen of the fifteen marketplaces, and cian was absent. A
  test now compares that list with the source registry.
- Цена, которую парсер не смог получить, больше не проходит молча. `validate_offer`
  теперь сообщает о неразобранной цене (`price_unparseable`), о пустой или
  пробельной строке цены (`price_blank`), умеет читать ценовое поле коннектора
  (`price_cny` у Taobao) и видит противоречие `in_stock` с неразобранной ценой;
  раньше пробельная цена не давала ни одного предупреждения. Новый общий
  `validate_items` собирает поверки по позициям в одно предупреждение на код,
  когда сломана хотя бы половина выдачи, и подставляет привычный код
  `no_prices_on_page`, если цены нет ни у одной позиции. Он подключён к поиску и
  карточкам avito, cian, citilink, detmir, dns, lamoda, megamarket, taobao и
  yandex (у wb и ozon такая проверка уже была); mpstats не подключён — его
  строки это аналитические метрики, а не нормализованные заявки с ценой и
  заголовком.

- `meta.healthy` означает одно и то же во всех коннекторах: `true` только когда
  ответ полный и его значениям можно верить, `false` при любом предупреждении о
  дрейфе, валидации или полноте — включая предупреждение, которое коннектор
  пишет в модель ответа вручную (общий хелпер `health_warnings` отделяет
  информационные заметки от сомнений в данных). Предупреждение с префиксом
  `info:` — это контекст, а не сомнение в данных, и флаг не гасит (так помечена
  заметка WB о том, что регион задаёт браузер, а не `dest`). В WB это
  исправляет `healthy=True` рядом с непустыми предупреждениями (поиск через DOM,
  `wb_categories`), а карточка через DOM, у которой не разобрался ни один
  запрошенный SKU, теперь несёт `no_items` вместо `healthy=true` с пустым
  списком. Три-состояние канарейки (healthy/drift/inconclusive) не изменилось.

- Пустой ответ и отказ площадки больше не выглядят успехом: поиск WB через DOM
  на странице ≥2 без плашки «ничего не нашлось» отдаёт `no_results` с
  предупреждением `page_end_unverified` и `healthy=false` (страница 1
  по-прежнему даёт `parser_drift`, а пустая страница с плашкой остаётся
  честной); пустой ответ API WB, когда v9 честно пуст и legacy-фолбэк упал,
  несёт `fallback_failed` и `healthy=false`; megamarket помечает страницу без
  единой цены (`no_prices_on_page`) и проверяет карточку (`no_price`); в
  `compare_prices` источник с нулём заявок считается `ok` только если его
  собственный ответ не сообщает о проблеме, иначе это `unhealthy` с
  `unhealthy_empty` и `complete=false`. Докстрока `ozon_search` приведена в
  соответствие с кодом: ноль позиций — это предупреждение `no_items` и
  `healthy=false`.

- A price the parser could not obtain no longer passes in silence. `validate_offer`
  now reports an unparseable price (`price_unparseable`), a blank or
  whitespace-only price string (`price_blank`), reads a connector's own price
  field (`price_cny` on Taobao) and sees `in_stock` contradicting an unparsed
  price; a whitespace price used to draw no warning at all. The new shared
  `validate_items` rolls per-item findings into one warning per code when at
  least half the page breaks it, and substitutes the established
  `no_prices_on_page` code when not one item carries a price. It is wired into
  the search and card tools of avito, cian, citilink, detmir, dns, lamoda,
  megamarket, taobao and yandex (wb and ozon already had such a check); mpstats
  is excluded because its rows are analytics metrics, not normalized offers with
  a title and a price.

- `meta.healthy` means one thing in every connector: `true` only when the answer
  is complete and its values can be trusted, `false` for any drift, validation or
  completeness warning — including one a connector writes into its response model
  by hand (the shared `health_warnings` helper separates informational notes from
  doubts about the data). A warning prefixed `info:` is context rather than doubt
  about the data and does not flip the flag (WB's note that the region comes from
  the browser, not from `dest`, is marked that way). In WB this fixes
  `healthy=True` next to non-empty warnings (DOM search, `wb_categories`), and a
  DOM card whose every requested SKU failed to parse now carries `no_items`
  instead of `healthy=true` with an empty list. The tri-state canary
  (healthy/drift/inconclusive) is unchanged.

- An empty answer and a marketplace refusal no longer read as success: WB DOM
  search on page 2 or later without the «nothing found» notice returns
  `no_results` with a `page_end_unverified` warning and `healthy=false` (page 1
  still raises `parser_drift`, and an empty page WITH the notice stays honest);
  an empty WB API answer where v9 is positively empty and the legacy fallback
  died carries `fallback_failed` and `healthy=false`; megamarket marks a page
  where not one price alias matched (`no_prices_on_page`) and validates its card
  (`no_price`); in `compare_prices` a source with zero offers counts as `ok`
  only when its own answer reports no problem, otherwise it is `unhealthy` with
  `unhealthy_empty` and `complete=false`. The `ozon_search` docstring now matches
  the code: zero items means a `no_items` warning and `healthy=false`.

- Неудачный автозапуск Chrome запоминается на 45 секунд
  (`_AUTOSTART_FAILURE_TTL_S`): следующие вызовы, включая уже ждущие блокировку,
  получают тот же отказ сразу, с подсказкой о возрасте попытки, вместо
  повторного ожидания готовности CDP. Успешный запуск (в том числе
  `browser start`/`start_browser`) память очищает, а явный запуск всегда
  пробует по-настоящему.

- A failed Chrome autostart is remembered for 45 seconds
  (`_AUTOSTART_FAILURE_TTL_S`): later callers, including those already waiting
  on the lock, get the same failure at once with an age hint instead of
  repeating the CDP readiness wait. A success (also `browser start` /
  `start_browser`) clears the memory, and an explicit start always tries for
  real.
- Раннер оценки маршрутизации (`scripts/model_routing_eval.py run`) больше не
  ставит `ok: true` прогону с меньшим числом сэмплов, чем требует протокол:
  обёртка с `samples_per_case` меньше 3 или не целым числом теперь ошибка.
  Голая карта ответов по-прежнему принимается, но отчёт пишет в
  `protocol_unchecked`, что сэмплирование не проверено.

- The routing eval runner (`scripts/model_routing_eval.py run`) no longer reports
  `ok: true` for a run with fewer samples than the protocol asks: a wrapper with
  `samples_per_case` below 3 or not an integer is now a problem. A bare answer
  map is still accepted, and the report says in `protocol_unchecked` that its
  sampling was not checked.
- `compare_prices` больше не считает «1 ТБ» и «1024 ГБ» разными размерами:
  размеры сравниваются в гигабайтах (1 ТБ = 1024 ГБ, как продают память
  телефонов и ноутбуков). «1000 ГБ» по-прежнему не совпадает с «1 ТБ».

- `compare_prices` no longer treats "1 TB" and "1024 GB" as different sizes:
  sizes are compared in gigabytes (1 TB = 1024 GB, as phone and laptop storage
  is sold). "1000 GB" still does not match "1 TB".
- `compare_prices` сохраняет тип цены в новом необязательном `price_kind`:
  цена с WB Кошельком, по подписке или с другим явным условием не выбирается
  в `cheapest` и `cheapest_comparable`. Если обычная цена известна, ранжируется
  она; иначе `price_rub` остаётся `null`, а исходная сумма — в `price_native`.
  Предупреждения называют условие и исключённую сумму, а сумму с WB Кошельком —
  и рядом с обычной ценой. Зачёркнутая цена не подставляется вместо обычной.

- `compare_prices` preserves the price kind in the new optional `price_kind`:
  WB Wallet, subscription and other explicitly conditional prices cannot become
  `cheapest` or `cheapest_comparable`. The regular price ranks when available;
  otherwise `price_rub` stays `null` and the quoted amount remains in `price_native`.
  Warnings name the condition and excluded amount, and a WB Wallet amount also
  next to a regular price. A struck-through price is never substituted for the regular one.
- Гейт стоимости MCP-схем падает закрыто: в режиме проверки (`--baseline` без
  `--update-baseline`) отсутствующий или нечитаемый файл базлайна и проверяемый
  профиль без бюджета `wire_tokens` — теперь ошибка с понятным сообщением, а не
  молчаливый пропуск любого снимка. Обычная проверка по закоммиченному базлайну и
  создание базлайна через `--update-baseline` работают как раньше. Ночной прогон
  дополнительно запускает `marketplace-mcp doctor --status-file` (через
  `scripts/live_check.py`, под общим бюджетом живых запросов) и падает только на
  коде «парсер дрейфовал» (1); заблокированные источники (2) не будят никого.
  Live-тесты самопроверки считают `drift_detected` провалом, а `inconclusive`
  (блок, челлендж, сеть) — скипом, не провалом.

- The MCP wire-cost gate fails closed: in gate mode (`--baseline` without
  `--update-baseline`) a missing or unreadable baseline file and a checked
  profile without a `wire_tokens` budget are clear failures, not a silent pass
  of any snapshot. The normal regression check against the committed baseline
  and baseline creation with `--update-baseline` keep working. The nightly job
  additionally runs `marketplace-mcp doctor --status-file` (through
  `scripts/live_check.py`, under the shared live-request budget) and fails only
  on the parser-drift exit code (1); blocked sources (2) page no one. Live
  selfcheck tests treat `drift_detected` as a failure and `inconclusive`
  (block, challenge, network) as a skip, not a failure.
- `coerce_price`, `coerce_int` и `coerce_rating` из `mcp_core.resilience` больше не
  выдумывают число из неоднозначного текста. Цены: две цены, склеенные пробелом
  («1 999 2 999», «1 999 2 999 ₽», «1,999,2,999»), дробь с тремя знаками
  («3 983.999»), месячный платёж («от 5 751 ₽/ мес.»), выгода («выгода 5 000 ₽»)
  и скидка («скидка 30%») дают `None`, а `validate_offer` помечает такую цену.
  Счёт с суффиксом в любой письменности («12万») или со знаком валюты
  («24 088₽») и рейтинг с не-пятибалльным знаменателем словами («4.5 из 10»)
  тоже дают `None`. Цена «от 1 999 ₽» и обычные формы («1 999 ₽»,
  «1 999,50 ₽», «1999.00») работают по-прежнему.

- `coerce_price`, `coerce_int` and `coerce_rating` in `mcp_core.resilience` no
  longer turn ambiguous text into a number. Prices: two prices glued by the
  thousands-separator space ("1 999 2 999", "1 999 2 999 ₽", "1,999,2,999"), a
  three-digit fraction ("3 983.999"), a monthly instalment
  ("от 5 751 ₽/ мес."), a saving ("выгода 5 000 ₽") and a discount
  ("скидка 30%") return `None`, and `validate_offer` flags such a price. A count
  with a magnitude letter in any script ("12万") or a currency glyph ("24 088₽"),
  and a rating with a non-5 denominator in words ("4.5 из 10"), also return
  `None`. The "from" price ("от 1 999 ₽") and the ordinary shapes
  ("1 999 ₽", "1 999,50 ₽", "1999.00") keep working.
- Бюджет навигаций CDP: запрос, стоявший в очереди, когда у хоста открылся
  размыкатель, больше не получает разрешение — когда подходит его очередь, он
  получает тот же `HostRefusingError`, что и новый запрос, а взятые слоты
  возвращаются сразу. Ожидание занятого хоста больше не занимает глобальный
  слот, поэтому запрос к свободному хосту не ждёт за занятым. Освобождение
  слотов при отказе и при отмене (`asyncio.CancelledError`) ровно одно.
- `detmir_card` больше не выдаёт свежий ответ за кешированный: `meta.cached`
  снимается до запроса, а `_fetch_json` пишет свежий ответ в кеш, поэтому первое
  сетевое обращение теперь `cached=false`, а повтор — `cached=true`.
- Из всех коннекторов убрана бесполезная привязка FastMCP `RetryMiddleware`: она
  ретраит только сырые `ConnectionError`/`TimeoutError`, а коннекторы отдают
  `mcp_core` `ToolError`, поэтому не срабатывала ни разу. Повторные попытки
  остаются за HTTP-слоем; `mcp-core` монтирует только `BearerAuthMiddleware`.

- CDP navigation budget: a request queued while its host's breaker opened is no
  longer handed a permit — when its turn comes it is refused with the same
  `HostRefusingError` a new request gets, and the permits are given back at once.
  Waiting for a busy host no longer holds a global slot, so a request for a free
  host is not delayed behind it. Slots are released exactly once on refusal and
  on cancellation (`asyncio.CancelledError`).
- `detmir_card` no longer passes off a fresh answer as cached: `meta.cached` is
  sampled before the fetch, and `_fetch_json` writes the fresh payload into the
  cache, so a first network-backed call is now `cached=false` and a replay is
  `cached=true`.
- Every connector dropped its inert FastMCP `RetryMiddleware` mount: it retries
  only raw `ConnectionError`/`TimeoutError`, while connectors raise an `mcp_core`
  `ToolError`, so it never fired. Bounded retries stay in the HTTP tier;
  `mcp-core` mounts only `BearerAuthMiddleware`.

- `compare_prices` больше не выбирает в `cheapest_comparable` вариант другого
  объёма: размеры, названные в запросе (`128GB`, `128 ГБ`, `1 ТБ`),
  сравниваются с размерами в заголовке каждой заявки. Заявка, в заголовке
  которой нет ни одного запрошенного размера, остаётся в `offers` и может быть
  простым `cheapest`, но не `cheapest_comparable`; заголовок без размера или
  запрос без размера не исключают ничего, а `8/128GB` не противоречит запросу
  `128GB`. Об исключении сообщает одно компактное предупреждение
  `identity_mismatch` на вызов.

- `compare_prices` no longer picks a different-capacity variant as
  `cheapest_comparable`: sizes stated in the query (`128GB`, `128 ГБ`, `1 ТБ`)
  are compared with the sizes named in each offer title. An offer whose title
  names none of the asked sizes stays in `offers` and may be plain `cheapest`,
  but never `cheapest_comparable`; a title without a size, or a query without
  one, excludes nothing, and `8/128GB` does not contradict a `128GB` query. One
  compact `identity_mismatch` warning per call reports the exclusions.

- `compare_prices` больше не теряет поля восстановления из ошибки источника:
  `SourceOutcome` несёт `retry_after_s` (сколько ждать после открытого
  предохранителя CDP-хоста), `retryable_now`, `retry_when` и `user_action`
  (контракт `challenge_required`), а описание `status` называет `unhealthy`.
  Раньше через эту границу проходили только `error_code` и `retryable`, поэтому
  «повторите» приходило без времени ожидания, а «нужно действие человека» — без
  самого действия.
- `compare_verify_offer` больше не подтверждает условную цену: карточка WB, у
  которой `price_rub` — цена Wallet (`price_kind="wallet"`, DOM-транспорт),
  получает `observed_price_rub=null`, `delta_rub=null`, `matches=null` и
  названный `price_kind` вместо вердикта о цене, доступной только владельцу
  Wallet.
- `wb_search` больше не падает на честно пустом ответе v9: отказ legacy-фолбэка,
  поднятый обычным `ToolError` (через `raise_tool_error`), теперь тоже ловится,
  поэтому ответ остаётся `no_results` с `fallback_failed` и `healthy=false`, а
  предупреждение называет код и сообщение фолбэка. Если v9 уже упал, ошибка
  по-прежнему поднимается наверх.
- `coerce_price` использует единственный список `DECOY_MARKERS` (переехал в
  `mcp_core.resilience`, из него же генерируется decoy-regex DOM-слоя), поэтому
  «Рассрочка 5 751 ₽» и «в 1356 пунктов» больше не становятся ценой; суффикс
  магнитуды («12万», «1.2K») на ценовом пути отвергается, как в `coerce_int`, а
  `coerce_rating` отвергает счётчик без своего разделителя («1 2999»).
- Приведены в соответствие тексты: докстроки `ozon_search` (ноль позиций —
  `no_items` и `healthy=false`) и `megamarket_search` (любое предупреждение без
  префикса `info:` гасит `healthy`), справочник `marketplace://errors`
  перечисляет `retryable_now`, `retry_when` и `user_action`, докстрока
  `registered_tools` говорит, что возвращает локальные инструменты, а склейка
  предложений в `instructions` девяти серверов получила потерянный пробел.

- `compare_prices` no longer drops the recovery fields of a source error:
  `SourceOutcome` carries `retry_after_s` (how long to wait after an open CDP
  host breaker), `retryable_now`, `retry_when` and `user_action` (the
  `challenge_required` contract), and the `status` description names
  `unhealthy`. Only `error_code` and `retryable` used to cross that boundary, so
  "retry" arrived with no clock and "a human must act" with no action.
- `compare_verify_offer` no longer certifies a conditional price: a WB card
  whose `price_rub` is the Wallet price (`price_kind="wallet"`, DOM transport)
  answers `observed_price_rub=null`, `delta_rub=null`, `matches=null` and the
  named `price_kind` instead of a verdict on a price only a Wallet holder gets.
- `wb_search` no longer dies on a positively empty v9 answer: a legacy-fallback
  refusal raised as a plain `ToolError` (through `raise_tool_error`) is caught
  too, so the answer stays `no_results` with `fallback_failed` and
  `healthy=false`, and the warning names the fallback's code and message. When
  v9 already failed, the error is still raised as before.
- `coerce_price` uses the one `DECOY_MARKERS` list (moved to
  `mcp_core.resilience`, which also generates the DOM decoy regex), so
  'Рассрочка 5 751 ₽' and 'в 1356 пунктов' are no longer read as prices; a
  magnitude suffix ('12万', '1.2K') is refused on the price path exactly as
  `coerce_int` refuses it, and `coerce_rating` refuses a counter missing its own
  separator ('1 2999').
- Texts corrected: the `ozon_search` docstring (zero parsed items means
  `no_items` and `healthy=false`) and the `megamarket_search` docstring (any
  warning without the `info:` prefix flips `healthy`), the
  `marketplace://errors` reference now lists `retryable_now`, `retry_when` and
  `user_action`, the `registered_tools` docstring says it returns local tools
  only, and the appended sentence in nine servers' `instructions` got its
  missing space back.

## [2.7.0] - 2026-10-30

Установка и первый запуск: `install --write` с профилями и мастер `setup`, Chrome и проверка сайтов из CLI, фолбэк Wildberries на отрисованную страницу при отказе API, сгенерированный справочник инструментов и подготовка к PyPI (публикация выключена).

Install and first run: `install --write` with profiles and a `setup` wizard, Chrome and site checks from the CLI, a Wildberries fallback to the rendered page when the API refuses, a generated tool reference, and PyPI preparation (publishing off).

### Added

- Подготовлен один дополнительный PyPI-бандл `ru-marketplace-mcp`: скрипт
  `scripts/build_pypi_bundle.py` выводит метаданные из манифестов workspace,
  отказывает при конфликте зависимостей или консольных имён и собирает wheel/sdist
  со всеми импортами и `py.typed`; README идёт в описание пакета, а не файлом в site-packages. Алиас `ru-marketplace-mcp` ведёт в CLI;
  workspace, его 16 отдельных пакетов и Docker остаются. Общие минимумы
  `curl-cffi` и Playwright согласованы с более высокими уже существовавшими
  требованиями; версии runtime в lock-файле не меняются. Новый CI job проверяет
  wheel в чистом pip-venv. Trusted publishing выключен до регистрации владельцем
  одного pending publisher и включения переменной; шаги — в DEPLOYMENT.md.
- `install` и `setup`, запущенные из кеша uv tool, пишут абсолютный путь к uv и
  `tool run --from ru-marketplace-mcp==<версия>` вместо временной консольной
  команды. Вывод объясняет пин релиза и обновление через `install --write --force`;
  запуск из checkout и обычного venv остаётся прежним.

- `wb_card` и `wb_search` в режиме `api` сами перечитывают запрос через
  отрисованную страницу в scraping-Chrome, когда API WB отказывает адресу:
  HTTP 401/403/498 или HTML-стена на месте JSON. Ответ получает предупреждение
  `dom_fallback` в `meta.warnings`; 429, сетевые ошибки, дрейф парсера и кривые
  запросы по-прежнему возвращают свою ошибку. Фолбэк один: один запрос к API и
  не больше одной попытки через DOM; если DOM тоже упал, ошибка DOM сохраняет
  свой тип и называет предшествующий отказ API. Включён по умолчанию,
  `WB_DOM_FALLBACK=0` возвращает прежнее поведение. `doctor` при включённом
  фолбэке говорит, что инструменты уже переключаются сами, и что
  `WB_TRANSPORT=dom` заставляет doctor проверять и отрисованный путь.
- `marketplace-mcp install <client> --write` пишет конфиг клиента сам. По умолчанию
  в блок входит одна запись `marketplace` — объединённый сервер; `--per-source`
  оставляет старый блок по источникам. Путь к `uv` и к консольной команде
  абсолютный, если он найден: у десктопного клиента часто нет PATH терминала, и
  голое `uv` — это сервер, который молча не стартует. Запись сливается в
  `mcpServers`, чужие серверы и остальные настройки файла не теряются, старый
  файл перед изменением копируется в `<файл>.bak-<метка>`, новая версия пишется
  временным файлом и подменяется через `os.replace`. Кривой JSON и запись с тем
  же именем, но другим содержимым — отказ с кодом 2 (конфликт снимается
  `--force`); такая же запись — «already configured», без записи и без копии.
  `--dry-run` печатает записи, которые добавит, только имена остальных серверов
  и настроек (их содержимое, например ключи в `env`, не выводится) и путь, и
  ничего не пишет. Конфиг с BOM читается; файл не в UTF-8 (например, UTF-16) —
  отказ с кодом 2 и подсказкой сохранить его как UTF-8. Вторая запись в ту же
  секунду получает копию `-1`, `-2`…, так что прежняя копия не затирается.
  Конфиг-симлинк остаётся симлинком: пишется файл, на который он указывает.
  `install claude-code --write` печатает готовую строку `claude mcp add`, без
  `--write` — сначала её, потом блок для `.mcp.json` проекта под поясняющим
  комментарием. `install dsh --write` отказывается: у dsh свой формат, не
  `mcpServers`. Ctrl-C на вопросах `setup` и `unblock` — «cancelled» и код 130.
- `marketplace-mcp --help` (и `help`) печатает короткий экран usage: команды, по
  примеру на каждую, и ссылку на `docs/QUICKSTART.md`. Неизвестная команда
  печатает объяснение и этот же usage, и завершается с кодом 2.
- `marketplace-mcp doctor` печатает под каждой нездоровой строкой один следующий
  шаг: поднять scraping Chrome, пройти проверку сайта или войти руками и
  перепроверить источник, переключить Wildberries на `WB_TRANSPORT=dom`, задать
  `MPSTATS_MP_AUTH`, разобрать дрейф парсера, подождать при отказе по частоте
  запросов или открыть `docs/ANTI_BOT.md`. Тот же шаг попадает в `--status-file`
  отдельным полем `next_step`. Шаг выбирается из кодов причин, которые реально
  шлют коннекторы (`blocked`, `login_wall`, `not_authenticated`,
  `transport_down`, `rate_limited`, `auth_missing`), и всегда один: без советов
  про решатели капч, stealth-инструменты, пулы прокси и повторные попытки. Если
  упал сам selfcheck (`error`), это не стена сайта: шаг предлагает открыть issue с
  его выводом.
- `marketplace-mcp browser status` и `marketplace-mcp browser show`: окно
  scraping-Chrome на Windows стартует за экраном, поэтому человек не может ни
  войти руками, ни пройти проверку сайта. `status` отвечает, достижим ли
  CDP-эндпоинт, какая версия Chrome там отвечает, сколько открыто page-таргетов
  и в каком состоянии окно (видно, за экраном, свёрнуто); при недостижимом
  эндпоинте — код возврата 1 и подсказка, как запустить scraping Chrome.
  `show` сворачивает и снова разворачивает окно (только так Windows поднимает
  окно, которое лежит позади других), переносит его на экран
  (80, 40; текущий размер сохраняется, если окно не меньше 800x600, иначе
  1280x900) и активирует первую вкладку. Обе команды отправляют только
  браузерные CDP-команды и не ходят ни в один сайт. Совет doctor'а для
  источника, закрытого браузером, теперь называет `marketplace-mcp unblock
  <source>`.
- `marketplace-mcp browser start [--hidden]` запускает scraping-Chrome сам:
  по умолчанию окно видно (без off-screen аргументов и без скрытия), `--hidden`
  сохраняет прежнее поведение автозапуска коннектора. Если CDP уже отвечает,
  команда ничего не запускает и подсказывает `browser show`; на удалённом
  `CHROME_CDP_HOST` отказывает, а если порт не поднялся — называет профиль,
  который может держать другой Chrome без remote debugging.
- `marketplace-mcp unblock [sources...] [--wait SECONDS] [--dry-run]`: для
  каждого браузерного источника из `doctor` открывает в новой вкладке страницу,
  где человек проходит проверку или входит (карта `UNBLOCK_PAGES`), поднимает
  окно на этой вкладке и ждёт Enter в терминале или `--wait` секунд (по
  умолчанию 180), затем перепроверяет только эти источники и печатает те же
  строки и следующие шаги, что `doctor`. `--dry-run` только печатает страницы.
  Ничего не решается и не обходится автоматически: страницу проходит человек.
- `scripts/start_chrome_cdp.ps1` и `.sh` больше не требуют закрыть обычный
  Chrome: если CDP-эндпоинт уже отвечает на порту, лаунчер сообщает об этом и
  выходит с кодом 0; если тот же профиль (`--user-data-dir`) уже занят другим
  Chrome — отказ с кодом 2; иначе Chrome запускается, даже когда другие окна
  Chrome открыты. Ручной запуск теперь видимый по умолчанию; `-Hidden` в
  PowerShell возвращает окно за экран, `--hidden` в shell на macOS прячет
  приложение (⌘H), на Linux ничего не меняет, `-Visible` по-прежнему
  принимается и ничего не меняет. Скрытый запуск заканчивает подсказкой
  `marketplace-mcp browser show`; автозапуск коннектора остаётся за экраном.
- `scripts/check_lock_revision.py` — новый гейт: лок-файл, перезаписанный старым
  uv (revision 3 вместо 5), больше не проходит молча. Это не
  `required-version` в `pyproject.toml`: тот флаг запретил бы оператору со
  старым uv на PATH запустить сервер через запись `uv run --directory ...`,
  которую пишет `install --write`. Правильный путь — перелочить uv не старше
  0.13.6 (`uvx --from uv==0.13.6 uv lock`).
- `marketplace-mcp install --profile compare|decision|full` выбирает, какой сервер
  запускает одна запись `marketplace`: `compare-mcp` (профиль по умолчанию для
  новых установок, 4 инструмента сравнения, около 2,2 тыс. токенов на запрос),
  `decision-mcp` (5 инструментов) или `marketplace-mcp` (все инструменты
  источников, около 17,5 тыс. токенов). Имя записи не меняется. `--per-source`
  по-прежнему пишет блок по источникам и с `--profile` не сочетается. Если в
  конфиге уже есть другая запись, отказ без `--force` теперь называет и текущий
  профиль, и запрошенный; вывод команды называет профиль и подсказывает
  `--profile full`. Без `--profile` повторный `install --write` и `setup`
  сохраняют профиль, который уже записан в конфиге: `compare` по умолчанию
  только для новых установок.
- `marketplace-mcp setup` ведёт от нуля до первого ответа теми же командами:
  находит Claude Desktop, Cursor и Claude Code (или берёт `--client`), пишет
  конфиг с профилем `compare` по умолчанию, запускает scraping-Chrome, прогоняет
  проверки `doctor` с их таблицей и предлагает `unblock` для закрытых браузером
  источников. `--dry-run` печатает шаги и файлы и ничего не пишет, не запускает
  и не запрашивает; `--no-browser` и `--no-doctor` пропускают эти шаги. Код 0 —
  конфиг записан хотя бы одному клиенту, 1 — не нашлось ни клиента, ни записи,
  2 — неверные аргументы.
- `scripts/bump_version.py` поднимает версию во всех 85 объявлениях из общей
  таблицы `scripts/version_sites.py`, которую использует и `check_versions.py`;
  `--dry-run` только печатает план. `.gitattributes` держит LF в checkout,
  workflow `Image check` собирает оба образа и прогоняет stdio-образ через
  MCP до тега, а живые запросы ограничены бюджетом 40/8/3 с через
  `scripts/live_budget.py` и `scripts/live_check.py`.
- `docs/TOOLS.md` теперь генерируется (`scripts/gen_tools_doc.py`) из тех же
  схем `tools/list`, что видит клиент: по каждому профилю (compare, decision,
  full) — консольная команда, число инструментов, размер JSON в байтах и
  примерное число токенов (та же оценка, что в `scripts/mcp_wire.py`), а по
  каждому источнику — имя, параметры из JSON-схемы и первый абзац описания.
  README ссылается на файл, а ручные цифры стоимости в README и в таблице
  режимов `dsh/README.md` заменены блоками, которые генератор переписывает из
  того же замера. `--check` в CI (`lint`) падает, если файл или блок устарели.
  Прежние цифры были устаревшими: compare стоит около 2,2 тыс. токенов на
  запрос, а не 0,9 тыс., полный набор — около 17,5 тыс., а не 13,6 тыс.

- Prepared one additional `ru-marketplace-mcp` PyPI bundle. The builder derives
  metadata from workspace manifests, rejects dependency/script conflicts, and
  builds a wheel/sdist with every import package and `py.typed`; the README is the package description, not a file in site-packages. The
  `ru-marketplace-mcp` alias enters the CLI; the 16-member workspace, subset
  artifacts and Docker builds remain. Shared curl-cffi/Playwright minimums are
  aligned with the higher requirements already present; locked runtime versions
  stay unchanged. A new CI job checks a clean pip-venv install. Trusted publishing
  stays off until the owner registers one pending publisher and enables the
  repository switch; DEPLOYMENT.md contains the owner steps.
- `install` and `setup` running from uv's tool cache now persist an absolute uv
  command and `tool run --from ru-marketplace-mcp==<version>`, never the temporary
  console-script path. The note explains the release pin and refresh through
  `install --write --force`; checkout and ordinary-venv entries are unchanged.

- In `api` mode, `wb_card` and `wb_search` now re-read the same request through the
  rendered page in the scraping Chrome when WB's API refuses the address: HTTP
  401/403/498 or an HTML wall where JSON belongs. The response carries a
  `dom_fallback` warning in `meta.warnings`; 429, network errors, parser drift and
  bad requests keep their own errors. There is one API attempt and at most one DOM
  attempt; if the DOM path fails too, its error type is preserved and its message
  names the API refusal. On by default; `WB_DOM_FALLBACK=0` restores the previous
  behaviour. With the fallback on, `doctor` says the tools switch by themselves and
  that `WB_TRANSPORT=dom` makes doctor check the rendered path too.
- `marketplace-mcp install <client> --write` writes the client config itself. The
  default block is one `marketplace` entry running the unified server; `--per-source`
  keeps the old per-source block. `uv` and the console script are resolved to
  absolute paths when found: a desktop client often has no terminal PATH, and a
  bare `uv` is a server that silently never starts. The entry is merged into
  `mcpServers` without dropping other servers or settings, the old file is copied
  to `<file>.bak-<timestamp>` before any change, and the new version is written
  through a temporary file and `os.replace`. Malformed JSON and a same-name entry
  with different content are refused with exit 2 (the conflict needs `--force`);
  an identical entry prints "already configured" and writes nothing. `--dry-run`
  prints the entries it would add, only the names of the other servers and
  settings (never their content, such as keys in `env`) and the target path,
  and writes nothing. A config saved with a BOM is read; a file that is not UTF-8
  (UTF-16, for example) is refused with exit 2 and a save-as-UTF-8 hint. A second
  write in the same second gets a `-1`, `-2`... backup, so an earlier backup is
  never overwritten. A symlinked config stays a symlink: its target is written.
  `install claude-code --write` prints the exact `claude mcp add` line; without
  `--write` it prints that line first, then the block for a project's
  `.mcp.json` under a comment that says so. `install dsh --write` is refused:
  dsh has its own format, not `mcpServers`. Ctrl-C at a `setup` or `unblock`
  prompt prints "cancelled" and exits 130.
- `marketplace-mcp --help` (and `help`) prints a short usage screen: the verbs,
  one example each, and a pointer to `docs/QUICKSTART.md`. An unknown command
  prints the explanation and that screen, and exits 2.
- `marketplace-mcp doctor` prints one next step under every source that is not ok:
  start the scraping Chrome, pass the site's check or log in by hand and recheck
  the source, switch Wildberries to `WB_TRANSPORT=dom`, set `MPSTATS_MP_AUTH`,
  diagnose a parser drift, wait out a rate limit, or open `docs/ANTI_BOT.md`. The
  same step lands in `--status-file` as a `next_step` field. The step is chosen
  from the reason codes the connectors really send (`blocked`, `login_wall`,
  `not_authenticated`, `transport_down`, `rate_limited`, `auth_missing`), and it is
  always one instruction — never a captcha solver, stealth tooling, a proxy pool or
  a retry loop. A selfcheck that failed itself (`error`) is not a site wall: its
  step asks for an issue with the output above.
- `marketplace-mcp browser status` and `marketplace-mcp browser show`: the
  scraping Chrome's window starts off-screen on Windows, so a human can neither
  log in nor pass a site's check. `status` says whether the CDP endpoint is
  reachable, which Chrome answers, how many page targets it holds and whether
  the window is visible, off-screen or minimized; when the endpoint does not
  answer it exits 1 and prints how to start the scraping Chrome. `show`
  minimizes and restores the window (only a restore makes Windows raise a
  window that sits behind others), moves it on-screen (80, 40;
  the current size is kept when the window is at least 800x600, otherwise
  1280x900) and activates the first tab. Both send browser-level CDP commands
  only and request nothing from any website. The doctor's advice for a source
  blocked by the browser now names `marketplace-mcp unblock <source>`.
- `marketplace-mcp browser start [--hidden]` launches the scraping Chrome
  itself: the window is visible by default (no off-screen arguments, nothing
  hidden), while `--hidden` keeps the connector autostart's off-screen
  behaviour. If CDP already answers it starts nothing and points at
  `browser show`; a remote `CHROME_CDP_HOST` is refused, and a port that never
  binds names the profile another Chrome may hold without remote debugging.
- `marketplace-mcp unblock [sources...] [--wait SECONDS] [--dry-run]`: for each
  browser-backed source from `doctor`, opens the page where a human passes the
  check or logs in (the `UNBLOCK_PAGES` map) in a new tab, raises the window on
  that tab and waits for Enter on a terminal or `--wait` seconds (180 by
  default), then re-checks only those sources and prints doctor's rows and next
  steps. `--dry-run` only prints the pages. Nothing is solved or bypassed
  automatically: the human passes the page.
- `scripts/start_chrome_cdp.ps1` and `.sh` no longer demand closing the
  everyday Chrome: when the CDP endpoint already answers on the port, the
  launcher says so and exits 0; when another Chrome holds the same profile
  (`--user-data-dir`), it refuses with exit 2; otherwise Chrome starts even
  while other Chrome windows are open. The manual launch is now visible by
  default; `-Hidden` in PowerShell parks the window off-screen, `--hidden` in the
  shell hides the app (⌘H) on macOS and changes nothing on Linux, and `-Visible` is still accepted and changes nothing. A hidden
  launch ends with a pointer to `marketplace-mcp browser show`; the connector's
  autostart stays off-screen.
- `scripts/check_lock_revision.py` — a new gate: a lockfile rewritten by an
  older uv (revision 3 instead of 5) no longer passes silently. This is not a
  `required-version` floor in `pyproject.toml`: that flag would stop an
  operator whose PATH carries an older uv from starting the server through the
  `uv run --directory ...` entry that `install --write` writes. The fix is to
  relock with uv 0.13.6 or newer (`uvx --from uv==0.13.6 uv lock`).
- `marketplace-mcp install --profile compare|decision|full` picks which server the
  one `marketplace` entry runs: `compare-mcp` (the default for new installs: 4
  price-comparison tools, about 2.2k tokens per request), `decision-mcp` (5 tools)
  or `marketplace-mcp` (every per-source tool, about 17.5k tokens). The entry name
  does not change. `--per-source` still writes the per-source block and cannot be
  combined with `--profile`. Without `--force`, a conflicting existing entry now
  names both the configured and the asked profile; the command output names the
  profile and points at `--profile full`. Without `--profile`, a repeated
  `install --write` and `setup` keep the profile the config already runs: compare
  is the default for new installs only.
- `marketplace-mcp setup` walks from zero to the first answer through the existing
  verbs: it detects Claude Desktop, Cursor and Claude Code (or takes `--client`),
  writes the config with the compare profile by default, starts the scraping
  Chrome, runs doctor's checks and table, and offers `unblock` for a source the
  site walls off. `--dry-run` prints the steps and files and writes, starts and
  requests nothing; `--no-browser` and `--no-doctor` skip those steps. Exit 0
  when at least one client's config was written, 1 when no client and no config
  were found, 2 on bad arguments.
- `scripts/bump_version.py` bumps all 85 declarations from the one site table in
  `scripts/version_sites.py` that `check_versions.py` also uses; `--dry-run`
  only prints the plan. `.gitattributes` keeps checkouts on LF, the `Image
  check` workflow builds both images and drives the stdio image through MCP
  before the tag, and live requests are capped at 40 total / 8 per marketplace
  / 3 s between requests by `scripts/live_budget.py` and `scripts/live_check.py`.
- `docs/TOOLS.md` is now generated by `scripts/gen_tools_doc.py` from the same
  `tools/list` schemas a client pays for: per profile (compare, decision, full)
  it lists the console script, tool count, JSON payload bytes and an approximate
  token count (the same estimate as `scripts/mcp_wire.py`), and per source it
  lists every tool's name, JSON-schema parameters and first description
  paragraph. The README links to it, and the hand-typed cost figures in the
  README and in the `dsh/README.md` mode table are replaced by blocks the
  generator rewrites from the same measurement. A CI `--check` in the lint job
  fails when any of them is stale. The old figures were out of date: compare
  costs about 2.2k tokens per request, not 0.9k, and the full set about 17.5k,
  not 13.6k.

### Fixed

- Фолбэк Wildberries на страницу: если после отказа API упал сам браузерный
  драйвер (Playwright бросает свою ошибку, а не ошибку инструмента), ответ
  теперь тоже говорит, что сначала API отказал адресу. Раньше такая ошибка
  уходила общим `transport_down` без этой причины, и было непонятно, при чём
  тут Chrome. Найдено живым прогоном 30 октября.
- Поиск Wildberries по странице (`WB_TRANSPORT=dom` и автоматический фолбэк):
  одна плитка с несколькими ценами без зачёркнутой больше не роняет весь поиск.
  Раньше её `in_stock` оставался пустым, ответ не проходил проверку модели, а
  через фолбэк это выглядело как `transport_down`. Теперь такая плитка приходит
  с `price_rub: null` и `in_stock: false`, а предупреждение называет её и
  советует `wb_card`. Ошибка была с v2.5.3; найдена аудитом перед релизом.
- Taobao: страница со ссылками на вход или регистрацию и без товаров больше не
  считается сеткой, которая ещё грузится, даже если на ней написано «加载中» и
  ссылок больше обычного. Раньше такая страница ждала 10 секунд и отвечала
  повторяемым `timeout`; теперь сразу `parser_drift`, как до v2.6.0, если не
  сработал признак стены входа. Найдено аудитом перед релизом.
- `wb_selfcheck` учитывает `WB_TRANSPORT=dom`: проверки `card` и `search_v9`
  читают отрисованные страницы теми же парсерами, что инструменты, вместо
  неиспользуемых API-хостов. Капча остаётся `inconclusive` без решения и повторов;
  ошибки транспорта и дрейф парсера различаются по коду. Остальные пробы сохранены.
- `compare_prices` считает цены Taobao в `source_outcomes[].detail` по
  `price_native`, а не по пустому `price_rub`: например, `20 results, 20 priced
  in CNY (not ranked)`. Рублёвые строки и ранжирование не изменились.
- Чек-лист релиза называл порог покрытия 70 %, а гейт требует 80 %
  (`--cov-fail-under=80`). В тексте теперь стоит то значение, которое
  проверяется.
- Запуск Chrome из PowerShell (`start_chrome_cdp.ps1`): путь к профилю с
  пробелом больше не разрывается на два аргумента. Раньше `-AllowMainProfile`
  (папка `User Data`) и любое имя пользователя Windows с пробелом запускали
  Chrome с другой папкой профиля, без входа на площадки. Если CDP не ответил за
  ~12 секунд, скрипт выходит с кодом 1, а не 0. Проверка «профиль уже занят»
  больше не путает профиль с соседним `Chrome-Scraping-2`.
- Запуск Chrome из shell (`start_chrome_cdp.sh`): «Chrome уже запущен» скрипт
  пишет, только когда порт отвечает как CDP. Чужой процесс на порту — отказ с
  кодом 2 и подсказкой `--port`; раньше скрипт молча выходил с кодом 0.
- Скрытие окна scraping-Chrome на Windows находит и процессы Edge и не трогает
  соседний профиль с похожим именем.
- `CHROME_CDP_HOST` с IPv6-адресом (`::1` или `[::1]`) работает: в URL адрес
  берётся в скобки и считается локальным.
- Предупреждение фолбэка Wildberries говорит, что цены и доставка взяты в
  регионе, выбранном в браузере, а не по `dest`. Раньше при регионе по
  умолчанию об этом не говорилось.
- `docs/DEPLOYMENT.md`: команды установки из wheelhouse оставались на версии
  прошлого релиза, потому что `bump_version.py` и `check_versions.py` их не
  видели. Теперь это три места в таблице версий: 88 вместо 85.
- Бюджет живых запросов мейнтейнера теперь считает страницу товара AliExpress,
  открытую через `window.open`, и вкладки `unblock`. Журнал защищён файловой
  блокировкой, поэтому два параллельных прогона больше не теряют записи. Чек-лист
  релиза запускает doctor как `python scripts/live_check.py doctor`, под тем же
  бюджетом.
- Бюджет живых запросов говорит, когда он сам не пустил запрос: строка
  `[refused]` в stderr и итог `refused before sending` после команды. Раньше
  отказ выглядел в doctor как `transport_down`, будто площадка заблокировала
  адрес. Чек-лист релиза объясняет, как проверить отзывы Ozon, которым не
  хватает лимита в 8 запросов.

- Wildberries' fallback to the rendered page: when the browser driver itself
  fails after an API refusal (Playwright raises its own error, not a tool
  error), the answer now also says the API refused this address first. Before,
  such a failure surfaced as a bare `transport_down` that never explained why
  Chrome was involved. Found in the 30 October live run.
- Wildberries search over the rendered page (`WB_TRANSPORT=dom` and the
  automatic fallback): one tile showing several prices without a struck one no
  longer fails the whole search. Its `in_stock` stayed empty, the answer failed
  model validation, and through the fallback that looked like `transport_down`.
  Such a tile now comes back with `price_rub: null` and `in_stock: false`, and
  the warning names it and points at `wb_card`. The bug dates from v2.5.3; the
  pre-release audit found it.
- Taobao: a page with login or registration links and no products is no longer
  taken for a grid that is still loading, even when it says «加载中» and has
  more links than usual. It used to wait 10 seconds and answer a retryable
  `timeout`; it now answers `parser_drift` at once, as before v2.6.0, unless a
  login-wall marker fires. Found by the pre-release audit.
- `wb_selfcheck` respects `WB_TRANSPORT=dom`: `card` and `search_v9` check
  rendered pages with the tools' parsers rather than unused API hosts. Captchas
  remain `inconclusive` without solving or retries; transport failures and parser
  drift are distinguished by error code. The other probes are preserved.
- `compare_prices` counts Taobao's native prices in `source_outcomes[].detail`
  instead of reporting zero rouble prices: for example, `20 results, 20 priced
  in CNY (not ranked)`. Rouble-source detail and ranking are unchanged.
- The release checklist named the coverage floor as 70 % while the gate enforces
  80 % (`--cov-fail-under=80`). The text now states the value that is checked.
- Starting Chrome from PowerShell (`start_chrome_cdp.ps1`): a profile path
  with a space is no longer split into two arguments. `-AllowMainProfile` (the
  `User Data` folder) and any Windows user name with a space used to start
  Chrome on a different profile folder, without the marketplace logins. When
  CDP does not answer within about 12 seconds the script now exits 1, not 0.
  The "profile already in use" check no longer mistakes a sibling such as
  `Chrome-Scraping-2` for the profile.
- Starting Chrome from the shell (`start_chrome_cdp.sh`): the script says
  "already running" only when the port answers as CDP. Another process on the
  port is refused with exit 2 and a `--port` hint; it used to exit 0 silently.
- Hiding the scraping Chrome window on Windows also finds Edge processes and
  leaves a sibling profile with a similar name alone.
- An IPv6 `CHROME_CDP_HOST` (`::1` or `[::1]`) works: the address is bracketed
  in URLs and counts as loopback.
- The Wildberries fallback warning says prices and delivery follow the region
  set in the browser, not `dest`. With the default region it used to say
  nothing about it.
- `docs/DEPLOYMENT.md`: the wheelhouse install commands kept the previous
  release, because `bump_version.py` and `check_versions.py` never saw them.
  They are now three sites in the version table: 88 instead of 85.
- The maintainer's live request budget now counts the AliExpress item page
  opened through `window.open` and the `unblock` tabs. A lock file guards the
  ledger, so two parallel runs no longer lose bookings. The release checklist
  runs doctor as `python scripts/live_check.py doctor`, under the same budget.
- The live request budget says when it refused a request itself: a `[refused]`
  line on stderr and a closing `refused before sending` summary. The refusal
  used to show in doctor as `transport_down`, as if the marketplace had blocked
  the address. The release checklist explains how to check Ozon reviews, which
  do not fit the cap of 8 requests.

### Security

- Публикующие workflow по тегу: `mcp-publisher` сверяется с SHA-256 до
  распаковки, checkout не оставляет токен на диске, сборка релиза не берёт кэш
  uv, и один тег публикуется одним запуском за раз (`concurrency`). Найдено
  аудитом перед релизом.

- Tag-triggered publishing workflows: `mcp-publisher` is checked against its
  SHA-256 before it is unpacked, checkout leaves no token on disk, the release
  build restores no uv cache, and one tag publishes in one run at a time
  (`concurrency`). Found by the pre-release audit.

## [2.6.0] - 2026-10-30

FastMCP 4 без смены публичного контракта инструментов; три исправления Taobao из замечаний к v2.5.4; Dependabot следит и за npm lock-файлом.

FastMCP 4 with the public tool contract unchanged; the three Taobao issues recorded in v2.5.4 are addressed; Dependabot also watches the npm lockfile.

### Changed

- Все пакеты перешли с FastMCP 3.x на 4.1.4 (MCP Python SDK v2), минимальная
  версия pydantic — 2.12 (#138). Поддерживаются прежнее рукопожатие
  (`initialize`, протокол 2025-11-25 и старше) и новый протокол 2026-07-28
  без сессий (`server/discover`). Имена инструментов, параметры и поля ответов
  не изменились: `_meta` остаётся `_meta`, вложенный блок карточки в
  `compare_verify_offer` — `meta`.
- Сохранённая вкладка проверки в протоколе без сессий привязана к процессу
  stdio-сервера: повтор от его единственного клиента продолжает ту же вкладку.
  По HTTP без сессии вкладка не сохраняется, потому что запросы нельзя связать
  с одним клиентом. У клиентов с рукопожатием сохраняется прежняя привязка к
  MCP-сессии (#138).
- `compare_browser_snapshot` читает объявленные клиентом возможности в обоих
  протоколах, в том числе сигнал поддержки изображений (#138).
- `scripts/e2e_stdio_check.py` проверяет оба протокола: ожидается 32 stdio-сессии
  вместо 16. Добавлены офлайн-тесты настоящих stdio- и локальных HTTP-серверов
  для привязки клиента и проверки bearer-токена (#138).
- Dependabot уже следит за закреплёнными по digest базовыми образами Docker
  (#134, включено в v2.5.4); теперь он проверяет и npm lock-файл для офлайн-тестов
  DOM-экстракторов, еженедельно, с группировкой minor- и patch-обновлений (#140).

- FastMCP 4 (4.1.4, MCP Python SDK v2) replaces the 3.x line in every
  package; pydantic's floor rises to 2.12 with it. The servers now speak both
  protocol eras: the handshake era (`initialize`, 2025-11-25 and older) and the
  sessionless 2026-07-28 era (`server/discover`). The tool contract is
  unchanged: the 20 card/search tools that return a `_meta` block still
  advertise and return `_meta` (FastMCP 4 would have renamed it to `meta`), and
  `compare_verify_offer` still nests the card's block as `meta`.
- Browser handoff on the sessionless era: such a connection has no session, and
  FastMCP gives every request a new `session_id`, so a retry could never find
  its retained tab, which then stayed open until it expired. A stdio server now
  keeps the tab for its one client (the process) instead; over HTTP nothing
  names the caller between requests, so the challenge is reported without
  retaining the tab. Handshake-era clients keep the per-session behaviour.
- `compare_browser_snapshot` reads the client's declared capabilities on both
  eras (the vision hint was lost on 2026-07-28 connections, which send no
  `initialize` params).
- The stdio smoke test (`scripts/e2e_stdio_check.py`) probes every server in
  both eras: 32 sessions instead of 16. New offline tests start real stdio and
  loopback HTTP servers to pin the client scope and the bearer-token check in
  both eras.
- Dependabot already watches the digest-pinned Docker base images (#134,
  shipped in v2.5.4); it now also watches the dev-only npm lockfile for offline
  DOM-extractor tests, weekly, with minor and patch updates grouped (#140).

### Fixed

- Taobao кодирует пробел в поисковом запросе как `%20`, а не `+`: площадка
  больше не ищет буквальное «iphone+15». Настоящий плюс остаётся `%2B`;
  канонический URL один для поиска, кэша и сохранённой вкладки (#141).
- Поиск Taobao возвращает обычные товары Tmall (`detail.tmall.com`) с их
  настоящими URL, id, названием, ценой в юанях и магазином. Рекламные трекеры
  `click.simba.taobao.com` намеренно исключены: переход оплачивает рекламодатель
  и это не URL товара (#141).
- `taobao_card` явно отказывает для Tmall: невосстановимый `bad_request`
  объясняет, что карточки Tmall не поддерживаются, а основные данные уже есть
  в поисковой выдаче. Политика разрешённых хостов не расширена (#141).
- Сетка Taobao с маркером «加载中» перечитывается в той же вкладке около 10 с.
  Если загрузка не закончилась, возвращается повторяемый `timeout`, в том числе
  в `compare_prices`, а не `parser_drift`. Вход и капча по-прежнему сразу дают
  `challenge_required`; пустая сетка без признака загрузки — `parser_drift`
  (#141).

- Taobao search: a space in the query is sent as `%20`, not `+`. Taobao read
  `q=iphone+15` as the literal text "iphone+15", while the page's own search
  box held "iphone 15" for `q=iphone%2015`. The URL is built in one place and
  stays the cache and browser-handoff key; a literal plus in the query is still
  escaped as `%2B`.
- Taobao search: Tmall listings are no longer skipped. A `detail.tmall.com`
  tile is an ordinary product — often the official brand store — and leaving it
  out skewed price comparisons, so search now returns Tmall items with their
  real host, id, title, price and shop. The `click.simba.taobao.com` ad-click
  trackers stay out on purpose: visiting one bills the advertiser, and it is
  not a product URL.
- Taobao card: a Tmall listing is refused with a clear, non-retryable
  `bad_request` instead of a raw navigation-policy error. Taobao redirects
  Tmall items to `detail.tmall.com`, which the card's host policy refuses;
  the tool now says that Tmall listings are not readable by `taobao_card` and
  that the search result already carries the title, price, shop and URL. The
  host policy itself is unchanged.
- Taobao search: a grid that is still rendering («加载中») is no longer
  reported as `parser_drift`. The extractor transports the loading marker as a
  structural flag, the browser read re-checks the same page for about 10
  seconds, and a grid that never finishes is a retryable `timeout` — in
  `compare_prices` too, where the source now reads as `timeout` instead of
  `parser_drift`. A login wall or a CAPTCHA never waits and still answers
  `challenge_required`; a genuinely empty page is still `parser_drift`.

## [2.5.4] - 2026-10-30

### Fixed

- Browser navigation: a 403 main document is no longer a block on sight. Ozon
  answers the first request with 403 and a JS check that reloads the page by
  itself, so `ozon_search` failed several times a week without any captcha.
  Now the page gets up to 10 s for that reload; only a 403 that survives it
  raises `NavBlocked`. The Playwright path waits for the new document to land,
  so the host check sees its URL and a redirect resolves to its final status.
  The raw-CDP path counts only main-frame documents: a 200 iframe on a block
  page no longer turns the block into data. Thanks @dont99code (#133).
- Browser handoff: the idle bound no longer switches off when the event-loop
  clock is young (under ~60 s since boot). `last_used` used `0.0` as "never
  used", but loop time counts from boot, so a real timestamp can be zero or
  negative; the marker is now `None`. Caught by the nightly CI on a fresh VM.

### Security

- Locked dependencies moved past every published advisory: pip-audit on the
  exported lock went from 64 known vulnerabilities in 10 packages (Authlib,
  certifi, cryptography, dnspython, FastMCP, filelock, mcp, opentelemetry-api,
  PyJWT, virtualenv) to none, and npm audit on the dev-only lock from 2 high
  to none. FastMCP stays on the 3.x line (3.4.11). Workflow actions are pinned
  to commit SHAs and the Docker base images to digests. Thanks @ilodezis (#132).
- Dependabot now also watches the Docker base images, so the digest pins pick
  up rebuilt images; the builder's uv moved to 0.13.6 and CI's setup-uv to
  v10 (#134, #135, #136).

## [2.5.3] - 2026-10-13

### Fixed

- `wb_search` with `WB_TRANSPORT=dom`: a narrow tile drops the «с WB Кошельком»
  caption but keeps the Wallet icon beside the price. Such tiles were read as
  the regular price; on 13 October that was 43 tiles of 214 (for nm 471317158
  the tile's 4 341 ₽ is the Wallet price, the regular price is 4 430 ₽). The
  icon now marks the Wallet price.
- WB product pages strike the old price with CSS, not `<del>`, so the struck
  hint was empty and the original price was guessed as the largest figure.
  The extractor now reads CSS line-through; without strike evidence, several
  figures are no longer guessed — `price_rub`/`price_original_rub` stay null
  with a warning (a Wallet caption still marks the lowest figure).

## [2.5.2] - 2026-10-13

### Fixed

- DNS and Citilink: a Qrator page served with HTTP 200 was cached before any
  check, and a card took the page's own title («Доступ ограничен») as the
  product name and answered success. The wall check existed but was never
  called. Walls are now recognised from positive evidence (document title or
  Qrator markup, never an empty title alone) before mapping and caching, answer
  `challenge_required`, and selfcheck reports them as blocked, not drift.
- Taobao cards check for a challenge before mapping, whatever the title or
  price; text inside hidden widgets cannot fake one.
- `wb_search`: when v9 failed and the legacy fallback failed too (429, HTTP
  error, bad JSON), the answer was `no_results`. It is now `rate_limited`,
  `parser_drift` or `transport_down`; `no_results` needs a positive empty
  answer, and an unknown total stays `null`. With `WB_TRANSPORT=dom` an empty
  search keeps the warning that Chrome's region was used instead of `dest`, a
  «-500 ₽» badge is no longer read as a price, and kopecks are kept.
- Avito search: an HTTP-200 error envelope raises `challenge_required`,
  `rate_limited` or `transport_down`; a body without an items list is
  `parser_drift`. Previously both were an empty successful search.
- `avito_card` reads shop listings that carry no schema.org Product from the
  page markers (title, a price with an explicit ₽, description); the page's
  item id must match. On 2026-10-13 one listing in six had no Product, and the
  canary reported drift whenever such a listing came first. `images` is `null`
  when the page does not say, not 0.
- `wb_search` no-results answers carry `meta: null` unless there are warnings.
- Ozon: a body without `widgetStates` is `parser_drift`, not an empty card;
  a later review page without it still returns the collected reviews as
  partial. Megamarket: a `success: false` envelope no longer becomes a card
  stamped with the requested id, and is never cached.
- Browser tier: an attach timeout reports how many tabs are open in the
  scraping Chrome instead of blaming the Chrome version; an idle-expired
  handoff frees its tab at the idle deadline; `aliexpress_card` matches the
  full item id, closes only the popup it opened and bounds every close;
  a cancellation during tab hand-over closes the tab. When the raw-CDP
  fallback fails after an attach timeout, the error keeps the tab count, and
  `doctor` advises closing unused tabs.
- `scripts/start_chrome_cdp.ps1` parses in Windows PowerShell 5.1 (it had
  non-ASCII dashes in a file without a BOM).

### Changed

- The CI coverage floor is 80 % (was 70 %); the offline suite measures 84.7 %.

## [2.5.1] - 2026-10-12

### Fixed

- `avito_card` reads the item page's schema.org Product over CDP because
  `/web/1/js/items` now ignores `itemId` and answered a catalog envelope that
  was returned as an empty successful card. Its seller block now supplies
  reputation and the profile link instead of leaving `seller` empty.
- `avito_seller` reads the rendered profile over CDP with the same pacing and
  allowed hosts as cards. On 2026-10-12 `/web/1/js/items` ignored `userId` too:
  different sellers received the same generic catalog with `totalCount: 1500`, falsely
  reported as their active listings. `/brands/<hash>`, `/user/<hash>/profile` and
  bare ids are accepted (32 hex characters select `brands`, other ids `user`);
  unavailable `active_items` stays `null`, not the generic catalog total.
- Avito's canary follows search → card → seller, taking the profile link from
  the card rather than relying on a seller id that search may not expose.
- Lamoda cards try HTTP GraphQL first (tier 1), then make one in-page GraphQL
  POST through the operator's Chrome over CDP (tier 2) on a non-200 response or
  `httpx.TransportError`. On 2026-10-12 GraphQL over HTTPS returned a 307 loop,
  so the HTTP-only path failed while the browser could read the card. Successful
  fallback reads report `tier_used: graphql_cdp`; HTTP 200 keeps the browser out
  of the request. A landing that stops on Lamoda's anti-bot page reports
  `challenge_required` at once instead of waiting out the fetch timeout.
- Yandex Market cards are read from `/card/{slug}/{id}`. Yandex retired
  `/product/{id}`, which now answers with an empty page frame, so every
  `yandex_card` and `compare_verify_offer` for Yandex came back as
  `empty_product_shell` while the product was live; built fallback links use the
  new path too (#105).

## [2.5.0] - 2026-10-12

### Added

- `cian_card` returns `photo_urls`: the full-size image URLs in Cian's order,
  with floor plans flagged `is_layout`. The count in `photos` stays; the URLs
  let a client download and inspect the photos, which the CDN serves without
  the browser session.
- Wildberries can read rendered pages instead of the API: `WB_TRANSPORT=dom`
  (default `api`) makes `wb_search` and `wb_card` open the search grid and the
  product page in the operator's Chrome over CDP and parse their text, for
  addresses where `card.wb.ru` / `search.wb.ru` and the site's own `/__internal/`
  API answer 403 to every client (#101). Search tiles report the WB Wallet price
  (`price_kind: wallet`, flagged in `meta.warnings`); cards report the regular,
  Wallet and struck prices separately, plus the store and its rating, warehouse,
  delivery date, return policy, cross-border flag with the seller's legal entity
  and country, warranty, the «Оригинал» badge, and «Все N предложений от X ₽»
  with a counterfeit-risk warning below 60 % of the card's price. «Подозрительная
  активность» and a visible captcha stop without retries. New settings:
  `WB_TRANSPORT`, `WB_DOM_WAIT_S`, `WB_DOM_MIN_GAP`, `WB_DOM_SETTLE_MS`,
  `WB_DOM_STORE_WAIT_S`, `WB_DOM_OFFERS_WAIT_S`, `WB_DOM_MAX_CARDS`. New
  `WbCardItem` fields are optional and empty on the API path. An empty first search
  page without WB's own «ничего не нашлось» notice is reported as `parser_drift`,
  not as no results.

### Fixed

- Wildberries search reports a refusal of `search.wb.ru` v9 or of the legacy path
  as `transport_down` instead of falling back to stale unpriced results or an
  empty answer (#101).
- Review health no longer reports content key drift for a small sample of
  rating-only reviews: the all-empty-text alarm needs at least 10 returned
  reviews (#111).
- `WB_TRANSPORT=dom` reads the other-offers header for a single other seller.
  With one offer WB writes «1 предложение от 4 735 ₽» (singular, no «Все»), which
  the «Все N предложений» pattern missed: nm 1345073040 listed its one other offer
  (nm 1552408950) in `other_offers` while `other_offers_count` and
  `other_offers_from_rub` stayed empty and the block's text was not cut out of
  the card's own lines. Both forms are recognised now, and the render wait for
  the block accepts the singular header too.
- `WB_TRANSPORT=dom` no longer glues another seller's offer onto a card whose
  own offer is unavailable. nm 164379765 (main seller Modern Device, no «Купить»
  button) came back with supplier UJII, 7 413 ₽ and `in_stock: false`: those
  belong to nm 1605653581, listed in the page's «Все N предложений» block, which
  the price and store fallbacks mistook for the buy block. Without a buy block
  the card now reports `in_stock: false` and empty price, Wallet and struck
  prices, supplier, supplier rating, delivery and warehouse, plus a warning
  `main offer unavailable; N other sellers from X ₽ (cheapest nm Y)`. The other
  sellers are kept in the new optional `other_offers` (up to five cheapest: `nm_id`
  from the tile's link, `supplier`, `price_rub`, `price_kind`, `delivery`; empty on
  the API path), and the 60 % counterfeit-risk comparison runs only when the card
  has its own offer.
- Citilink search sends the query as `text`, the parameter citilink.ru reads.
  With `?q=` the site renders its generic search landing, so every search parsed
  zero tiles and was reported as drift while the site itself answered normally;
  the self-check probe built the same URL (#104).
- Wildberries reads `card.wb.ru`, `search.wb.ru` and `catalog.wb.ru` through
  browser impersonation. Those three answer the default client's TLS handshake
  with a 403 HTML page on a network where curl and curl_cffi get a 200 from the
  identical URL, so `wb_card` and `wb_category_products` failed outright while
  `wb_search` fell through to the legacy stale-id path and returned unrelated
  products instead of an error. Hosts without the gate, including `feedbacks2.wb.ru`
  on the same apex, keep the shared budget and retry path.
- Comparison preserves native source warnings alongside valid offers, including
  fallback extraction, missing fields, coupon pricing, and result truncation.
  Successful source access no longer hides those data-quality limitations.
- The nightly live canary no longer shares a concurrency slot with pushes to
  `main`: the group carries the event name and a scheduled run is never
  cancelled, so a routine commit can no longer delete the only automatic live
  evidence, and the canary can no longer cancel a merge verification. The
  contract is pinned by `scripts/test_ci_concurrency.py`.
- Wildberries refusals now reach the request pacer, so the longer post-refusal
  gap applies and the "standing block, not a blip" hint can reach an operator;
  an HTML wall page served with HTTP 200 counts as a refusal instead of being
  cached as a success. `wb_selfcheck` probes `search.wb.ru` v9 directly, so a
  refusal of the primary search path is visible rather than hiding behind the
  legacy fallback's answers.
- The impersonated transport streams its body through the same cap as the shared
  path, and the gated-host note records the dated re-measurement: the 403s did
  not reproduce from a different address, while impersonation remained the
  difference on `catalog.wb.ru`.
- Transport failures name the exception class instead of surfacing an empty
  detail, and Detsky Mir reports an HTTP 418 from its edge as an address block
  rather than an unexplained status. A dated [live record](docs/LIVE_STATUS.md)
  states what was verified against the marketplaces, and the Detsky Mir note in
  `docs/ANTI_BOT.md` carries the dated correction.
- The gated Wildberries hosts prefer impersonation and fall back once to the
  shared client, because a second measurement showed the split is not a property
  of the host: from one address the impersonated path timed out on the primary
  search endpoint while the shared client answered it, and from the next
  `card.wb.ru` refused both. Canary probes read past the cache, so a `healthy`
  verdict cannot describe a read that already happened.
- Cian works from a Chrome profile that remembers a region. `www.cian.ru`
  redirects such a profile to a regional subdomain (`rostov.cian.ru`), which the
  exact-host navigation policy rejected, so every `cian_search` and `cian_card`
  failed with `transport_down`. A policy entry with a leading dot (`.cian.ru`)
  now admits subdomains of that host, and only the Cian connector uses one;
  `cian.ru` itself and look-alike hosts stay outside it.

## [2.4.2] - 2026-09-19

### Fixed

- Yandex search keeps distinct reported SKUs within a product family before
  applying its result limit, including zone payloads with no `wareId`.
- Comparison preserves unknown Wildberries quantities as unknown stock and
  interprets Ozon availability conservatively: negative labels cannot win an
  in-stock-only comparison, and pack sizes or ambiguous counts remain unknown.
- Doctor retains failure explanations supplied through a canary's `detail`
  or `notes` when no standard `reason` is present, including Yandex empty-card
  responses; healthy checks stay terse.

- Operator CLI: installation includes AliExpress and optional MPStats; doctor
  includes AliExpress, accepts documented source aliases, and rejects invalid
  arguments before querying sources. Unknown subcommands no longer start a
  waiting stdio server.
- Comparison and shortlist inspection share the native card dispatcher:
  Megamarket receives the correct argument, and Wildberries/Detsky Mir IDs and
  canonical URLs use the same validation in both profiles.
- Sources deliberately excluded by `MARKETPLACE_SOURCES` are distinguished from
  missing installations and do not make default comparisons falsely partial.
- Startup, wire-budget, and Docker probes enforce response deadlines, drain
  stderr, reject JSON-RPC errors, and clean up their processes. The local stdio
  gate now checks the running release version and complete source inventory.
- A failed pytest collection cannot satisfy the documented-test-count gate.
  Operational regression tests run in CI, Node tests use the lockfile, and the
  pre-commit Ruff version matches the workspace lock.

### Documentation

- Added a task-oriented [first-query guide](docs/QUICKSTART.md) covering client
  setup, targeted diagnostics, offer verification, and access limitations.
- Updated the release checklist to cover sixteen servers and 32 artifacts.
- Standalone installation uses GitHub Release wheels or a source checkout,
  rather than unavailable PyPI package names. The comparison `all` extra now
  includes AliExpress; instructions retain the explicit AliExpress install
  needed by the previously published v2.4.1 wheels.

## [2.4.1] - 2026-09-18

**Withdrawn: 2.4.0.** Its source distributions carried third-party personal data in
the test fixtures — two Yandex reviewer accounts (including one encoded inside a
base64 protobuf) and three named private individuals from Cian listings, with
account ids and a resolvable agent profile URL. The fixtures are scrubbed here, and
the 2.4.0 release and tag were withdrawn rather than left downloadable.

### Fixed

- Fixture privacy: the reviewer identity in `card_washer.html` is masked in all four
  carriers (`uidPublicId`, `avatarMediaId`, `analyticsData.uid`, and the
  `authorComplaintContext` protobuf that encoded the same UID); the Cian fixtures no
  longer name private realtors, while agency records stay as business data.
- Provenance prose no longer points at the operator's private capture workspace, and
  the fixture pins are recomputed.
- `ARCHITECTURE.md` now documents nine error codes, not eight - `challenge_required`
  was missing from a contract integrators branch on.
- `CDP_SETUP.md` lists Cian among the sources that need a browser, and the CDP-only
  set is six, not five.
- `DEEP_RESEARCH_V2.0.0.md` carries a superseded notice: the four security items it
  listed as open work all shipped since (`a7b3f86`, `ca846fe`).

## [2.4.0] - 2026-09-18 — WITHDRAWN (see 2.4.1)

### Reliability

- CDP navigations are bounded: at most three in flight process-wide and one per
  host, with a per-host breaker that pauses a host after repeated refusals instead
  of letting the rest of a fan-out queue behind it. The budget covers the
  navigation, not the page - a retained challenge page waits for a human and must
  not hold its host's slot.
- Refusal backoff is jittered, so sources refused at the same moment no longer
  retry in lockstep.
- The browser-handoff registry holds eight leases, bounds a lease by lifetime
  (900 s, never extended by a retry) and by idleness (600 s), and the handle issuer
  and consumer now agree on what "live" means.

### Reporting

- A resumed read says what happened: whether the retained page was resumed,
  whether the challenge cleared, and whether the data moved (tri-state - "nothing
  to compare with" is not "nothing changed"), compared by digest rather than by
  keeping the payload.
- Refusals explain themselves: busy names the reason and a retry hint, a full
  registry says so, and a caller's own expired handle says it expired while a
  foreign handle stays opaque.

### Correctness

- Yandex zone snippets are found with a quote-aware tag scanner; a raw `>` inside
  an attribute value no longer makes a snippet disappear from the parse.
- The routing-eval runner's verdict is machine-checkable: a partial run is not
  `ok`, missing answers count as failed, and the exit code follows the verdict.

### Privacy

- Fixtures no longer carry third-party contact phone numbers, a logged-in account
  nick, per-request identifiers, reviewer display names, account ids or order
  numbers; the operator's egress IP was replaced with documentation space.
- Every fixture pin is verified against its file by a CI gate.

### Tooling

- `scripts/check_provenance.py` checks all fixture pins and refuses to let a
  quarantined pin stay quarantined once it matches again.

## [2.3.0] - 2026-09-13

- `compare_browser_snapshot(handoff_id)` exposes the retained browser viewport as
  standard MCP image content for native-vision clients. The handle is bound to the
  MCP session and lease expiry; no OCR or external vision service is invoked.
- DSH profile selection is mutually exclusive (`full > decision > compare`), and
  the stored wire baselines cover all three profiles.
- Offer verification now selects the requested WB row, unwraps Detsky Mir cards,
  checks Ozon's regular price, and can require the Yandex search-row `sku_id`
  before comparing a card price. Seller article fields remain non-MPN evidence.
- WB preserves unambiguous typed color evidence. MPN/GTIN remain `unknown` when
  the source does not provide manufacturer identifiers.

### Browser recovery

- Optional `CHROME_CHALLENGE_HANDOFF_S` retains DOM-detected Lamoda search and
  Taobao search/card challenge tabs. Same-session repeats read the owned page
  without navigating again. Expiry is capped at 300 seconds from initial attach,
  does not extend, and at most four leases are active. Error/comparison metadata
  exposes `handoff_expires_at` and an opaque `handoff_id` only when retained.
- New `compare_browser_snapshot(handoff_id)` returns a bounded JPEG viewport and
  capture metadata through standard MCP image content. It never navigates or
  invokes an OCR/vision service; a capable MCP client can use its own native
  vision. Handles are same-session and expire with the retained lease.
- DSH mounts exactly one profile with precedence full, decision, compare. This
  fixes duplicate compare/decision mounts and the full+decision flag conflict.
- The snapshot tool costs 370 wire tokens in the stored baseline; the profiles it
  belongs to total compare 2023, decision 2278, unified 16549
  (`work/performance/wire-baseline.json`, enforced by the CI wire gate). Baselines and the public tool contract were
  updated for this addition; CI now gates the middle profile too.
- Owned tabs close on success, failure, expiry, caller cancellation and graceful
  shutdown. Active handoffs suppress profile hiding; owned-window foreground
  activation is best-effort. Default behavior, browser-less compare installations,
  and headless operation retain their existing behavior. Hard process-kill expiry
  and HTTP failures rejected before DOM extraction are outside this feature.
- Raw CDP now attempts bounded cleanup of its exact owned target even when
  target discovery or page-websocket attachment fails. Cancellation and cleanup
  errors preserve the original failure. Concurrent ownership tests now use six
  distinct targets and a foreign tab rather than a single shared fake target.
- Taobao search/card failures no longer enter the successful-result cache, and
  Lamoda search does not cache detected challenge pages. After browser action,
  a new call reads fresh evidence instead of replaying a cached block for up to
  the default 120-second TTL. Successful payloads still use the normal cache.
- Taobao search now uses its existing CAPTCHA detector in the public tool, not
  only in selfcheck. Empty cards with challenge evidence return the same
  structured error; ordinary product cards mentioning CAPTCHA remain data.
- Taobao login/CAPTCHA walls and Lamoda search challenges return
  `challenge_required`. Comparison outcomes now retain `error_code`, `retryable`,
  `requires_user_action`, and `challenge_type`, even when error detail is truncated.
  Successful sources remain available while the client waits for browser action.
- DSH guidance retries only affected sources after action completes, preserving
  query settings and disclosing that old and retried offers have different
  observation times. Opt-in retention now supports explicit same-session repeats;
  autonomous challenge completion remains outside this implementation.
- Corrected the documented offline-test count that failed the previous CI run.

### Идентификация / Identity

- `compare_verify_offer` принимает необязательный `expected_identity` и
  возвращает сведения из карточки вместе с вердиктом сопоставления. Артикул
  продавца не считается MPN; совпадение MPN требует марки. Отсутствующие
  характеристики варианта дают `unknown`, кириллические и китайские значения
  сохраняются при сопоставлении. Проверка формата GTIN отклоняет знаки,
  дробные числа и нулевой заполнитель; ведущие нули до 14 знаков не меняют
  идентичность GTIN. Проверка не меняет ранжирование цен; извлечение
  manufacturer-полей из живых источников ещё не подтверждено.
- `compare_verify_offer` accepts optional `expected_identity` and returns
  observed card evidence with a match verdict. Seller articles are not MPNs;
  MPN equality requires a brand. Incomplete variant evidence stays `unknown`,
  and non-Latin variant values retain their meaning. Malformed GTINs are
  rejected and leading-zero GTIN representations compare equally. Price
  ranking is unchanged; live manufacturer-field extraction remains unverified.
- The deliberate optional-input expansion adds about 196 estimated tokens:
  compare profile 1816 → 2023; unified profile 16030 → 16549 (values as stored in `work/performance/wire-baseline.json`). The stored
  wire baseline is updated for this contract change; the 10% gate is unchanged.

### Исправлено

- Lamoda больше не принимает слова `captcha` и «не робот» внутри скриптов,
  скрытых виджетов или названий товаров за блокировку; challenge определяется
  только по видимому тексту пустой выдачи.
- Параллельные CDP-коннекторы теперь сопоставляют созданную вкладку с её
  `targetId`, чтобы один вызов не получил вкладку другого.
- В CI добавлен гейт роста MCP wire-схем относительно сохранённого baseline.
- Кэш адреса Megamarket теперь разделён по CDP endpoint и scraping profile;
  смена профиля не использует старый адресный идентификатор.
- Добавлена датированная матрица model-level routing с отдельной проверкой
  text/structured capability и явной оговоркой, что one-shot probe не является
  универсальным рейтингом моделей.
- Яндекс Маркет: «полая» страница товара (HTTP 200, `pageId market:product`,
  все товарные коллекции пусты, нет Product ld+json) классифицируется как
  `empty_product_shell` → retryable `transport_down` у `yandex_card` и
  `inconclusive` у selfcheck, а не `parser_drift`. Причина живая: ~12–13.09
  Яндекс перевёл товарные данные поиска и карточек из SSR-состояния в
  клиентскую ленивую загрузку — знакомые семейства полей не изменили форму,
  они отсутствуют в отдаче, а по тристейт-доктрине это сервинг/сессия, не
  парсер. Search-канарейка больше не считает выдачу через ld+json-fallback
  (`ok_ldjson_only`) здоровой: probe-id для карточной проверки из такой
  выдачи ненадёжен.
- Model-level routing eval стал запускаемым: бандл из 38 кейсов
  (`work/evals/routing-cases-v1.json`, 19 routing + 19 классификаций сбоев,
  каждый с evidence-цитатой из SKILL.md), детерминированный офлайн-раннер
  `scripts/model_routing_eval.py run` (оценивает уже собранные ответы моделей,
  accuracy per-slice, «не схлопывать в одну оценку»), протокол
  мульти-модельного прогона `work/evals/MODEL_ROUTING_PROTOCOL.md` и
  single-model пилот `work/evals/pilot-2026-09-13.md`.

### Fixed

- Lamoda no longer treats `captcha` or bot wording in scripts, hidden widgets,
  or product names as a block; a challenge is classified only from visible text
  on an empty result.
- Concurrent CDP connectors now correlate a created page with its `targetId`,
  preventing one call from claiming another call's tab.
- CI now gates MCP schema wire-cost growth against the stored baseline.
- Megamarket address caching is now scoped to the CDP endpoint and scraping
  profile, so switching profiles cannot reuse the previous address identifier.
- Added a dated model-level routing matrix with separate text/structured
  capability checks; the one-shot probe is explicitly not a universal model ranking.
- Yandex Market: a hollow product page (HTTP 200, `pageId market:product`,
  every product collection empty, no Product ld+json) is now classified as
  `empty_product_shell` — a retryable `transport_down` from `yandex_card` and
  `inconclusive` in the selfcheck, not `parser_drift`. Live cause: around
  Sep 12–13 Yandex moved search/card product data from the SSR state into
  client-side lazy loading — the familiar field families did not change
  shape, they are absent from the served page, which the tri-state doctrine
  classes as serving/session, not the parser. The search canary no longer
  treats an ld+json-fallback extraction (`ok_ldjson_only`) as healthy: a
  card-probe id taken from such a page is unreliable.
- The model-level routing eval is now runnable: a 38-case bundle
  (`work/evals/routing-cases-v1.json`, 19 routing + 19 failure-classification
  cases, each with an evidence citation from SKILL.md), a deterministic offline
  runner `scripts/model_routing_eval.py run` (scores already-collected model
  answers, per-slice accuracy, never collapsed into one score), the
  multi-model run protocol `work/evals/MODEL_ROUTING_PROTOCOL.md`, and a
  single-model pilot `work/evals/pilot-2026-09-13.md`.

## [2.2.0] — 2026-09-11

### Добавлено

- `cian_search` умеет посуточную аренду: `deal="daily"` (рядом с `sale` и
  `rent`). Это отдельный рынок Циана — тот же `_type`, но `for_day: "1"` вместо
  `"!1"`; замер 2026-09-10 по Москве: 25 411 длительных квартир против 53 441
  суточной, а без флага выдача их смешивает. Работает для квартир, комнат и
  домов; коммерции посуточно у Циана нет, и такой запрос отклоняется как
  `bad_request`, а не отдаёт пустую страницу под видом «сегодня ничего нет».
- Новое поле `price_unit` в выдаче и карточке: `total` (продажа), `month`
  (длительная аренда) или `day` (посуточно). У суточных объявлений Циан не
  заполняет ни `paymentPeriod`, ни `leaseTermType` и не отдаёт `priceRur` —
  без явной единицы 5 000 ₽ за ночь читались бы как более дешёвое предложение,
  чем 90 000 ₽ за месяц.

- Коннектор Циан (`cian-connector`, скрипт `cian-mcp`, тулы `cian_search` и
  `cian_card`) — недвижимость, а не товары: квартиры, комнаты, дома и коммерция
  на продажу и в долгосрочную аренду. Только второй ярус (CDP): WAF Циана режет
  голый HTTP по IP (403 `cian_waf_block`, без капчи), а из сессии Chrome
  отвечает собственный JSON-API сайта (`search-offers-desktop`) и состояние
  карточки в `window._cianConfig`. Поиск по фильтрам (сделка, тип, регион,
  комнаты, цена, площадь), не по тексту; регионы 1/2/4593/4588 проверены живьём.
  Карточка отдаёт цену и её историю, планировку, дом, адрес, метро, описание и
  публикатора. Цена без указания — `None`, не 0. В `compare_prices` источник не
  участвует. Страница агента структурированных данных не отдаёт, поэтому тула
  `cian_agent` нет — агент приходит внутри карточки (`docs/ANTI_BOT.md` § Cian).
- Переменная `MARKETPLACE_SOURCES` выбирает, какие источники монтирует
  `marketplace-mcp`: схемы всех тулов уходят клиенту в каждом запросе, и
  оператор, которому нужны три площадки, не платит за остальные. Имена
  канонические, псевдонимы `wb`, `ym`/`yandex`, `detmir`, `ali` тоже
  принимаются. Не задана или пуста — монтируется всё, как раньше. Снятые
  источники видны в `marketplace_sources` → `skipped` с пометкой
  `deselected`, а `compare_prices` опрашивает тот же набор.

### Исправлено

- `marketplace_sources`: флаг `mounted` в `capabilities` у Яндекс Маркета и
  Детского мира всегда был `false`, даже когда они смонтированы, — таблица
  монтирования называет их `yandex`/`detmir`, а метаданные —
  `yandex_market`/`detsky_mir`.
- `MARKETPLACE_SOURCES` теперь отклоняет неизвестные имена при запуске и
  перечисляет поддерживаемые источники: опечатка больше не создаёт частичный
  сервер молча.

### Added

- `cian_search` covers daily rent: `deal="daily"` alongside `sale` and `rent`.
  It is a separate Cian market — same `_type`, `for_day: "1"` instead of `"!1"`;
  measured in Moscow on 2026-09-10: 25 411 long-term flats against 53 441 daily
  ones, and dropping the flag mixes them. Flats, rooms and houses only; Cian has
  no daily commercial market, so that pair is refused as `bad_request` rather
  than returning an empty page that reads as "nothing free today".
- New `price_unit` field on search rows and cards: `total` (sale), `month`
  (long-term rent) or `day` (daily). Cian fills in neither `paymentPeriod` nor
  `leaseTermType` on daily offers and omits `priceRur` there, so without an
  explicit unit a 5 000 ₽ night would read as cheaper than a 90 000 ₽ month.
- Cian connector (`cian-connector`, `cian-mcp` console script, tools
  `cian_search` and `cian_card`) — real estate, not goods: flats, rooms, houses
  and commercial property for sale or long-term rent. Tier 2 only: Cian's WAF
  blocks plain HTTP by IP (403 `cian_waf_block`, no captcha), while inside the
  Chrome session the site's own JSON API (`search-offers-desktop`) and the card
  state in `window._cianConfig` answer. Search is by filters (deal, type,
  region, rooms, price, area), not text; regions 1/2/4593/4588 verified live.
  The card carries the price and its history, layout, building, address, metro,
  description and publisher. A missing price is `None`, never 0. The source
  takes no part in `compare_prices`. The agent page exposes no structured data,
  so there is no `cian_agent` tool — the agent ships inside the card
  (`docs/ANTI_BOT.md` § Cian).
- `MARKETPLACE_SOURCES` selects which sources `marketplace-mcp` mounts: every
  tool schema is sent to the client on every request, so an operator who needs
  three marketplaces does not pay for the rest. Canonical names, plus the
  aliases `wb`, `ym`/`yandex`, `detmir` and `ali`. Unset or blank mounts
  everything, as before. Deselected sources appear in `marketplace_sources` →
  `skipped` marked `deselected`, and `compare_prices` queries the same subset.

### Fixed

- `marketplace_sources`: the `mounted` flag in `capabilities` always read
  `false` for Yandex Market and Detsky Mir, even when mounted — the mount table
  names them `yandex`/`detmir`, the metadata `yandex_market`/`detsky_mir`.
- `MARKETPLACE_SOURCES` now rejects unknown names at startup and lists the
  supported sources, so a typo cannot silently create a partial server.

### Исправлено

- `marketplace-connector` теперь объявляет `aliexpress-connector` жёсткой
  зависимостью: отдельный wheel unified-сервера больше не теряет AliExpress
  молча. `_mount_all` монтирует его с момента появления коннектора, но строк в
  `[project].dependencies` и `[tool.uv.sources]` не было — вне ворксплейса
  источник исчезал из mount, и единственной уликой оставался
  `marketplace_sources.skipped`. PR #47 повторил ту же ошибку с
  `cian-connector`, и её поймали только на ревью.
- Новый тест dependency-parity
  (`packages/marketplace-connector/tests/test_dependency_parity.py`) роняет
  офлайн-набор, если источник из `_mount_all` не объявлен в зависимостях
  пакета или в `[tool.uv.sources]` — и в обратную сторону тоже. Класс ошибки
  «зарегистрирован в _mount_all, забыт в pyproject» теперь закрыт гейтом, а не
  памятью ревьюера.
- `scripts/test_ops_gates.py` получил `__main__`-раннер: задокументированная
  команда `uv run python scripts/test_ops_gates.py` раньше выходила с кодом 0,
  не выполнив ни одного из своих трёх тестов, — гейт, который не может упасть,
  ничего не проверяет. Теперь команда прогоняет тесты через pytest и
  пробрасывает настоящий код возврата; `uv run pytest scripts/test_ops_gates.py`
  работает как прежде.

### Fixed

- `marketplace-connector` now declares `aliexpress-connector` as a hard
  dependency, so a standalone unified-server wheel no longer silently drops
  AliExpress. `_mount_all` has mounted it since the connector landed, but the
  rows in `[project].dependencies` and `[tool.uv.sources]` were missing —
  outside the workspace the source vanished from the mount, and the only
  evidence was `marketplace_sources.skipped`. PR #47 repeated the same mistake
  with `cian-connector`, caught only in review.
- A new dependency-parity test
  (`packages/marketplace-connector/tests/test_dependency_parity.py`) fails the
  offline suite if a source mounted by `_mount_all` is missing from the package
  dependencies or from `[tool.uv.sources]` — and in the reverse direction too.
  The "registered in _mount_all, forgotten in pyproject" bug class is now a
  gate, not a reviewer's memory.
- `scripts/test_ops_gates.py` gained a `__main__` runner: the documented
  invocation `uv run python scripts/test_ops_gates.py` used to exit 0 without
  executing any of its three tests — a gate that cannot fail checks nothing.
  It now runs them through pytest and propagates the real exit code;
  `uv run pytest scripts/test_ops_gates.py` keeps working unchanged.

### Изменено

- Офлайн-счётчик тестов в документации обновлён до 1315: четыре теста
  dependency-parity добавились к 1311. Заодно вычищен дрейф документации,
  накопившийся с приходом AliExpress и Циана: дерево пакетов в
  `docs/ARCHITECTURE.md`, счётчики навыков (15), источников (14), артефактов
  релиза (32) и мест хранения версии (84) в README, `docs/DEPLOYMENT.md`,
  `docs/RELEASE_CHECKLIST.md`, `release.yml` и dsh-бандле.
- Офлайн-счётчик тестов в документации обновлён до 1349: двадцать четыре
  теста login-стены и анти-бот-вердикта Taobao добавились к 1325.

### Changed

- Documented offline test count is 1315: four dependency-parity tests on top
  of 1311. The documentation drift that had accumulated since AliExpress and
  Cian landed is swept in the same pass: the package tree in
  `docs/ARCHITECTURE.md`, skill (15), source-server (14), release-artifact
  (32) and version-location (84) counts across README, `docs/DEPLOYMENT.md`,
  `docs/RELEASE_CHECKLIST.md`, `release.yml` and the dsh bundle.
- Documented offline test count is 1349: twenty-four Taobao login-wall and
  anti-bot-verdict tests on top of 1325.

### Исправлено

- Классификация login-стены Taobao: стена с **пустым** `<title>` (живой замер
  2026-09-10 — именно так она сейчас и выглядит) раньше доходила до вердикта
  `drift_detected`, хотя по тристейт-доктрине сессия/IP/капча — это
  `inconclusive`, а не дрейф парсера. Детектор дополнен структурными
  маркерами: login-маршруты (`member/login.jhtml`, `register.jhtml`), общее
  число анкоров (порог `< 40`; здоровая страница несёт 133), видимый текст
  тела (`/登录|log ?in|sign ?in/i`). Решает Python, экстрактор-JS только
  передаёт счётчики и сниппет. Тулы отвечают `transport_down` с инструкцией
  про вход инлайн, `taobao_selfcheck` — `inconclusive` с причиной
  `login_wall`. Регрессионная фикстура — настоящий снимок стены (trimmed,
  с provenance и тестом на отсутствие сессионных данных).
- Вердикт капчи Taobao переехал из экстрактор-JS в Python и гейтится на
  пустой выдаче: скрытый текст виджета baxia («人机») раньше давал
  `__BLOCKED__` даже на здоровых страницах с 29–38 товарами, из-за чего
  shape-drift канарейка никогда не запускалась по-настоящему. Теперь страница
  с товарами не заблокирована (канарейка работает), а страница без товаров
  с текстом капчи — `inconclusive(blocked)`; старый JS-маркер
  `__BLOCKED__` понимается для легаси-payload'ов.
- Карточка товара, в **названии** которого есть «登录»/«login», больше не
  принимается за login-стену: title-маркер на card-payload читает заголовок
  документа (`page_title`), а не имя товара.

### Fixed

- Taobao login-wall classification: a wall with an **empty** `<title>` (the
  shape observed live on 2026-09-10) used to reach the `drift_detected`
  verdict, although the tri-state doctrine classes a session wall as
  `inconclusive`, never drift. The detector gained structural markers —
  login routes (`member/login.jhtml`, `register.jhtml`), total anchor count
  (ceiling `< 40`; a healthy page carries 133) and the visible body text
  (`/登录|log ?in|sign ?in/i`). Python decides; the extractor JS only
  transports counts and a snippet. Tools answer `transport_down` with the
  log-in fix inline, `taobao_selfcheck` answers `inconclusive` with reason
  `login_wall`. The regression fixture is a real trimmed capture of the wall
  with provenance and a test asserting it carries no session data.
- Taobao's captcha verdict moved from the extractor JS into Python and is
  gated on a zero-item extraction: the hidden baxia widget text («人机») used
  to bake `__BLOCKED__` even into healthy pages rendering 29–38 items, so the
  shape-drift canary never actually ran. A page with items is now not blocked
  (the canary runs); a zero-item page carrying captcha text is
  `inconclusive(blocked)`; the legacy JS `__BLOCKED__` marker is still
  honored for replayed older-build payloads.
- A card whose product **name** contains «登录»/«login» is no longer
  mistaken for a login wall: on card payloads the title marker reads the
  document title (`page_title`), never the product name.

### Изменено

- Мегамаркет: чтение адресов профиля (`/profileService/address/list` —
  аккаунтная зона, запрос с `credentials: include` в вашем залогиненном
  Chrome) стало **opt-in**: `MEGAMARKET_USE_PROFILE_ADDRESS=1`. По умолчанию
  выключено — адрес резолвится через публичный suggest-эндпоинт из
  `MEGAMARKET_ADDRESS` (Москва), поиск работает как прежде, но без
  персонализации профиля. Использование профиля раскрывается в
  `_meta.warnings` (`address_source:profile` + `profile_address_read`);
  сырой `addressId` не покидает процесс ни в успешных ответах, ни в ошибках
  (отказ при нуле офферов называет `address_source`, а не идентификатор).
  Раньше профильный эндпоинт читался безусловно при каждом старте.
- Офлайн-счётчик тестов в документации обновлён до 1356: семь тестов
  приватности Мегамаркета добавились к 1349.

### Исправлено

- `SECURITY.md` (RU+EN) и trust-boundary абзац README (обе половины) больше
  не утверждают, что MPStats — единственная точка входа в аккаунтную зону:
  список адресов профиля Мегамаркета документирован как вторая такая
  поверхность, доступная только через явный opt-in. Раньше пользователь мог
  включить Мегамаркет, полагая, что читаются лишь публичные каталожные
  эндпоинты, а сервер читал локационные данные профиля и персонализировал
  ими выдачу (находка S4 исследования `work/v2-research/security.md`).

### Changed

- Megamarket: reading the profile addresses (`/profileService/address/list` —
  an account-gated zone, fetched with `credentials: include` inside your
  logged-in Chrome) is now **opt-in**: `MEGAMARKET_USE_PROFILE_ADDRESS=1`.
  The default is off — the address resolves through the public suggest
  endpoint from `MEGAMARKET_ADDRESS` (Moscow), so search keeps working
  without profile personalization. Profile use is disclosed in
  `_meta.warnings` (`address_source:profile` + `profile_address_read`); the
  raw `addressId` never leaves the process, in successful responses or in
  errors alike (the zero-offers refusal names the `address_source`, not the
  identifier). Previously the profile endpoint was read unconditionally at
  startup.
- Documented offline test count is 1356: seven Megamarket privacy tests on
  top of 1349.

### Fixed

- `SECURITY.md` (RU+EN) and the README trust-boundary paragraph (both
  halves) no longer claim MPStats is the only account-gated surface: the
  Megamarket profile address list is now documented as a second such
  surface, reachable only through explicit opt-in. Previously a user could
  enable Megamarket believing only public catalog endpoints were read,
  while the server read profile location data and personalized results with
  it (finding S4 of `work/v2-research/security.md`).

### Исправлено

- `yandex_search` возвращал зачёркнутую базовую цену вместо той, что
  покупатель реально платит: `price_rub` строился из `offer.price.value`
  SERP-состояния (а на скидочных строках там лежит `initialPrice`) с
  фолбэком на тот же `initialPrice`. Живой замер 2026-09-11: Tuvio
  TKP2117S — коннектор отдавал 3698 ₽ при фактических 2367 ₽ (завышение
  +61%). Теперь `price_rub` берётся из корзины SERP
  (`productPayload.cartButton.price.valueFmt`), затем из
  `additionalPrices[withDiscount]`, и лишь в последнюю очередь из цены
  оффера; `price_with_plus` — из `actualPrice`/`yaBank`; `price_old_rub` —
  только когда зачёркнутая цена строго больше фактической. Побочный эффект
  на `compare_prices`: раньше яндекс-строки ранжировались по зачёркнутой
  цене и систематически проигрывали (завышение +24…+61%), из-за чего
  «cheapest» мог доставаться другому маркетплейсу при фактически более
  дешёвом Яндексе.
- Провенанс июльских фикстур yandex-поиска записывал «displayed» цены с
  выхода багованного парсера, а не с экрана (washer: записано 22600,
  страница показывала 16724) — value-pinning тесты фактически пиннили баг.
  Значения перечитаны из тех же снимков (HTML и sha256 не менялись),
  provenance-заметка исправлена с явной пометкой о коррекции. Добавлена
  новая живая фикстура `search_kettle` (снимок 2026-09-11) с тремя
  строками: регрессия зачёркнутой цены, промежуточная цена продавца,
  SERP-офер ≠ дефолт карточки.
- Задокументирован quirk Яндекса (сайт, не коннектор): строка SERP описывает
  офер сниппета, который может отличаться от дефолтного офера карточки
  (REDMOND: KM243 sku 4668084807 @2004 в выдаче против KM245 sku
  103808288420 @4146 на карточке того же pid). Строки поиска сверяются по
  `sku_id`, а не по product-URL; docstring, SKILL.md (×2) и README (RU+EN)
  это отражают.

### Изменено

- Офлайн-счётчик тестов в документации обновлён до 1360: четыре теста
  ценового маппинга yandex-поиска добавились к 1356.

### Fixed

- `yandex_search` returned the strike-through base price instead of what a
  buyer actually pays: `price_rub` was built from the SERP state's
  `offer.price.value` (which carries `initialPrice` on discounted rows) with
  a fallback to `initialPrice` itself. Measured live 2026-09-11: Tuvio
  TKP2117S — the connector said 3698 ₽ while the real price was 2367 ₽ (a
  +61 % overstatement). `price_rub` now comes from the SERP cart price
  (`productPayload.cartButton.price.valueFmt`), then
  `additionalPrices[withDiscount]`, and only then the offer price;
  `price_with_plus` from `actualPrice`/`yaBank`; `price_old_rub` only when
  the strike-through price is strictly greater. Side effect on
  `compare_prices`: Yandex rows used to rank by the strike-through price and
  lost systematically (+24…+61 % inflation), so "cheapest" could go to
  another marketplace while Yandex was actually cheaper.
- The July search fixtures' provenance recorded "displayed" prices from the
  buggy parser's output rather than the screen (washer: recorded 22600, the
  page showed 16724) — the value-pinning tests were pinning the bug. Values
  re-read from the same captures (HTML and sha256 unchanged), the provenance
  note corrected with an explicit annotation. New live fixture
  `search_kettle` (captured 2026-09-11) covers three rows: the
  strike-through regression, an intermediate seller price, and a SERP offer
  differing from the card default.
- Documented a Yandex site-side quirk (not a connector bug): a SERP row
  describes the snippet's offer, which can differ from the card's default
  offer (REDMOND: KM243 sku 4668084807 @2004 in search vs KM245 sku
  103808288420 @4146 on the card for the same pid). Search rows verify
  against `sku_id`, not the product URL; docstring, SKILL.md (×2) and README
  (RU+EN) now say so.

### Changed

- Documented offline test count is 1360: four yandex-search price-mapping
  tests on top of 1356.

## [2.1.0] — 2026-09-09

### Добавлено

- Средний DSH-профиль `ru-marketplace-decision` (консоль-скрипт `decision-mcp`):
  сравнение цен плюс один card inspector `decision_inspect(source, product_id_or_url)`.
  Позволяет проверить победителя сравнения, продавца и карточку без полного
  37-инструментного mount; ряд включён в `dsh/cordis.patch.yml` выключенным по
  умолчанию и включается через `RU_MARKETPLACE_MCP_DECISION=1` (взаимоисключающе
  с полным mount).
- `compare_prices`: предложения теперь несут `identity` (бренд, модель, MPN,
  GTIN, атрибуты варианта) и `evidence` (provenance нативного оффера), чтобы
  точное соответствие товара проверялось, а не предполагалось.
- Reliability-скрипты: детерминированный routing-контракт DSH
  (`scripts/routing_eval.py`), операционные гейты (`scripts/test_ops_gates.py`),
  baseline/snapshot-режимы `scripts/mcp_wire.py` против регрессии wire-токенов
  и латентности.
- CDP: ограничение размера websocket-фреймов и allowlist финального хоста
  (`_check_final_host`) после навигации.

### Изменено

- `e2e_stdio_check.py` проверяет 15 stdio-серверов, включая `decision-mcp`;
  dsh-bundle guard в CI ожидает 3 MCP rows, все выключены по умолчанию.
- Офлайн-счётчик тестов в документации обновлён до 1243.

### Added

- Middle DSH profile `ru-marketplace-decision` (`decision-mcp`): comparison plus
  one card inspector `decision_inspect(source, product_id_or_url)`, shipped
  disabled by default and enabled with `RU_MARKETPLACE_MCP_DECISION=1`.
- `compare_prices` offers carry `identity` (brand, model, MPN, GTIN, variant
  attributes) and `evidence` provenance for verifiable exact-product matching.
- Reliability scripts: DSH routing eval, operational gates, and
  `mcp_wire.py` wire-token/latency regression baselines.
- CDP hardening: bounded websocket frames and a final-host allowlist.

### Changed

- `e2e_stdio_check.py` covers 15 stdio servers including `decision-mcp`; the DSH
  bundle CI guard asserts all 3 MCP rows are disabled by default.
- Documented offline test count is 1243.

## [2.0.2] — 2026-09-09

### Исправлено

- macOS: CDP-Chrome больше не забирает фокус и не перекидывает рабочий стол
  (Space) на каждый вызов. Причина была в `Target.createTarget` без
  `background: true` — так Chrome становится активным приложением, а macOS
  следует за его окном; закрытие вкладки и навигация вдобавок «расхайдивали»
  приложение. Режим `CHROME_STEALTH` (по умолчанию включён) теперь работает и на
  macOS: вкладки открываются в фоне в обоих путях (Playwright через
  browser-level CDP-сессию и raw CDP), а окно скрапинг-профиля прячется (⌘H)
  после навигации и после закрытия вкладки. `start_chrome_cdp.sh` прячет окно
  сразу после подъёма CDP. Прячется только процесс с нашим `--user-data-dir`;
  основной Chrome не трогается. `CHROME_STEALTH=0` возвращает прежнее поведение.

### Fixed

- macOS: the CDP Chrome no longer steals focus or drags the desktop to its
  Space on every call. `Target.createTarget` without `background: true` makes
  Chrome the active app and macOS follows its window; closing the tab and
  navigating un-hid the app on top of that. `CHROME_STEALTH` (on by default) now
  covers macOS: tabs open in the background on both paths (Playwright via a
  browser-level CDP session, and raw CDP), and the scraping-profile window is
  hidden (⌘H) after navigation and after the tab closes. `start_chrome_cdp.sh`
  hides the window as soon as CDP is up. Only the process running our
  `--user-data-dir` is touched; the operator's daily Chrome is left alone.
  `CHROME_STEALTH=0` restores the old behaviour.

## [2.0.1] — 2026-09-09

### Исправлено

- Docker stdio release probe обновлён до 37 tools после добавления
  `compare_verify_offer`; MCP Registry publish снова проверяет фактическую
  unified surface.

### Fixed

- Docker stdio release probe now expects 37 tools after adding
  `compare_verify_offer`, so MCP Registry publication verifies the actual
  unified surface.

## [2.0.0] — 2026-09-09

### Добавлено

- Продуктовый trust/evidence слой: comparable winner, stock-aware ranking,
  native card verification и capability metadata для routing.
- DSH progressive disclosure и activation contract.
- v2 research artifacts, eval matrix и security findings.
- Исправлена утечка upstream MPStats message в MCP ToolError.

### Added

- Product trust/evidence layer: comparable winner, stock-aware ranking, native
  card verification, and routing capability metadata.
- DSH progressive disclosure and activation contract.
- v2 research artifacts, eval matrix, and security findings.
- Fixed upstream MPStats message leakage into MCP ToolError.

## [1.9.0] — 2026-09-09

### Добавлено

- Lightweight `compare_verify_offer` проверяет победившее предложение через
  native card tool без включения full unified mount.

### Added

- Lightweight `compare_verify_offer` verifies a winning offer through its native
  card tool without enabling the full unified mount.

## [1.8.0] — 2026-09-09

### Добавлено

- `compare_prices(in_stock_only=true)` ранжирует только предложения с явно
  подтверждённым наличием, сохраняя исключённые строки для аудита.

### Added

- `compare_prices(in_stock_only=true)` ranks only offers with explicitly
  confirmed stock while retaining excluded rows for auditability.

## [1.7.0] — 2026-09-09

### Добавлено

- `compare_prices` возвращает `cheapest_comparable` — более безопасного кандидата
  после исключения очевидных аксессуаров и товаров в другом состоянии.
- `marketplace_sources` публикует статические capability-метаданные для routing:
  access tier, CDP/login, валюту, text search и mounted state.
- DSH skills разделяют cheap compare routing и full marketplace activation.

### Added

- `compare_prices` exposes `cheapest_comparable`, a safer candidate after
  excluding obvious accessories and different-condition listings.
- `marketplace_sources` exposes static routing capabilities: access tier,
  CDP/login, currency, text-search support, and mounted state.
- DSH skills separate cheap compare routing from full marketplace activation.

## [1.6.1] — 2026-09-09

### Добавлено

- `ozon_reviews` помечает каждый отзыв полем `item_id` — SKU товара, о котором отзыв
  написан на самом деле. Ozon держит один пул отзывов на всю карточку-семейство, и
  соседи по пулу регулярно оказываются другим товаром другого бренда: у карточки
  Huter 1500 Вт (SKU 5264146973, 4.8 из 356 отзывов) среди 100 вытянутых отзывов не
  было ни одного о самом Huter. Без `item_id` агент выдавал чужой рейтинг за
  собственный.
- Ответ `ozon_reviews` дополнен полями `requested_item_id`, `own_reviews` (сколько
  возвращённых отзывов относится к запрошенному SKU) и `pool_variants`
  (`SKU → название` всех товаров пула), чтобы чужой `item_id` можно было назвать.

### Added

- `ozon_reviews` tags every review with `item_id`, the SKU the review is actually
  about. Ozon serves one review pool per card family and the neighbours are often a
  different product from a different brand: a card for a 1500 W Huter (SKU
  5264146973, 4.8 across 356 reviews) returned 100 reviews, none of them about the
  Huter. Without `item_id` an agent reports a borrowed rating as the product's own.
- The `ozon_reviews` response gained `requested_item_id`, `own_reviews` (how many
  returned reviews belong to the requested SKU) and `pool_variants` (`SKU → name` for
  every product in the pool), so a foreign `item_id` can be named.

### Исправлено

- **Avito снова честный selfcheck** (спасибо @avxone за находку): Avito перенёс
  массив объявлений из верхнего уровня payload в `catalog.items[]`. Парсер уже
  читал новую форму, а смоук-проверка ждала старую и отвечала `drift_detected` на
  живом поиске. Семейства ключей принимают оба конверта, фикстура и справочник
  формы ре-фингерпринтированы по живому замеру 2026-08-29.
- CDP-поиск Taobao и Lamoda теперь отличает антибот-проверку с HTTP 200 от
  настоящего parser drift и возвращает честный `inconclusive/blocked`.
- Все пакеты ограничили FastMCP диапазоном `>=3.4.6,<4`: FastMCP 4 меняет
  публичную MCP-схему `_meta` на `meta` и несовместим с текущим контрактом.

### Fixed

- **Avito selfcheck honest again** (thanks @avxone): Avito moved the listings array
  from the payload's top level into `catalog.items[]`. The parser already followed
  it, but the smoke check waited for the old shape and answered `drift_detected`
  on live traffic. Required key families now accept both envelopes; fixture and
  shape reference re-fingerprinted from a live capture dated 2026-08-29.
- CDP search for Taobao and Lamoda now distinguishes an HTTP 200 anti-bot
  challenge from real parser drift and reports `inconclusive/blocked` honestly.
- All packages cap FastMCP at `>=3.4.6,<4`: FastMCP 4 changes the public MCP
  schema from `_meta` to `meta` and is incompatible with the current contract.

## [1.6.0] — 2026-08-20

### Добавлено

- **Коннектор AliExpress** (`aliexpress_search`, `aliexpress_card`) — одиннадцатый
  источник и пятый CDP-only. У AliExpress нет анонимного пути: x5sec ставит капчу
  на карточки, а поиск отдаёт пустую SPA-оболочку (замерено 2026-08-20,
  `docs/ANTI_BOT.md`). Коннектор садится на страницу поиска, которую не
  челленджат, и открывает карточку новой вкладкой из неё. Цены в рублях,
  участвуют в `compare_prices`.
- Честное состояние `price_missing`: под нагрузкой x5sec тихо убирает ценовой
  модуль у карточек — коннектор сообщает об этом предупреждением, а не числом.
- Тексты отзывов намеренно не отдаются (вкладка отзывов за капчей); рейтинг и
  число заказов читаются из карточки и из меты.
- Скилл `skills/aliexpress-connector/SKILL.md` (и в вендоре `dsh/`), domtest-фикстуры
  живой выдачи и карточки, tri-state selfcheck с цепочкой поиск→карточка.

### Изменено

- Объединённый сервер теперь монтирует 35 инструментов (36 с `marketplace_sources`);
  `scripts/e2e_stdio_check.py`, CI-таблицы ожидаемых инструментов и dsh-витрина
  обновлены соответственно.
- Вендор `dsh/` несёт 14 скиллов.
- `docs/CDP_SETUP.md`: восемь источников через браузер; CDP-only теперь пять.

## [1.5.1] — 2026-08-16

Патч доставки для официального MCP-реестра: OCI-образ stdio, настоящий
`docker run -i` probe в CI и публикация `server.json` через `mcp-publisher` на
каждый тег. Функциональных изменений поверхности нет.

### Добавлено

- `Dockerfile.stdio` с `MCP_TRANSPORT=stdio`, `CMD ["marketplace-mcp"]` и
  OCI-меткой владения для MCP Registry.
- `scripts/e2e_stdio_check_docker.py` — initialize / tools/list / tools/call
  через настоящий `docker run --rm -i`.
- `.github/workflows/mcp-registry-publish.yml` — на тег `v*`: сборка и push в
  GHCR, docker-stdio probe, `mcp-publisher login github-oidc` + `publish`.
- `packages` в `server.json`: `ghcr.io/vladimir-human/ru-marketplace-mcp:1.5.1`,
  `runtimeHint: docker`, transport stdio.

### Изменено

- Версия всех 72 объявлений поднята на 1.5.1.

English summary:

### Added

- `Dockerfile.stdio` with `MCP_TRANSPORT=stdio`, a default
  `marketplace-mcp` entrypoint and the MCP Registry ownership label.
- `scripts/e2e_stdio_check_docker.py`: a real initialize / tools/list /
  tools/call session over `docker run --rm -i`.
- A tag-driven workflow that pushes the OCI image to GHCR, runs the docker
  stdio probe, then authenticates through GitHub OIDC and publishes
  `server.json` with `mcp-publisher`.
- An `oci` package entry in `server.json` pointing at the 1.5.1 GHCR image.

### Changed

- All 72 version declarations bumped to 1.5.1.

## [1.5.0] — 2026-08-16

Основной релиз: установка в DeepSeek Harness и снижение постоянной цены
MCP-контекста втрое. Теперь есть бандл `dsh/` (13 скиллов сразу, две
MCP-строки выключены до явного включения), `marketplace-mcp install dsh`,
гейты бандла в тестах и CI.

### Изменено

- **Выходные схемы сжаты до имён полей верхнего уровня.** По реальному
  MCP-проводу: `marketplace-mcp` — 38 078 → 13 020 токенов на запрос,
  `compare-mcp` — 2 424 → 918. Доля выходных схем упала с 64 % до 10 %.
- **11 `*_selfcheck` ушли с MCP-поверхности** и остались CLI-only:
  `marketplace-mcp doctor` их по-прежнему вызывает. Модельный набор — 33
  инструмента в двенадцати серверах и 34 в объединённом вместо 44/45.
- **Скиллы больше не предлагают агенту вызвать selfcheck.** Проверка паритета
  теперь про раздел: пункт под заголовком «Tools available» обязан быть
  зарегистрированным MCP-инструментом.
- **Описания скиллов `mpstats-connector` и `compare-prices`** приведены под
  лимит каталога dsh (476 и 449 символов).

### Добавлено

- **Бандл `dsh/`** и разделы про dsh в корневом README на обоих языках.
- **Гейт установки бандла в CI** (`dsh-bundle`): состав профиля, 13 скиллов,
  обе MCP-строки `disabled`, откат через `remove`.
- **`mcp_wire.py` / `mcp_startup.py`** — замеры цены и старта MCP-серверов по
  настоящему протоколу.

### Исправлено

- `dsh/package.json` выпадал из git из-за общего ignore `package.json`
  (CI checkout был без манифеста бандла); добавлено исключение.
- Smoke-работа CI и `e2e_stdio_check.py` ожидали прежние счётчики
  инструментов (вплоть до 45); приведены к новой поверхности.

English summary:

### Changed

- Output schemas now advertise top-level field names only. Measured over a real
  stdio session: `marketplace-mcp` fell from ~38,078 to ~13,020 tokens per
  request, `compare-mcp` from ~2,424 to ~918. Output-schema cost share: 64% → 10%.
- The 11 operator `*_selfcheck` diagnostics left the MCP surface and remain
  CLI-only via `marketplace-mcp doctor`. The model-facing surface is now 33
  tools across twelve servers and 34 in the unified server, instead of 44/45.
- Skills no longer list selfchecks under "Tools available"; the parity gate now
  checks that a tools-list entry names a registered MCP tool.

### Added

- A DeepSeek Harness bundle in `dsh/` with 13 skills and two disabled
  MCP rows; root README sections in Russian and English.
- A CI job that installs the bundle into a clean profile and asserts the profile
  layer, 13 skills, both MCP rows disabled, and clean removal.
- `scripts/mcp_wire.py` and `scripts/mcp_startup.py` for wire-level cost and
  startup measurements.

### Fixed

- `dsh/package.json` was silently ignored by the root `package.json` gitignore
  rule, so CI checkouts shipped without the bundle manifest; an exception now
  tracks it.
- CI smoke and `e2e_stdio_check.py` still expected the old tool counts (up to
  45); they now match the new surface.

## [1.4.1] — 2026-08-08

Патч по итогам четырёх независимых аудитов, проведённых сразу после v1.4.0.
Две находки закрыты; контракт инструментов и гейт не менялись.

### Исправлено

- **`coerce_int` и его дубль `compare._as_count` больше не фабрикуют счётчики
  из юникодных знаков и range-строк.** Гард знаков покрывал только ASCII
  `[-+]`, поэтому юникодный минус (U+2212) и тире (U+2013/U+2014), которыми
  маркетплейсы рендерят диапазоны, проходили насквозь: «1 000–2 000»
  конкатенировалось в 10002000, «−5» читалось как 5. Теперь и то и другое —
  `None` (fail loud), в паритете с `coerce_price`. Целочисленные и float-входы
  не тронуты — property-тест pass-through целых остался зелёным. Обнаружено
  независимым аудитом доктрины, 5 красных тестов до фикса.

### Добавлено

- **Провенансы для 7 живых фикстур**, у которых их не было (citilink
  `search_grid`, avito `js_items_live`, 5 фикстур yandex). Закрыто нарушение
  правила «фикстура без `.provenance.json` считается выдуманной»; теперь каждая
  живая фикстура имеет url/дату/sha256/метод съёма/ground truth.

English summary:

### Fixed

- `coerce_int` and its `compare._as_count` duplicate no longer fabricate counts
  from unicode signs and ranges. The sign guard only covered ASCII `[-+]`, so a
  unicode minus (U+2212) or dash (U+2013/U+2014) slipped through —
  «1 000–2 000» concatenated to 10002000, «−5» read as 5. Both now return
  `None` (fail loud), matching `coerce_price`. Integer/float inputs are
  unchanged (the integer pass-through property test stays green). Found by an
  independent doctrine audit; 5 red tests before the fix.

### Added

- Provenance files for 7 live fixtures that lacked them (citilink
  `search_grid`, avito `js_items_live`, 5 yandex fixtures). No live fixture is
  left without url/date/sha256/capture-method/ground-truth provenance.

## [1.4.0] — 2026-08-08

Релиз паритета доктрины и живых доказательств. Полтора десятка локальных
парсеров цен и счётчиков приведены к одной гарантии — «никогда 0, никогда
отрицательная, никогда не-конечная, никогда не бросает» — и зафиксированы
тестами на байтах, реально снятых с маркетплейсов. Плюс транспорт теперь
переживает Chrome новее, чем понимает Playwright. Контракт инструментов не
менялся; все изменения — в устойчивости чтений и в честности вердиктов.

### Исправлено

- **Каждый локальный парсер цены/счётчика закрыт от не-цен.** `compare._as_price`
  (отрицательные, inf, OverflowError), `compare._as_count` (NaN/Infinity,
  тихий сброс знака «-3» → 3), `yandex.ssr._to_number/_to_int` (inf и
  OverflowError огромных чисел), `wb._kopeck_to_rub` (inf и OverflowError на
  гигантских копейках), `avito._posted_at` (OverflowError огромного
  timestamp'а), `mcp_core.coerce_rating` и `parse_retry_after` (OverflowError,
  inf/nan). Одна отравленная ячейка больше не обрывает вызов инструмента.
- **`compare._as_price` делегирован в `mcp_core.coerce_price`.** Дублирующая
  реализация фабрикующе склеивала range-строки («1 000–2 000 ₽» → 10 002 000).
  Перевод доказан живьём: 144 ценовые строки, снятые с поиска и карточки Ozon,
  парсятся обеими реализациями одинаково, кроме range-строк, где дубликат
  фабриковал, а общий парсер честно отказывает.
- **Три живых дрейфа карточного экстрактора Taobao**, пойманные съёмом живой
  карточки: заголовок читал плейсхолдер «按图片搜索» (на современной странице
  нет h1, имя — в mainTitle-спане), цена не находилась вовсе (современная
  раскладка рендерит её спанами symbol/text внутри highlightPrice/subPrice, а
  не одним глиф-узлом), shop/sales склеивали текст всей страницы. Экстрактор
  читает и старую, и новую раскладку; обе зафиксированы фикстурами.
- **Два дрейфа поискового экстрактора Lamoda**, пойманные живым съёмом: бейдж
  скидки «−49%» внутри image-anchor'а читался как заголовок, а конкатенированные
  размеры плитки уходили в зачёркнутую цену (3.5e16 ₽). Заголовок теперь берётся
  из product-name-узла, ценовой hunt скоуплен в ценовой блок.
- **Разрыв цен WB search-vs-card переизмерен живьём** и описан честно: на пяти
  товарах 1.7–34.3 %, направление не униформно по товарам, поисковая цена
  протухает (не менялась 10 дней при движении карточной). Правило «цитируй
  `wb_card`, сверяй до победителя» встроено в скилл; ранжирование не тронуто.

### Добавлено

- **Raw-CDP fallback в `mcp-core`.** Chrome 151 перестал отвечать на
  connect_over_cdp-рукопожатие Playwright (проверено на Playwright 1.61 и 1.62:
  websocket соединяется, attach висит; сырые CDP-команды по тому же сокету
  отвечают). Транспорт при таймауте attach'а ведёт вкладку по сырому протоколу:
  scheme-guard, NavBlocked-вердикты по статусу главного документа и
  evaluate-семантика Playwright сохранены. На старых Chrome ничего не меняется —
  Playwright-путь байт-в-байт прежний. Это вернуло к работе все CDP-источники
  на свежем Chrome: `doctor` показывает 6 healthy вместо 3.
- **Живые фикстуры с провенансом** (sha256 LF-байтов, метод съёма, ground
  truth): поиск Ozon (composer JSON, снят из прогретой сессии — в лоб
  composer-API отвечает 403 анти-фрода), карточка Taobao, поиск Lamoda, поиск и
  карточка WB (v9/card v4), карточка и категория Детского Мира, поиск
  Мегамаркета. Парсеры исполняются на этих байтах в офлайн-тестах.
- **Сверка живой сигнатуры в selfcheck Lamoda и Taobao** (shape_reference):
  selfcheck сравнивает форму живого ответа с эталоном, снятым с этих фикстур, и
  именует пропавшие семейства ключей. Все четыре CDP-источника с DOM-поиском
  теперь под сверкой.
- **Секции `## Return Format` и `## Error Format` у всех 45 инструментов.**
  Выяснилось, что griffe обрезает описание инструмента после `Args:` — секции,
  стоявшие ниже, не попадали в живой маунт. Секции перенесены перед `Args:`;
  структурный тест пинит наличие обеих секций у каждого смонтированного
  инструмента.
- `docs/releases/RELEASE_NOTES_v1.4.0.md` с таблицей, какие источники проверены живо и как,
  а какие не проверены и почему.

### Прочее

- Офлайн-тесты: 980 → 1146 (+166). Покрытие ветвей 78.83 % (порог 70 %).
- Версия согласована в 72 местах (`check_versions.py`), счётчик тестов в 7
  местах сверяет `check_test_count.py`, e2e_stdio_check — 13/13 серверов.
- Живой `doctor` на момент выпуска: wildberries, ozon, yandex, detmir, taobao,
  citilink — healthy; avito (анти-бот), dns (401 сессии), megamarket
  (ServicePipe), mpstats (нет платного токена) — честные inconclusive.
- Бюджет живых запросов прогона: 25 из 40 (см. журнал живого прогона).

English summary:

### Fixed

- Every local price/count parser is closed against non-prices: compare
  `_as_price`/`_as_count`, yandex `_to_number`/`_to_int`, wb `_kopeck_to_rub`,
  avito `_posted_at`, mcp-core `coerce_rating`/`parse_retry_after` — negatives,
  inf/NaN, OverflowError. One poisoned cell no longer aborts a tool call.
- compare's `_as_price` now delegates to `mcp_core.coerce_price`; the duplicate
  fabricated prices out of range strings (proved on 144 live Ozon strings).
- Three live drifts in the Taobao card extractor and two in the Lamoda search
  extractor, caught by capturing the live pages; both layouts stay supported.
- The WB search-vs-card gap re-measured live (1.7–34.3 %, direction not uniform,
  search index goes stale); the skill now says quote `wb_card`.

### Added

- Raw-CDP fallback in mcp-core: Chrome 151 no longer answers Playwright's
  connect_over_cdp handshake, so the transport drives the tab over raw CDP when
  the attach times out. Restores every CDP source on modern Chrome (doctor: 6
  healthy instead of 3); behaviour on older Chrome is unchanged.
- Live fixtures with provenance (sha256, capture method, ground truth) for Ozon
  search, Taobao card, Lamoda search, WB search/card, Detsky Mir card/category,
  Megamarket search; parsers run on those bytes offline.
- Live shape-signature checks in the Lamoda and Taobao selfchecks.
- `## Return Format` / `## Error Format` on all 45 tools (moved before `Args:`
  because griffe truncates descriptions after it), pinned by a structural test.
- `docs/releases/RELEASE_NOTES_v1.4.0.md` with the verified/unverified source table.

### Other

- Offline tests 980 → 1146 (+166); branch coverage 78.83 % (floor 70 %).
- Version agreed in 72 places; live doctor: 6 healthy, 4 honestly inconclusive
  (avito anti-bot, dns 401, megamarket ServicePipe, mpstats needs a paid token).

## [1.3.1] — 2026-08-04

Hardening-правки по итогам независимого аудита. Поведение инструментов не
менялось: имена, аргументы и формы ответов те же, что в v1.3.0.

### Исправлено

- Экстракторы DOM декодируют вывод как явный UTF-8: без этого фрагмент с
  кириллицей мог быть прочитан в cp1251, а тест-раннер jsdom терял
  не-ASCII-символы в фикстурах.
- Глифы валют генерируются из одного списка, а не из трёх разъехавшихся
  регулярных выражений; юани признаются наравне с рублём.
- DNS: признак наличия на карточке берётся из `textContent`, а не из
  сломанного соседнего элемента.
- Taobao: оба экстрактора переведены на общие DOM-хелперы из `mcp-core`.
- Тесты экстракторов закреплены за снятыми фикстурами: Lamoda (поиск),
  Citilink (карточка), Taobao (поиск и карточка), DNS (карточка). Раньше
  поведение проверялось только на живой странице, которая может дрейфовать.

### Добавлено

- DOM-фикстуры и тесты для карточек DNS и Citilink и для поиска Taobao и
  Lamoda — десять новых тестов.

### Прочее

- Число офлайн-тестов в документации приведено к измеренному: 980.

English summary:

### Fixed

- DOM extractors decode output as explicit UTF-8.
- Currency glyph regexes are generated from one list; yuan is admitted.
- DNS card availability falls back to `textContent`.
- Taobao's extractors share the `mcp-core` DOM helpers.
- Extractor behaviour is pinned to captured fixtures (Lamoda search,
  Citilink card, Taobao search and card, DNS card).

### Added

- DOM fixtures and extractor tests for DNS/Citilink cards and Taobao/Lamoda
  search — ten new tests.

### Other

- The documented offline test count is the measured 980.

## [1.3.0] — 2026-07-30

Новый коннектор MPStats, первый платный источник в проекте. До этого все серверы
читали анонимные scrape-эндпоинты без ключей, а MPStats устроен качественно
иначе: это аналитический слой поверх Ozon и Wildberries, с квотой и аккаунтом. Он
опционален: без `MPSTATS_MP_AUTH` сервер запускается, инструменты отвечают
`auth_missing`, а остальные двенадцать серверов работают как прежде.

Первоначальный код коннектора прислал [@Xpos587](https://github.com/Xpos587) в
[PR #5](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/5). Здесь он
переработан под инварианты, которых держится остальной проект, но идея, разбор
API плагина и структура парсеров — его.

### Добавлено

**Новый сервер `mpstats-mcp` (3 инструмента)**
- `mpstats_item(skus, place, oz_fbs=True)` — аналитика продаж/цены/остатков за 30
  дней по до 100 SKU Ozon или Wildberries: 4 графика по дням (заказы, цены,
  остатки, рубрики), текущая цена и остаток (последняя ненулевая ячейка графика),
  продавец/бренд, агрегаты `totals` (orders/sum/sum_prev), скользящий
  `orders_per_day`.
- `mpstats_warehouses(skus, place)` — остатки по складам: FBS (склад продавца) и
  FBO (склад маркетплейса), плюс `last_update` апстрима.
- `mpstats_selfcheck()` — канарейка в трёх состояниях: `success` /
  `drift_detected` / `inconclusive`. Отсутствие токена и транспортные сбои
  попадают в `inconclusive`, а не в `drift`, чтобы не гнать мейнтейнера искать
  дрейф схемы, которого не было.

**Авторизация.** Эндпоинт `POST plugin.mpstats.io/pluginapi` работает в стиле
RPC: метод лежит в поле `Request`, параметры называются `Sku`, `Place`, `ozFBS`.
Авторизует одна cookie `mp_auth`, то есть JWT из залогиненной сессии плагина на
mpstats.io, и задаётся она через `MPSTATS_MP_AUTH`. По данным автора холодный
replay с одним только `mp_auth` отвечает 200 с данными, а без него приходит тело
`{"code":403,"message":"Unauthorized"}` под HTTP 200. Этот внутренний код мапится
в `auth_missing`, чтобы отказ авторизации не читался как «нет данных». Токен
остаётся секретом платного аккаунта с квотой: он не логируется и не коммитится.

**Транспорт.** Первый ярус на httpx, но только POST, а `get_text_budgeted` из
`mcp-core` умеет исключительно GET. Поэтому в коннекторе живёт локальный помощник
`_post_json_budgeted` с теми же инвариантами: байтовый кап, бюджет по времени,
классифицированные ошибки, ретраи только транспортных сбоев, вежливый гейт между
попытками. POST-путь в общий рантайм не добавлен намеренно. Он нужен одному
коннектору, а перенос в ядро расширил бы тестовую поверхность для всех
тринадцати.

**Парсеры.** Графики длиной `days` (по умолчанию 30), от старых к новым: последняя
ненулевая ячейка — текущая цена/остаток, снятое с продажи SKU даёт `None`, не
ложный `0`, который выиграл бы сравнение «где дешевле». Остаток при сплошь
нулевом графике ведёт себя наоборот и становится `0`, потому что «нет на складе»
это показание, а не отсутствие данных. Пустой график даёт `None` в обоих случаях.
Ноль в отдельной ячейке значит «нет данных за тот день», а не «значение было
нулевым». `coerce_int` и `coerce_price` отказываются угадывать неоднозначные
формы, будь то знак или диапазон цен: лучше `None`, чем правдоподобное и неверное
число. 52 офлайн-теста
покрывают парсеры, контракт ошибок, auth-гейт, три-стейт selfcheck и транспорт
`_post_json_budgeted` (байтовый кап, wall-clock бюджет, ретраи только транспортных
сбоев, классификация ошибок).

**Прочее.** `mpstats-mcp` смонтирован в объединённый `marketplace-mcp` (стало 45
инструментов: 44 смонтированных плюс `marketplace_sources`), добавлен в `doctor`,
registry-запись в `server.json`, навык `skills/mpstats-connector`, README (RU+EN),
CI-счётчик инструментов.

## [1.2.1] — 2026-07-28

Патч по итогам стороннего ревью. Поведение инструментов не менялось; правки
касаются документации и одного пути к модулю в тестовом харнессе.

### Исправлено

- **Скидочный бейдж мог стать ценой.** `coerce_price` обещает в докстринге
  «NEVER a negative», но выполнял это только для чисел: строка `-500 ₽`
  разбиралась в 500, потому что токенайзер смотрел на цифры и не смотрел на
  знак перед ними. Маркетплейсы печатают такой бейдж рядом с настоящей ценой, а
  меньшее число выигрывает сравнение «где дешевле» — тот же класс, что рассрочка
  в поле цены. Теперь ведущий минус (в четырёх начертаниях) отбрасывает
  кандидата.
- **`NaN` и бесконечность роняли инструмент целиком.** `json.loads` принимает
  оба по умолчанию, а `int()` на них падает; у `coerce_price` проверка на
  конечность была, у `coerce_int` — нет. Одна испорченная ячейка обрывала весь
  вызов вместо того, чтобы обнулить одно поле.
- **Токен MPStats попадал в текст `ToolError`.** В лог ошибка писалась
  вычищенной, а в ответ инструмента — сырой; достаточно перевода строки в конце
  скопированного токена, чтобы httpx положил весь заголовок `Cookie` в текст
  исключения, а тот уходит модели и в транскрипт клиента. Остальные коннекторы
  вычищают это место; MPStats был единственным с секретом и единственным без
  вычистки. Заодно `mp_auth` стал `SecretStr`, так что `repr` и `model_dump`
  настроек больше не печатают его.
- **Отказ авторизации кешировался.** Запись в кеш шла по HTTP 200 до разбора
  внутреннего `code`, а MPStats отдаёт и `403`, и свои `5xx` под двухсотым
  статусом. Пользователь вставлял свежую cookie и продолжал получать
  `auth_missing`, пока не истечёт TTL. Кеш переехал за вердикт.
- **`uv run pre-commit install` из `CONTRIBUTING.md` не работал.** Хуки
  документированы как запускаемые через `uv run`, но самого `pre-commit` в
  dev-зависимостях не было: команда падала на чистом клоне и проходила только у
  того, у кого он стоял глобально. Добавлен в группу `dev`.
- **Комментарий в `resilience.py` ссылался на три коннектора, которых в проекте
  нет** (`youtube`, `fourpda`, `x`), и обосновывал устройство трёхсостоянийного
  selfcheck ссылкой на консультацию с языковой моделью. Первое — след чужого
  проекта, второе ничего не сообщает читателю. Обоснование там и без этого
  самодостаточное, ссылки убраны, названы девять коннекторов, которые контракт
  реально используют.
- **MPStats не замедлялся после отказа.** Восемь коннекторов ходят через общий
  `mcp_core.pacing.Pacer`, который после `429` или блокировки удлиняет паузу;
  MPStats был единственным со своим трёхстрочным гейтом и после отказа шёл в ту
  же стену с той же скоростью. Для источника, чья оферта делает превышение темпа
  основанием для блокировки аккаунта без возврата денег, это била по пользователю.
  Переведён на общий `Pacer`, отказы и успехи записываются.
- **Одна ошибка у Lamoda уходила невычищенной.** `TransportDownError` с текстом
  исключения без `_redact` — единственное такое место в проекте после правок
  MPStats.
- **Число офлайн-тестов в документации снова разошлось** — и дважды подряд по
  вине самого аудита, который его же и правил, потому что добавлял тесты. Цифра
  стоит в семи местах и правится руками, то есть портится по расписанию. Теперь
  её сверяет `scripts/check_test_count.py` (в гейте, в CI и в pre-commit),
  сравнивая с тем, что реально отбирает коллекция. Заодно выяснилось, что число
  было неоднозначным: тестов существует 946, а без необязательного jsdom
  проходит 937 и девять честно скипаются.
- **Пин `mcp-core` мог разойтись с версией и остаться незамеченным.** Внутри
  workspace `uv.sources` перекрывает ограничение, поэтому устаревший пин проходит
  и `uv lock`, и `uv sync`, и весь набор тестов — ударяет он только по тому, кто
  ставит колесо со страницы релиза. `check_versions.py` теперь сверяет и пин:
  мест стало 72 вместо 59.
- **Зависимость `mcp-core` не была запинена, а имя занято на PyPI** посторонним
  пакетом. Колесо, скачанное со страницы релиза и поставленное `pip`, тихо
  подтягивало чужой код, после чего сервер падал на импорте. Во всех тринадцати
  коннекторах стоит `mcp-core==1.2.1`: теперь это громкий отказ разрешения
  зависимостей вместо тихой подмены.
- **`classify_http_error` падал при любом вызове** — импортировал `mcp_common`,
  пакета с таким именем в проекте нет. Функция экспортирована из `mcp_core`, но
  ни разу не вызывалась, поэтому никто не замечал. Импорт исправлен на
  `mcp_core.errors`.
- **CI-шаг «Every server imports and registers its tools» проверял 11 серверов
  из 12.** `mpstats` был в списке ожиданий, но не в проверяемом словаре, и цикл
  его пропускал, печатая «all servers registered their expected tools». Теперь
  расхождение ключей — первая же ошибка шага.
- `check_versions.py` не замечал исчезнувшего объявления версии: удаление строки
  `__version__` просто уменьшало число в сводке. Теперь это провал.
- `pre-commit` был красным на чистом дереве: девяти файлам не хватало
  завершающего перевода строки. Двум исходникам он дописан, а снятые с сайтов
  фикстуры исключены из хука — это разметка-улика, править её нельзя.
- `scripts/start_chrome_cdp.sh` был закоммичен без бита исполнения, хотя
  документация просит запускать его напрямую.
- Заголовок `start_chrome_cdp.ps1` описывал несуществующий коннектор и советовал
  логиниться в x.com, а выделенный профиль называл «для параноиков» — при том
  что он и есть поведение по умолчанию.

### Исправлено в документации

- Число офлайн-тестов, счёт артефактов релиза (28, не 26), число тестов MPStats
  и замер покрытия приведены к измеренному.
- Проза про `*_PROXY` называла среди семи коннекторов Taobao, у которого этой
  настройки нет намеренно, и не называла MPStats, у которого она есть.
- `CONTRIBUTING.md` и `docs/ADDING_A_SOURCE.md` советовали голый
  `uv run pytest -q`, который собирает живые тесты; в шаблоне PR остался глоб
  `packages/*/src`, не работающий в PowerShell.
- «CI прогоняет линтер, типы и все тесты на трёх ОС» — на трёх ОС идут только
  тесты, остальное один раз на Ubuntu.
- Навык Мегамаркета обещал данные пройденному челленджу; на деле нужен вход в
  аккаунт. Навык Ozon показывал `ozon_search(query)` и «top 20 results» вместо
  настоящей сигнатуры с `page`.
- Разное поведение цены и остатка при сплошь нулевом графике описано прямо, а не
  одним обещанием `None` на оба поля.
- `SECURITY.md` в русской секции утверждал, что учётных данных нет вообще,
  включая «ни требования заводить `.env`»; английское зеркало уже было
  поправлено, русское — нет.

- **`npm install jsdom` не работал так, как написано в README.** Проба искала
  модуль из корня репозитория и находила его, а раннер запускался из временного
  каталога, где Node его уже не видел. Девять DOM-тестов падали вместо того,
  чтобы честно скипнуться. Теперь проба возвращает путь, по которому jsdom
  нашёлся, и передаёт его раннеру через `NODE_PATH`.
- **Число офлайн-тестов в документации разошлось на три значения.** README писал
  812, `docs/ARCHITECTURE.md` — 726, `AUDIT_REPORT.md` содержал и 822, и 726.
  Измеренное значение 822 теперь стоит везде.
- **Быстрый старт в README запускал живые тесты.** Команда `uv run pytest -q` с
  подписью «сеть не нужна» собирала четыре теста, которым сеть нужна, и у
  пользователя за пределами России падала на Wildberries. В примере теперь тот
  же фильтр, что в CI.
- **Шаг чек-листа проверял пустое множество.** `pytest -m "cdp"` собирает ноль
  тестов: маркер объявлен в `pyproject.toml`, но не стоит ни на одном тесте.
  Команда убрана, вместо неё сказано, что этот ярус проверяется вызовами
  `*_selfcheck` и сверкой глазами. Из `ARCHITECTURE.md` убрано утверждение, что
  CDP-тесты помечены маркером.
- Число заменяемых записей конфига в разных файлах стояло как «десять» и
  «двенадцать». Верное значение — одиннадцать: всего двенадцать серверов, и
  объединённый монтирует остальные одиннадцать.
- Абзац про карточку Lamoda дублировался в обеих языковых секциях этого файла.
- **Версия релиза не доехала до `__version__`.** Тринадцать пакетов сообщали
  бы `1.2.0` из установленного колеса с метаданными `1.2.1`. Ловится тем, что
  версию никто не сверял: она написана в пятидесяти пяти местах и правится
  руками. Теперь сверяет `scripts/check_versions.py` — он в гейте, в CI и в
  pre-commit, и именно он нашёл этот дефект.
- **`uv run mypy packages/*/src` не работает в PowerShell.** Глоб раскрывает
  оболочка, а PowerShell для нативных команд этого не делает, и mypy получал
  путь с звёздочкой буквально. В CI команда проходила (там bash), у владельца
  падала. Дерево переехало в `files` внутри `[tool.mypy]`, команда стала
  `uv run mypy` и ведёт себя одинаково везде.
- `npm install jsdom` оставляет рядом `package.json` и `package-lock.json` —
  оба добавлены в `.gitignore` вслед за `node_modules/`.
- «Бесценное объявление» заменено на «объявление без цены»: первое означает
  «неоценимо дорогое». Та же калька была в английском зеркале и в тексте навыка
  Авито.
- `node_modules/` добавлен в `.gitignore`.

### Изменено

- README получил раздел «Как это сделано» с прямым указанием, что код и
  документация писались с ИИ-ассистентами, и с перечнем того, чем это проверено.
- `AUDIT_REPORT.md` прошёл редакторскую правку: плотность тире снижена с одного
  на 45 слов до одного на 180, навязчивая антитеза «X, а не Y» убрана в
  большинстве мест. Числа, идентификаторы и вердикты не тронуты.

## [1.2.0] — 2026-07-28

Шесть новых маркетплейсов, объединённый сервер и настраиваемый CDP-хост. 41
инструмент в 11 серверах вместо 22 в 5. Имена и сигнатуры двадцати двух
инструментов 1.1.0 не менялись — только добавления.

### Добавлено

**Новые маркетплейсы**
- **Авито** (`avito_search`, `avito_card`, `avito_seller`, `avito_selfcheck`) —
  объявления через внутренний `js/items` API. Двухуровневый, как Ozon:
  TLS-имперсонация с резидентного IP, дальше ваш Chrome по CDP. Авито — это
  объявления, а не каталог: пула отзывов на товар нет, репутация продавца и есть
  сигнал доверия. Объявление без цены (обмен, даром, цена по запросу) приходит с
  `price_rub: null`, не `0`.
- **Taobao** (`taobao_search`, `taobao_card`, `taobao_selfcheck`) — поиск и
  карточки. Поиск Taobao — клиентское React-приложение с подписанным mtop API,
  поэтому все чтения идут внутри вашего Chrome, где сайт сам подписывает запросы.
  Цены в юанях (CNY) и не конвертируются: зашитый курс молча устарел бы.
- **Мегамаркет** (`megamarket_search`, `megamarket_card`, `megamarket_selfcheck`) —
  мобильный JSON API через CDP (ServicePipe). Отказ по IP (code 7) мапится в
  `transport_down`, а не в данные.
- **Lamoda** (`lamoda_search`, `lamoda_card`, `lamoda_selfcheck`) — карточки
  анонимно через GraphQL, поиск через CDP. Рейтингов у Lamoda нет нигде —
  `rating` не входит в схему GraphQL, и это задокументировано.
- **DNS-Shop** и **Ситилинк** (`dns_*`, `citilink_*` — поиск, карточка,
  selfcheck) — отрисованный DOM через CDP (Qrator proof-of-work). Бинарный
  gRPC-web Ситилинка осознанно не реверсится.

Четыре источника (Мегамаркет, Lamoda, DNS, Ситилинк) были отклонены в 1.0/1.1,
потому что анонимный пробинг не мог их подтвердить. CDP-уровень это изменил.
Их in-browser формы задокументированы в `docs/ANTI_BOT.md` и ждут живого
подтверждения из вашей сессии — именно это и показывают их `*_selfcheck`.

**Объединённый сервер**
- `marketplace-mcp` монтирует все установленные коннекторы как один namespaced
  набор инструментов — одна запись в конфиге клиента вместо одиннадцати. Имена
  инструментов (`wb_search`, `avito_seller`, …) не меняются.
- Операторский CLI: `marketplace-mcp install` печатает готовый блок
  `mcpServers`, `marketplace-mcp doctor` запускает все selfcheck разом плюс пробу
  CDP-сессии и умеет писать машиночитаемый JSON-снапшот (`--status-file`).

**Инфраструктура**
- `CHROME_CDP_HOST` в `mcp-core`: куда дозвониться CDP-клиенту (по умолчанию
  `127.0.0.1`). Из контейнера ставьте `chrome` (сайдкар) или
  `host.docker.internal` — tier-2 источники заработали в Docker без host
  networking. Автозапуск Chrome только на loopback: удалённый хост значит, что
  браузером управляете вы сами.
- `probe_session()` в `chrome_cdp`: health-check CDP-сессии для диагностики,
  никогда не бросает исключение.
- Адаптеры Avito и Taobao в `compare-connector`. Taobao отчитывается с ценами в
  юанях и **никогда не ранжируется** против рублёвых — зашитая конверсия
  сфабриковала бы выгоду.
- Ночная live-job в CI: канарейки реальных эндпоинтов по расписанию, отдельно от
  push-гейта.
- Контрактные тесты в `mcp-core` пинают инварианты: None-not-zero, файрвол ≠
  данные, блок ≠ отсутствие товара.
- Дрифт-защита в `yandex_search`: страница, где ни у одного товара нет цены или
  заголовка, предупреждает о вероятном дрейфе SSR вместо уверенного пустого ответа.
- Skills и examples для всех новых коннекторов, плюс `compare_with_china.py`.

### Изменено

- `docs/ANTI_BOT.md` переписан: четыре «отклонённых» источника теперь в разделе
  «требовавших CDP-уровня», с вердиктами под v1.2.0.
- `docs/DEPLOYMENT.md`: tier-2 история обобщена на все challenge-gated источники
  и Chrome-сайдкар.
- `docker-compose.yml`: опциональный Chrome-сайдкар с named volume для профиля.
- `MarketOffer` в `compare-connector` получил поля `currency` и `price_native`.
  Рублёвые источники дублируют в `price_native` свою же цену, Taobao кладёт туда
  юани. Ранжирование теперь явно фильтрует по `currency == "rub"`, а не полагается
  на то, что `price_rub` случайно окажется `None`.
- `marketplace-mcp doctor` различает три исхода кодом возврата: `0` — всё здорово,
  `1` — дрейф парсера, `2` — проверить не удалось (блок, нет CDP, не тот регион).
  Раньше «ничего не смогли проверить» возвращало `0`, то есть выглядело как успех.
- `marketplace-mcp install` печатает реальный путь к вашей копии репозитория, а не
  заглушку `/path/to/...`, и отказывается печатать конфиг для неизвестного клиента.

### Исправлено

- **SSRF в `citilink_card` и `dns_card`.** Идентификатор товара доставался
  нежёстким `.search()` и служил только пропуском, а навигация шла по исходной
  строке. Ссылка вида `https://чужой-хост/product/<24-hex>/` проходила проверку и
  открывалась в Chrome оператора со всеми его куками. Теперь хост проверяется, а
  URL собирается из `SITE_BASE` по извлечённому id. Добавлены регрессионные тесты
  на шесть враждебных форм ввода, включая scheme-relative и `javascript:`.
- **Docker-образ не собирался.** Слой зависимостей копировал 6 манифестов из 13, и
  `uv sync --all-packages --frozen` падал на `Distribution not found`. Копируются
  все тринадцать.
- **Молчаливый дрейф парсера в `megamarket_search`.** Ответ, разобранный в ноль
  товаров, отдавался как успех, и `compare_prices` считал сравнение полным, хотя
  Мегамаркет не дал ничего. Теперь отсутствие самого массива товаров и `total > 0`
  без разобранных позиций поднимают `parser_drift`. Пустой массив под известным
  ключом по-прежнему честный нулевой результат.
- **Шесть коннекторов сообщали чужую версию.** Avito, Taobao, Мегамаркет, Lamoda,
  DNS и Ситилинк не передавали `version` в `FastMCP`, поэтому клиенту по протоколу
  уходила версия библиотеки FastMCP (3.4.4) вместо 1.2.0. Видно только в живой
  MCP-сессии — статическая проверка `SERVER_VERSION` этого не ловит.
- **Прокси-пароль утекал в логи.** `redact` не вырезал `user:pass@` из URL, а прокси
  настраивается именно такой строкой, так что ошибка соединения уносила логин и
  пароль в stderr и в ответ клиенту.
- **`marketplace-connector` не объявлял ни одного коннектора** в зависимостях, хотя
  монтирует одиннадцать: установка пакета отдельно давала пустой сервер, потому что
  `ImportError` глушится по замыслу. Новый инструмент `marketplace_sources`
  показывает, что смонтировано и почему остальное пропущено.
- **`avito-connector` не объявлял `playwright`**, хотя импортирует CDP-транспорт:
  на чистой установке падал при первом же бане. Extra `mcp-core[cdp]` тянул
  `websockets`, хотя транспорт работает через Playwright.
- `compare_prices` схлопывает дубликаты по паре «источник и id товара». Один и тот
  же товар мог занять и первое, и второе место и выглядеть как два независимых
  подтверждения цены.
- `shape_signature()` в `mcp-core` наконец существует: `ARCHITECTURE.md` и docstring
  ссылались на функцию, которой не было ни в одном файле.
- Ситилинк отдавал пользователю ошибки «DNS navigation blocked» и открывал свой
  модуль строкой про DNS — копипаста из соседнего коннектора.
- В CI появились проверка `check_no_print.py` (была только в pre-commit), `UV_FROZEN: 1`
  и живая MCP-сессия по stdio для всех двенадцати серверов.
- **Мегамаркет: последняя причина — редирект на категорию.** `/catalog/?q=ноутбук`
  площадка перенаправляет на `/catalog/noutbuki/`, и url/parse отвечает
  `collection: None` для общего поискового URL, но настоящей коллекцией для той
  категории, куда редирект ведёт. Мы отправляли в url/parse неперенаправленный
  URL, поэтому коллекции не было и поиск возвращал `listingSize > 0` с пустым
  `items` даже после исправления тела и адреса. Редирект теперь проходится в
  браузере, и в url/parse уходит финальный URL. Подтверждено живьём: коллекция
  502202, 44 товара.
- Разобранные параметры поиска кэшируются на запрос: каждое разрешение стоит
  навигации в браузере плюс вызова API, а агент задаёт один и тот же вопрос
  многократно.
- Офлайн-набор перестал спать на живом пейсере. Тесты Мегамаркета и Lamoda
  прогоняли настоящий `_polite_wait` с трёхсекундным интервалом, и «офлайн»
  прогон занимал 93 секунды вместо шести. Плюс шаг редиректа в тестах пытался
  дотянуться до Chrome.
- **Мегамаркет искал, не спросив у Мегамаркета, что значит запрос.** Даже с
  верными именами полей, резолвленным адресом и `requestVersion: 10` поиск
  отвечал `listingSize > 0` и пустым `items`. Пропущенного не было в теле — не
  было самого запроса: текстовый запрос площадка сначала разбирает через
  `urlService/url/parse` и выдаёт предполагаемую коллекцию, а поиск ждёт её в
  `collectionId` и `selectedAssumedCollectionId`. Без них листингу нечего
  перечислять. `xob0t/mmparser` тоже никогда не собирает тело из строки: он
  сначала отправляет каталожный URL в url/parse. Теперь так же, плюс коды
  фильтров (`LEFT_BOUND` → 1) конвертируются как ждёт эндпоинт. Если url/parse
  недоступен, поиск всё равно идёт — просто без подсказок.
- **Мегамаркет не передавал адрес доставки, и поиск возвращал ноль товаров.**
  Живой прогон из залогиненной сессии с пройденным челленджем: HTTP 200,
  `success: true`, `listingSize: 44`, `items: []`. Не блок, не разлогин, не
  уехавшая схема — в запросе не было адреса. У каждого предложения свои
  `deliveryPossibilities`, поэтому без адреса доставляемых предложений нет, а
  `listingSize` продолжает считать найденное в каталоге. Адрес теперь
  резолвится: сначала адрес по умолчанию из профиля (тогда цены совпадают с
  тем, что оператор видит на сайте), иначе город из `MEGAMARKET_ADDRESS` через
  публичный suggest. Непустой `listingSize` с пустым `items` больше не выдаётся
  за честный ноль — это ошибка с указанием, что настроить.
- **Мегамаркет посылал неполное тело запроса и читал не ту схему.** Поиск уходил
  как `{"text", "page"}` — API принимает такое с HTTP 200 и отвечает пустым
  `items`, неотличимым от «ничего не нашлось». Реальный конверт требует
  `searchText`, `requestVersion`, `limit`/`offset` и ещё десяток полей; страницы
  считаются смещением, а не номером. Вдобавок элемент результата не плоский:
  товар лежит в `goods`, цена в `favoriteOffer`, а `goodsId` несёт суффикс
  продавца. И карточка стучалась в `productCard/get`, которого в API нет, вместо
  `productCardMainInfo/get`. Схема выверена по поддерживаемому `xob0t/mmparser`,
  который ходит в тот же endpoint.
- `is_available` из поиска Мегамаркета больше не теряется: новое поле в ответе,
  и `compare_prices` берёт из него наличие вместо жёсткого `None`.
- **Карточка Lamoda не читалась из-за двух ошибок сразу, обе проверены живьём.**
  Запрос просил поле `old_price`, которого нет: эндпоинт отвечает HTTP 200 и
  `{"error": "Internal server error", "code": -32603}` вообще без данных.
  Published-имя — `old_price_amount`. Но и с верным полем парсер бы не нашёл
  товар: у Lamoda нестандартный конверт — товары в `result`, а не в
  `data.products`, а ошибка приходит одной строкой `error`, а не массивом
  `errors`. Неизвестный SKU даёт `result: null`, и это честный not_found, а не
  дрейф. Все три конверта сняты с живого эндпоинта и лежат в тестах.
- В JS-экстракторе Lamoda `\/p\/` записывался как `\/p\/` в неraw-строке —
  Python это терпит с предупреждением и сломает в будущей версии.
- **Поиск Ситилинка и DNS теперь читает и вложенные фреймы.** Живая проверка с
  резидентного IP показала раскладку, где все 27 товарных ссылок лежат в iframe,
  а в верхнем документе их нет ни одной. Экстрактор читал только `document` и на
  такой странице честно сообщал дрейф при исправном парсере — отсюда и плавающий
  результат между прогонами.
- **`compare_prices` предупреждает про товар в другом состоянии.** На живом
  запросе «iphone 15» дешёвые позиции Wildberries оказались «Восстановленный» и
  «Витринный образец» — их ранжировало против новых телефонов. Словарь
  аксессуаров такое не ловит, и разрыв в треть цены не дотягивает до проверки по
  медиане, так что нужен был отдельный признак.
- **Пейсинг переехал в `mcp-core` и научился отступать.** Восемь коннекторов
  носили по своей копии `_polite_wait`, и ни одна не отличала успешный запрос от
  отказа. Живой прогон в июле 2026 показал цену этого: Taobao и DNS сначала стали
  здоровыми, а потом развалились после серии запросов подряд. Общий `Pacer`
  держит паузу между запросами, удлиняет её после отказа и считает отказы
  подряд: после нескольких он говорит оператору сменить адрес или перелогиниться
  вместо шестого одинакового «заблокировано». Публичные парсеры этих площадок
  живут ровно на этих трёх привычках.
- **Мегамаркет больше не путает разлогин с отсутствием товара.** Пустой `items`
  при пройденном ServicePipe — это неавторизованная сессия, а не сломанный
  парсер: с начала 2025 площадка отвечает анонимному клиенту пустотой, а не
  ошибкой (это же описывает публичный mmparser). Selfcheck теперь отдаёт
  `inconclusive` с причиной `not_authenticated` вместо `drift`, а поиск
  прикладывает предупреждение. Пропавший массив по-прежнему `parser_drift`,
  а `total > 0` без разобранных позиций — тоже.
- Появился `.editorconfig`: отступы и переводы строк для тех, чей редактор не
  читает pyproject.
- **`doctor` скрывал причину, по которой проверка не удалась.** Коннекторы
  классифицируют отказ (`rate_limited`, `blocked`, `transport_down` с HTTP-кодом),
  но CLI печатал только слово `inconclusive`. Три разные ситуации с тремя разными
  действиями выглядели одинаково. Причина и код теперь в выводе.
- `compare_prices` предупреждает, когда самое дешёвое предложение похоже на
  аксессуар, а не на искомый товар, и когда его цена вдвое ниже медианы по
  остальным. Только предупреждение: порог, подобранный без живых выдач, не
  должен получать право что-то скрывать. Поводом стал живой прогон, где по
  запросу iPhone 15 предложение за 34 224 ₽ обошло настоящие за 52 049 ₽.
- `scripts/diagnose_drift.py` научился зонду для Мегамаркета (API вместо DOM,
  форма ответа через `shape_signature`) и различает «селектор уехал» и «товары
  вообще не в DOM, а в JSON-состоянии» — случай Lamoda.
- **DNS и Ситилинк не находили ни одного товара.** `_PRODUCT_ID_RE` требовал 24
  hex-символа — формат MongoDB ObjectId, которого нет ни на одной из площадок:
  Ситилинк отдаёт `/product/noutbuk-lenovo-2169270/`, DNS `/product/b7a1667f9b19ed20/`.
  Поиск разбирался в ноль плиток и уходил в `parser_drift`. Баг дожил до релиза
  потому, что фикстуры выдумывали id в том же неверном формате, которого ждал
  парсер, — тесты соглашались с ошибкой. Теперь в них реальные маршруты,
  снятые с живой сессии, и отдельная проверка, что JS в странице и Python-парсер
  читают один и тот же набор символов.

### Что нашла независимая перепроверка перед выпуском

Перед выпуском релиз прошёл сплошной аудит: весь гейт воспроизведён с нуля, а
часть источников сверена с живыми страницами. Ниже — то, что он изменил.

#### Исправлено

- **Цена могла оказаться платежом по рассрочке.** Экстракторы выбирали цену как
  наименьшее число на плитке, а плитка DNS показывает «от 5 751 ₽/ мес.» рядом с
  ценой 58 999 ₽. Такое значение проходит валидацию и выглядит правдоподобно —
  то есть хуже, чем пустое поле. Ценой теперь считается только число,
  привязанное к знаку валюты; рассрочка, бонусы, бейдж скидки и счётчик пунктов
  выдачи отбрасываются.
- **`dns_search` отдавал 24 ссылки с `title=None` и `price=None`.** Плитка
  искалась через `closest()`, а он проверяет сам элемент раньше предков: первый
  товарный якорь в плитке DNS — ссылка на картинку, у которой нет текста.
  Замерено на снятой сетке: у найденного узла `textContent` длиной 0 против 402
  у настоящего корня плитки.
- **Ситилинк не находил цену никогда** — знак ₽ у него лежит в отдельном
  элементе от цифр, а старый фильтр требовал их в одной текстовой строке.
  Экстрактор переведён на стабильные атрибуты `data-meta-*`, которые сайт держит
  для своей аналитики, вместо классов, меняющихся каждой сборкой.
- **`avito_search` падал целиком из-за одного вложенного поля** — `location`
  приходит объектом, а не строкой. На живом ответе вскрылись ещё два дефекта: у
  каждого результата отсутствовала ссылка (настоящий ключ `urlPath`, а искали
  `uriPath` — одна буква) и дата публикации (её нет строкой, только
  `sortTimeStamp` в миллисекундах).
- **`wb_search` за концом выдачи отдавал первую страницу под видом новой.**
  Проверено живьём: `page=20` вернула те же 100 товаров в том же порядке, что и
  `page=1`, с HTTP 200 и без признака, тогда как докстринг обещал в этом случае
  `WbNoResultsResponse`. Коннектор запоминает отпечаток первой страницы и,
  встретив его снова, отвечает так, как обещано. Собственный `total` от WB для
  этого непригоден — он больше реально отдаваемой глубины.
- Экстракторы читают `textContent`, а не `innerText`: последний зависит от
  раскладки и видимости, то есть ровно от того, что различается между прогретой
  вкладкой и только что открытой.

#### Добавлено

- Тесты, которые прогоняют **настоящий JS коннектора** по снятой разметке (через
  jsdom) и сверяют результат с ценами, которые в тот момент были на странице:
  `dns-connector` и `citilink-connector`. Раньше экстракторы не исполнял ни один
  из 707 тестов — дефекты жили точно в непокрытом слое. Без jsdom DOM-половина
  честно скипается, питоновская идёт всегда.
- Фикстура живого ответа Авито и контрактные тесты по ней: форма `location`,
  цена из `priceDetailed.value`, наличие ссылки у каждого результата, ISO-дата.
- Общий слой извлечения `mcp_core.dom` (разрешение плитки и выбор цены) и
  харнесс `mcp_core.domtest` — вместо четырёх копий, расходившихся по-разному.
- `test_skills_parity.py`: коннектор без навыка, навык с несуществующим
  инструментом, забытый инструмент и несоответствие опубликованному контракту
  frontmatter теперь роняют прогон.

#### Навыки

- Навык DNS требовал ссылку вида `/product/<24-hex>/` — тот самый шаблон, который
  чинили как баг. Настоящий id у DNS 16-hex, у Ситилинка — слаг с числом. Это
  жило ещё и в описаниях параметров `dns_card`/`citilink_card` и в текстах их
  ошибок, то есть в контракте, который MCP-клиент показывает модели.
- У WB не были описаны `wb_questions` и `wb_category_products`: навык учил
  получить дерево категорий и не давал инструмента, чтобы по нему сходить.
- Навыки DNS и Ситилинка советовали проверять раскладку через `selfcheck` — при
  том что DNS это ровно тот случай, где selfcheck позеленел при пустых данных.
- Навыки поехали вместе с серверами: `Dockerfile` копирует `skills/` в образ, а
  `.dockerignore` получил исключение `!skills/**/SKILL.md` — без него `COPY`
  положил бы в образ пустые каталоги. README получил раздел о навыках.

#### Документация

- Числа приведены к измеренным: 822 теста, покрытие 77.7 %.
- `AUDIT_REPORT.md` переписан. Прошлая версия утверждала «10/10 источников
  healthy» рядом с приложенным `doctor-status.json`, где 7 healthy, 2
  inconclusive и 1 drift; оба утверждения не могли быть верны.
- `ADDING_A_SOURCE.md` больше не пишет, что «Lamoda была отвергнута» — Lamoda
  поставляется; описан реальный случай: карточки по GraphQL, поиска в нём нет.
- `ANTI_BOT.md` больше не пишет «DNS went healthy». Записана честная
  последовательность: правка регулярки id позеленила `selfcheck`, а данные
  остались пустыми.

## [1.3.0] — 2026-07-30 (English)

New MPStats connector — the first paid source in the project. Until now every
server worked over anonymous scrape endpoints with no keys; MPStats is a
different kind: an analytics layer over Ozon/Wildberries with a quota and an
account. It is optional: without `MPSTATS_MP_AUTH` the server boots, the tools
answer `auth_missing`, and the other twelve servers behave exactly as before.

The connector's original code came from [@Xpos587](https://github.com/Xpos587) in
[PR #5](https://github.com/Vladimir-Human/ru-marketplace-mcp/pull/5). It was
reworked here for the invariants the rest of the project holds to, but the idea,
the plugin API work and the parser structure are his.

### Added

**New `mpstats-mcp` server (3 tools)**
- `mpstats_item(skus, place, oz_fbs=True)` — 30-day sales/price/stock analytics
  for up to 100 Ozon or Wildberries SKUs: 4 per-day graphs (orders, prices,
  stock, categories), current price/stock (last non-zero graph cell),
  seller/brand, `totals` aggregates (orders/sum/sum_prev), rolling
  `orders_per_day`.
- `mpstats_warehouses(skus, place)` — warehouse stock split: FBS (seller's
  warehouse) and FBO (marketplace warehouse), plus upstream `last_update`.
- `mpstats_selfcheck()` — tri-state canary: `success` / `drift_detected` /
  `inconclusive`. A missing token and transport failures report `inconclusive`,
  not `drift`, so the maintainer is not sent hunting a schema drift that never
  happened.

**Auth.** Endpoint `POST plugin.mpstats.io/pluginapi` (RPC style: method in the
`Request` field, parameters as `Sku`/`Place`/`ozFBS`). Auth is a single
`mp_auth` cookie (JWT from a logged-in plugin session at mpstats.io), set via
`MPSTATS_MP_AUTH`. Per the author's report: a cold replay with only `mp_auth`
answers 200 with data; without it the body is
`{"code":403,"message":"Unauthorized"}` behind HTTP 200 — that inner code maps
to `auth_missing` so an auth failure does not read as "no data". The token is a
secret on a paid, quota-billed account; it is never logged or committed.

**Transport.** Tier 1 (httpx) but POST-only, so instead of the GET-only
`get_text_budgeted` from `mcp-core` the connector carries a local
`_post_json_budgeted` helper with the same invariants: byte cap, wall-clock
budget, classified errors, retries of transport failures only, a polite gate
between attempts. No POST path was added to `mcp-core` on purpose: POST is
needed by one connector, and promoting it to the shared runtime would grow the
test surface for everyone.

**Parsers.** Graphs are `days` long (30 by default), oldest first: the last
non-zero cell is the current price/stock, a delisted SKU yields `None`, not a
false `0`. A zero cell means "no data for that day", not "the value was zero".
`coerce_int`/`coerce_price` refuse to guess ambiguous shapes (signs, price
ranges) — `None` instead of a plausibly-wrong number. 52 offline tests cover
the parsers, the error contract, the auth gate, the tri-state selfcheck and the
`_post_json_budgeted` transport (byte cap, wall-clock budget, retries of
transport faults only, classified errors).

**Also.** `mpstats-mcp` is mounted into the unified `marketplace-mcp` (now 45
tools: 44 mounted plus `marketplace_sources`), added to `doctor`, the registry
entry in `server.json`, the `skills/mpstats-connector` skill, README (RU+EN),
and the CI tool-count.

## [1.2.1] — 2026-07-28 (English)

A patch release following an outside review. Tool behaviour is unchanged; the
fixes cover documentation and one module path in the test harness.

### Fixed

- **`npm install jsdom` did not work the way the README described it.** The
  probe resolved the module from the repository root and found it, while the
  runner executed from a temp directory where Node could not. Nine DOM tests
  failed instead of skipping. The probe now reports where jsdom was found and
  hands that path to the runner through `NODE_PATH`.
- **The offline test count disagreed with itself across three files.** README
  said 812, `docs/ARCHITECTURE.md` said 726, `AUDIT_REPORT.md` carried both 822
  and 726. The measured figure, 822, is now used everywhere.
- **The README quickstart ran live tests.** `uv run pytest -q`, labelled "no
  network needed", collected four tests that need one and failed on Wildberries
  for anyone outside Russia. The example now uses the same filter as CI.
- **A checklist step verified an empty set.** `pytest -m "cdp"` collects zero
  tests, because the marker is declared but carried by none. The command is gone;
  the checklist now says this tier is exercised through `*_selfcheck` calls and
  by comparing against the site. `ARCHITECTURE.md` no longer claims CDP tests are
  marked.
- The number of config entries the unified server replaces was written as ten in
  one file and twelve in another. It is eleven.
- A Lamoda paragraph was duplicated in both language sections of this file.
- **The release version never reached `__version__`.** Thirteen packages would
  have reported `1.2.0` from a wheel whose metadata said `1.2.1`. A version
  string is written in fifty-five places here and was compared by nobody;
  `scripts/check_versions.py` now compares them, in the gate, in CI and in
  pre-commit — and it found this defect.
- **`uv run mypy packages/*/src` does not work in PowerShell,** which does not
  expand globs for native commands, so mypy received the path with a literal
  asterisk: green in CI on bash, red on the maintainer's machine. The file list
  moved into `[tool.mypy]` and the command is now plain `uv run mypy`.
- `npm install jsdom` also leaves `package.json` and `package-lock.json`; both
  join `node_modules/` in `.gitignore`.
- "Бесценное объявление" means an invaluable listing, not one without a price;
  reworded here, in the English mirror and in the Avito skill.
- `node_modules/` is now ignored.

### Changed

- The README gained a "How this was built" section stating plainly that the code
  and documentation were written with AI assistants, and listing what verifies them.
- `AUDIT_REPORT.md` was copy-edited: dash density dropped from one per 45 words to
  one per 180, and the repeated "X, not Y" antithesis is mostly gone. Numbers,
  identifiers and verdicts were left untouched.

## [1.2.0] — 2026-07-28 (English)

Six new marketplaces, a unified server, and a configurable CDP host. 41 tools
across 11 servers, up from 22 across 5. The 22 v1.1.0 tool names and signatures
are unchanged — additions only.

### Added

**New marketplaces**
- **Avito** (`avito_search`, `avito_card`, `avito_seller`, `avito_selfcheck`) —
  classifieds via the internal `js/items` API. Two-tier like Ozon: TLS
  impersonation from a residential IP, then your Chrome over CDP. Avito is
  classifieds, not a catalog: no per-item reviews — seller reputation is the
  trust signal. A listing with no price (swap, free, price on request) reports `price_rub: null`, never `0`.
- **Taobao** (`taobao_search`, `taobao_card`, `taobao_selfcheck`) — search and
  cards. Taobao search is a client-side React app over the signed mtop API, so
  every read runs inside your Chrome where the site signs requests natively.
  Prices stay in yuan (CNY): a baked-in rate would go silently stale.
- **Megamarket** (`megamarket_search`, `megamarket_card`,
  `megamarket_selfcheck`) — mobile JSON API via CDP (ServicePipe). The code-7 IP
  refusal maps to `transport_down`, not data.
- **Lamoda** (`lamoda_search`, `lamoda_card`, `lamoda_selfcheck`) — anonymous
  GraphQL cards plus CDP-backed search. Lamoda exposes no ratings anywhere.
- **DNS-Shop** and **Citilink** (`dns_*`, `citilink_*`) — rendered DOM via CDP
  (Qrator proof-of-work). Citilink's binary gRPC-web is deliberately not reversed.

Four sources were rejected in v1.0/v1.1 because anonymous probing could not
confirm them end to end. The CDP tier changed that. Their in-browser shapes are
documented in `docs/ANTI_BOT.md` and await live confirmation from your session —
exactly what their `*_selfcheck` tools report.

**Unified server**
- `marketplace-mcp` mounts every installed connector as one namespaced toolset —
  one client config entry instead of ten. Tool names keep their prefixes.
- Operator CLI: `marketplace-mcp install` prints the `mcpServers` block,
  `marketplace-mcp doctor` runs every selfcheck plus a CDP session probe, with an
  optional machine-readable JSON snapshot (`--status-file`).

**Infrastructure**
- `CHROME_CDP_HOST` in `mcp-core`: where the CDP client dials (default
  `127.0.0.1`). From a container set `chrome` (sidecar) or
  `host.docker.internal` — tier-2 sources work in Docker without host networking.
  Chrome autostart is loopback-only.
- `probe_session()` in `chrome_cdp`: a never-raising CDP session health-check.
- Avito and Taobao adapters in `compare-connector`. Taobao reports yuan prices
  and is **never ranked** against rubles.
- Nightly live CI canaries, separate from the push gate.
- Contract tests pinning invariants: None-not-zero, firewall ≠ data, blocked ≠
  absent.
- `yandex_search` drift guard: a page where every product lost its price or
  title warns of likely SSR drift instead of a confident empty answer.
- Skills and examples for every new connector, plus `compare_with_china.py`.

### Changed

- `docs/ANTI_BOT.md` rewritten: the four "rejected" sources moved to a
  "needed the CDP tier" section with v1.2.0 verdicts.
- `docs/DEPLOYMENT.md`: the tier-2 story generalised across challenge-gated
  sources and the Chrome sidecar.
- `docker-compose.yml`: optional Chrome sidecar with a named profile volume.
- `MarketOffer` in `compare-connector` gained `currency` and `price_native`.
  Rouble sources mirror their own price into `price_native`; Taobao puts yuan
  there. Ranking now filters on `currency == "rub"` explicitly instead of relying
  on `price_rub` happening to be `None`.
- `marketplace-mcp doctor` separates three outcomes by exit code: `0` healthy,
  `1` parser drift, `2` could not be judged (blocked, no CDP, wrong region).
  "We checked nothing" used to exit `0`, which reads as success.
- `marketplace-mcp install` prints the real path to your checkout instead of a
  `/path/to/...` placeholder, and refuses to print a config for an unknown client.

### Fixed

- **SSRF in `citilink_card` and `dns_card`.** The product id came out of an
  unanchored `.search()` and acted only as a gate — navigation used the original
  string. A URL like `https://attacker.example/product/<24-hex>/` passed validation
  and opened in the operator's Chrome with all its cookies. The host is now checked
  and the URL rebuilt from `SITE_BASE` around the extracted id, with regression
  tests covering six hostile inputs including scheme-relative and `javascript:`.
- **The Docker image did not build.** The dependency layer copied 6 of 13 manifests,
  so `uv sync --all-packages --frozen` failed with `Distribution not found`. All
  thirteen are copied now.
- **Silent parser drift in `megamarket_search`.** A response that parsed to zero
  items returned success, and `compare_prices` then called the comparison complete
  while Megamarket had contributed nothing. A missing items container, or `total > 0`
  with nothing parsed, now raises `parser_drift`. An empty array under a known key
  is still an honest zero-result answer.
- **Six connectors advertised the wrong version.** Avito, Taobao, Megamarket,
  Lamoda, DNS and Citilink never passed `version` to `FastMCP`, so the protocol
  handshake reported the FastMCP library version (3.4.4) rather than 1.2.0. Only a
  live MCP session shows this; grepping `SERVER_VERSION` does not.
- **Proxy passwords leaked into logs.** `redact` did not strip `user:pass@` from a
  URL, and proxies are configured as exactly that, so a connect error carried the
  credentials into stderr and into the client's error text.
- **`marketplace-connector` declared none of the connectors** it mounts, so
  installing the package on its own produced an empty server — the `ImportError` is
  swallowed by design. A new `marketplace_sources` tool reports what mounted and why
  anything else was skipped.
- **`avito-connector` did not declare `playwright`** despite importing the CDP
  transport, so a clean install died at the first ban. The `mcp-core[cdp]` extra
  pulled `websockets` although the transport runs on Playwright.
- `compare_prices` collapses duplicates on (source, product id). One listing could
  otherwise take both first and second place and read as two independent
  confirmations of a price.
- `shape_signature()` exists in `mcp-core` at last: `ARCHITECTURE.md` and a docstring
  referenced a function that was in no file.
- Citilink returned "DNS navigation blocked" to users and opened its module with a
  line about DNS — copy-paste from the neighbouring connector.
- CI gained the `check_no_print.py` guard (previously pre-commit only), `UV_FROZEN: 1`,
  and a real stdio MCP session against all twelve servers.
- **Megamarket: the last cause was a search-to-category redirect.**
  `/catalog/?q=ноутбук` redirects to `/catalog/noutbuki/`, and url/parse answers
  `collection: None` for the generic search URL but a real collection for the
  category it lands on. We were posting the un-redirected URL, so there was no
  collection and search kept returning `listingSize > 0` with an empty `items`
  even after the body and the address were fixed. The redirect is now followed in
  the browser and the final URL goes to url/parse. Verified live: collection
  502202, 44 items.
- Resolved search params are cached per query: each resolution costs a browser
  navigation plus an API call, and an agent asks the same question repeatedly.
- The offline suite no longer sleeps on the real pacer. Megamarket and Lamoda
  tests were running the real `_polite_wait` with its three-second gap, turning
  an "offline" run into 93 seconds instead of six. The redirect step was also
  reaching for Chrome from tests.
- **Megamarket searched without asking Megamarket what the query means.** Even
  with the right field names, a resolved address and `requestVersion: 10`, search
  answered `listingSize > 0` with an empty `items`. What was missing was not in
  the body but a request we never made: the site parses a text query through
  `urlService/url/parse` into an assumed collection, and the search endpoint
  expects it in `collectionId` and `selectedAssumedCollectionId`. Without them the
  listing has nothing to list. `xob0t/mmparser` never builds a body from a query
  either — it POSTs the catalog URL to url/parse first. We now do the same, and
  convert filter bounds (`LEFT_BOUND` → 1) to the codes the endpoint wants. If
  url/parse is unavailable the search still runs, just without the hints.
- **Megamarket sent no delivery address, so search returned zero products.**
  A live run from a logged-in, challenge-passed session answered HTTP 200 with
  `success: true`, `listingSize: 44` and `items: []`. Not a block, not a
  logged-out session, not a moved shape — the request carried no address. Every
  offer has its own `deliveryPossibilities`, so with no address there is no
  deliverable offer while `listingSize` still counts what the catalog matched.
  The address is now resolved: the profile's default address first, so prices
  match what the operator sees on the site, then the `MEGAMARKET_ADDRESS` city
  through the public suggest endpoint. A positive `listingSize` with an empty
  `items` array is no longer passed off as an honest zero — it is an error that
  names the setting to change.
- **Megamarket posted an incomplete request body and read the wrong shape.**
  Search went out as `{"text", "page"}`, which the API accepts with HTTP 200 and
  answers with an empty `items` array — indistinguishable from "nothing matched".
  The real envelope needs `searchText`, `requestVersion` and `limit`/`offset`
  paging plus a dozen more fields. A result item is also not flat: the product
  nests under `goods`, its price under `favoriteOffer`, and `goodsId` carries a
  merchant suffix. The card hit `productCard/get`, which is not part of the API,
  instead of `productCardMainInfo/get`. Schema verified against the maintained
  `xob0t/mmparser`, which drives the same endpoint.
- Megamarket's per-item `is_available` is no longer dropped: it reaches the
  response as a new field and `compare_prices` reads stock from it instead of
  hardcoding `None`.
- **The Lamoda card failed on two bugs at once, both verified live.** The query
  asked for `old_price`, which does not exist: the endpoint answers HTTP 200 and
  `{"error": "Internal server error", "code": -32603}` with no data at all. The
  published name is `old_price_amount`. Even with the right field the parser
  would have missed the product: Lamoda's envelope is not the standard one —
  products sit under `result` rather than `data.products`, and a failure arrives
  as a single `error` string rather than an `errors` array. An unknown SKU
  returns `result: null`, which is an honest not_found rather than drift. All
  three envelopes were captured from the live endpoint and are pinned in tests.
- Fixed an invalid escape in Lamoda's JS extractor string, which Python tolerates
  with a warning today and will reject in a future version.
- **Citilink and DNS search now read nested frames too.** A live check from a
  residential IP found a layout where all 27 product links sit inside an iframe
  and the top-level document holds none. The extractor read only `document`, so
  on that layout it reported drift with a perfectly good parser — which is what
  made the result flicker between runs.
- **`compare_prices` warns about a different product condition.** On a live
  "iphone 15" query the cheap Wildberries rows were "Восстановленный" and
  "Витринный образец" — a refurbished phone and a display unit, ranked against
  new ones. The accessory list cannot catch that, and a third off the median is
  not steep enough for the outlier check, so it needed its own signal.
- **Pacing moved into `mcp-core` and learned to back off.** Eight connectors each
  carried a copy of `_polite_wait`, and not one of them knew a successful request
  from a refusal. The live July 2026 run showed what that costs: Taobao and DNS
  both went healthy and then fell over after a burst of back-to-back calls. The
  shared `Pacer` holds a gap between requests, lengthens it after a refusal, and
  counts consecutive refusals — after a few it tells the operator to change the
  address or refresh the session instead of returning a sixth identical
  "blocked". The public parsers that survive on these marketplaces live on
  exactly those three habits.
- **Megamarket no longer confuses a logged-out session with an absent product.**
  An empty `items` array behind a passed ServicePipe challenge means the session
  is not authenticated, not that the parser broke: since early 2025 the site
  answers an anonymous client with emptiness rather than an error, which the
  public mmparser project documents too. Selfcheck now reports `inconclusive`
  with reason `not_authenticated` instead of `drift`, and search attaches a
  warning. A missing array is still `parser_drift`, and so is `total > 0` with
  nothing parsed.
- Added `.editorconfig` for contributors whose editor does not read pyproject.
- **`doctor` hid why a check could not be judged.** Connectors classify the
  refusal (`rate_limited`, `blocked`, `transport_down`, with the HTTP code) but the
  CLI printed only the word `inconclusive`. Three situations needing three
  different responses looked identical. The reason and code now show.
- `compare_prices` warns when the cheapest offer reads as an accessory rather than
  the product searched for, and when its price sits below half the median of the
  rest. Warnings only: a threshold tuned without live data has no business hiding
  a row. Prompted by a live run where an iPhone 15 query ranked a 34 224 ₽ listing
  above genuine 52 049 ₽ ones.
- `scripts/diagnose_drift.py` gained a Megamarket probe (API rather than DOM,
  envelope fingerprinted with `shape_signature`) and now separates "the selector
  moved" from "the products are not in the DOM at all, they are in JSON state" —
  the Lamoda case.
- **DNS and Citilink found no products at all.** `_PRODUCT_ID_RE` demanded 24 hex
  characters — a MongoDB ObjectId shape neither site uses: Citilink serves
  `/product/noutbuk-lenovo-2169270/`, DNS `/product/b7a1667f9b19ed20/`. Search
  parsed zero tiles and reported `parser_drift`. It shipped because the fixtures
  invented ids in the same wrong shape the parser expected, so the suite agreed
  with the bug. They now carry routes observed on a live session, plus a check
  that the in-page JS and the Python parser read the same id charset.

### Found by the pre-release audit

The release went through a full independent audit: every gate reproduced from
scratch, and part of the sources compared against live pages.

- **A price could be read as the instalment shown beside it.** Extractors took
  the smallest number on a tile, and a DNS tile advertises "от 5 751 ₽/ мес."
  next to a 58 999 ₽ price. That validates and looks plausible — worse than a
  null. A price is now only a number attached to a currency glyph.
- **DNS search returned 24 links with no title and no price.** Tiles were
  resolved with `closest()`, which tests the element itself before any ancestor
  and so landed on the image link, which carries no text.
- **Citilink never found a price** — it renders the ₽ glyph in an element
  separate from the digits. The extractor now keys on Citilink's stable
  `data-meta-*` attributes rather than its build-hashed class names.
- **Avito search failed entirely over one nested field** (`location` arrives as
  an object). The live payload also showed every search hit missing its URL
  (`urlPath`, not `uriPath`) and its publication date (`sortTimeStamp` in epoch
  milliseconds; there is no date string in the response at all).
- **Wildberries search served page 1 again past the end of the result set** —
  `page=20` returned the same 100 products in the same order as `page=1`, HTTP
  200, no marker, while the docstring promised a no-results response.
- Extraction moved into a shared `mcp_core.dom` layer, and tests now run the
  connectors' real JavaScript against captured markup instead of mocking the
  render call away — the layer where all of the above had been hiding.
- Every connector's skill is now checked against the code by
  `test_skills_parity.py`, and the skills ship inside the Docker image.

## [1.1.0] — 2026-07-26

Технический долг, дыры в функциональности и два новых инструмента Wildberries.
Имена и сигнатуры двадцати инструментов версии 1.0.0 не менялись: на них завязаны
конфиги MCP-клиентов, так что только добавления.

### Добавлено

**Новые инструменты Wildberries**
- `wb_questions(imt_id, limit, skip, answered_only)` — вопросы покупателей и ответы
  продавца. Отзывы рассказывают, каково владеть товаром; вопросы уточняют, что это
  за товар. Ответ продавца часто единственное публичное утверждение о том, чего нет
  в описании. Эндпоинт проверен живьём на шести товарах до того, как была написана
  первая строка кода: у него три ловушки, каждая из которых выглядит как пустой
  результат, а не как ошибка. Подробности в `docs/ANTI_BOT.md`.
- `wb_category_products(shard, query, page, sort, dest)` — товары категории по
  `shard` и `query`, которые отдаёт `wb_categories`. Раньше эти селекторы было
  некуда применить. Формат элементов совпадает с `wb_search`, поэтому обход
  категорий и текстовый поиск сравнимы напрямую.

**Регион Детского мира на каждый вызов**
- У всех четырёх инструментов появился параметр `region`, он перекрывает
  `DETMIR_REGION`. До этого сменить город можно было только перезапуском сервера,
  что посреди диалога с агентом невозможно.

**Кэш и прокси у Wildberries и Ozon**
- `WB_CACHE_TTL`, `WB_PROXY`, `OZON_CACHE_TTL`, `OZON_PROXY`. README и SECURITY
  обещали `*_PROXY` у всех коннекторов, а на деле он был у двух из четырёх.
  Кэшируются только удачные ответы: запомнить сбой значило бы растянуть секундную
  помеху на весь TTL, а для Ozon кэш блокировки неотличим от настоящей.

**Запуск и развёртывание**
- HTTP-транспорт как опция (`MCP_TRANSPORT=http`). По умолчанию по-прежнему stdio,
  так что существующие конфиги клиентов работают без правок.
- Docker-образ и `compose`. Ограничения второго уровня Ozon в контейнере описаны
  честно, а не замазаны: `docs/DEPLOYMENT.md`.
- `server.json` — манифест для реестра MCP-серверов.

**Инфраструктура**
- Измерение покрытия тестами в CI с порогом 70% (фактическое покрытие ветвей —
  74%, порог взят с запасом, чтобы не ломать сборку из-за постороннего шума).
- `check_untyped_defs` включён для `mcp_core`.
- Dependabot для `uv` и GitHub Actions.
- Релизный workflow: по тегу `v*` собираются wheels и sdist всех шести пакетов и
  прикладываются к релизу. Публикации в PyPI нет — имена пакетов ещё не решены.
- Шаблоны issue и PR, `CODE_OF_CONDUCT.md`, бейджи в README.

### Исправлено

- **Карточка Детского мира игнорировала регион.** Она не отправляла фильтр региона
  вообще, но подписывала ответ значением `DETMIR_REGION`. Из-за этого
  `store_count` всегда был 0, а ответ выглядел достоверным. Регион работает только
  через `filter=withregion:`; форма `?withregion=` принимается и молча
  игнорируется. Один и тот же товар: 152 магазина в Москве, 37 в Петербурге, 2 в
  Хабаровске.
- **Адаптер Ozon в сравнении цен был нерабочим.** Он читал поля `price_rub`,
  `reviews_count`, `feedbacks`, `name`, `id`, `brand` — ни одного из них нет в
  `OzonSearchItemOut`. Все молча превращались в `None`. Хуже: те поля, что он
  всё-таки находил, — это текст для показа (`1 234 ₽`, `4,8`), а `price_rub` в
  модели `float | None`, так что pydantic ронял валидацию и весь источник целиком.
- **Дубли в выдаче Яндекса.** Один товар может занимать несколько сниппетов на
  странице. Дедупликация идёт до применения `limit`, иначе дубль съедал часть
  запрошенного объёма без всяких пояснений.

### Изменено

- `MetaOut` и модели selfcheck переехали в `mcp_core.models`. Не одним плоским
  классом: у Яндекса есть поле `extraction`, у Детского мира — `cached`, и слить их
  значило бы удалить оба и сломать два контракта. Сериализованный JSON всех 37
  моделей побайтово совпадает с 1.0.0.
- Логика запросов Wildberries поднята в ядро как `get_text_budgeted`: общий
  дедлайн на всю операцию, ошибки возвращаются классифицированной строкой, а не
  бросаются, вежливая пауза соблюдается и перед повтором. Обратное направление
  (перевести WB на более слабый общий хелпер) означало бы регресс.
- Отображение товара WB в карточку было скопировано в трёх местах — теперь одно
  `_card_item_dict`. Правило `in_stock` живёт в одном месте: остаток без цены —
  это непродаваемая позиция, и назвать её доступной значило бы вывести мёртвый
  товар в самые дешёвые.
- Тесты `mcp_core.process` переехали из набора Ozon в `mcp-core`.
- Тестов стало 406 вместо 221.

### Не сделано намеренно

- **`ozon_seller`.** Реквизиты продавца Ozon — прямой аналог `wb_seller`, и спайк
  был. Путь верный, id продавца уже приходит в `ozon_card` как `seller.link`, но с
  датацентрового IP каждый запрос заканчивается 403 от анти-бота. Контрольная
  проверка показательнее самих попыток: уже работающий путь `/product/{id}/` падает
  точно так же — блокируют IP, а не адрес. Значит, эндпоинт почти наверняка живой,
  а вот **пути к полям никто не видел**. Писать парсер под неувиденную структуру —
  это придумать имена полей и отдать то, что случайно совпадёт. Инструмент,
  возвращающий правдоподобное название чужого юрлица, хуже отсутствующего:
  проверяют продавца ровно для того, чтобы отличить официальный магазин от
  похожего перекупщика. Шаблон URL и порядок проверки — в
  `docs/RELEASE_CHECKLIST.md`.

---

## [1.1.0] — 2026-07-26 (English)

Technical debt, functional gaps, and two new Wildberries tools. The 20 tool names
and signatures from 1.0.0 are untouched: MCP client configs depend on them, so this
release only adds.

### Added

- `wb_questions(imt_id, limit, skip, answered_only)` — buyer questions with seller
  answers. Verified live across six products before any code was written; the
  endpoint has three failure modes that each look like an empty result rather than
  an error (see `docs/ANTI_BOT.md`).
- `wb_category_products(shard, query, page, sort, dest)` — the products behind the
  `shard`/`query` selectors `wb_categories` already returned and nothing consumed.
- Per-call `region` on all four Detsky Mir tools, overriding `DETMIR_REGION`.
- `WB_CACHE_TTL`, `WB_PROXY`, `OZON_CACHE_TTL`, `OZON_PROXY` — the docs promised
  `*_PROXY` everywhere while only two connectors had it. Only successful reads are
  cached.
- Optional HTTP transport (`MCP_TRANSPORT=http`); stdio remains the default, so
  existing client configs keep working.
- Docker image and compose, with the Ozon tier-2 limitations documented rather than
  glossed over: `docs/DEPLOYMENT.md`.
- `server.json` registry manifest.
- CI coverage gate at 70% (measured branch coverage is 74%), `check_untyped_defs`
  for `mcp_core`, Dependabot, a tag-triggered release workflow that builds wheels
  and sdists for all six packages, issue/PR templates, `CODE_OF_CONDUCT.md`, README
  badges.

### Fixed

- **`detmir_card` ignored the region entirely** — it sent no region filter but
  labelled the response with `DETMIR_REGION`, so `store_count` was always 0 while
  the answer looked authoritative. Only `filter=withregion:` works; `?withregion=`
  is accepted and silently ignored.
- **The compare connector's Ozon adapter could not work.** It read six field names
  `OzonSearchItemOut` does not declare, and the fields it did hit are display text
  (`1 234 ₽`, `4,8`) where `price_rub` is `float | None`, so pydantic failed
  validation and killed the whole source.
- **Duplicate products in Yandex search results**, deduped by `product_id` before
  the limit is applied so a repeat cannot eat the caller's page budget.

### Changed

- `MetaOut` and the selfcheck envelopes moved into `mcp_core.models` as base
  classes, not one flat class: Yandex adds `extraction`, Detsky Mir adds `cached`,
  and flattening would have deleted both. Serialized JSON for all 37 response
  models is byte-identical to 1.0.0.
- Wildberries' request logic was promoted into the core as `get_text_budgeted`
  (whole-operation deadline, classified error strings instead of exceptions, polite
  gate re-entered before each retry) rather than porting WB down to the weaker
  shared helper.
- The WB product-to-card mapping, previously copy-pasted three times, is one
  `_card_item_dict`.
- `mcp_core.process` tests moved out of the Ozon suite into `mcp-core`.
- 406 tests, up from 221.

### Deliberately not shipped

- **`ozon_seller`.** The path is right and the seller id already arrives via
  `ozon_card`'s `seller.link`, but every request from a datacenter IP ends in an
  anti-bot 403 — and the repo's already-working `/product/{id}/` path fails
  identically, which is what proves the IP is gated rather than the URL wrong. The
  endpoint is almost certainly live; the field paths are what nobody has seen.
  A seller tool returning a plausible name for the wrong legal entity is worse than
  no tool, since the only reason to look a seller up is telling an official store
  from a lookalike. Template and verification steps: `docs/RELEASE_CHECKLIST.md`.

---

## [1.0.0] — 2026-07-26

First public release. The project grew from two connectors into a uv workspace of
five MCP servers over a shared runtime.

### Added

**New marketplaces**
- **Yandex Market** connector (`yandex_search`, `yandex_card`, `yandex_selfcheck`).
  Reads the server-rendered widget state, since Yandex exposes no usable JSON API.
  Reports the everyday price and the Plus-subscriber price separately, plus the
  per-star rating distribution and server-rendered reviews.
- **Detsky Mir** connector (`detmir_card`, `detmir_category`, `detmir_categories`,
  `detmir_selfcheck`) over its anonymous public JSON API, including offline store
  availability.

**Cross-marketplace comparison**
- New `compare-connector` with `compare_prices` and `compare_sources`. Queries
  every installed marketplace concurrently, ranks offers by everyday price, and
  reports a per-source outcome so a partial result is never mistaken for a
  complete one. Subscription-only prices are excluded from ranking.

**New Wildberries tools**
- `wb_seller(supplier_id)` — registered legal entity, INN, KPP, OGRN, legal
  address and trademark behind a seller id.
- `wb_categories(root, max_depth)` — catalog tree with WB's own shard/query
  selectors, bounded so a response stays a usable size.

**Shared runtime (`mcp-core`)**
- `transport.http_tier` — polite rate limiting, capped bodies, and retries scoped
  to transport faults and gateway statuses (429 deliberately excluded).
- `transport.chrome_cdp` — the authenticated tier, generalised out of the Ozon
  connector and now cross-platform.
- `process` — cross-platform worker spawn/reap with an allowlisted child
  environment.
- `cache` — in-process TTL cache with concurrent-miss collapsing.
- Proxy support across connectors via `*_PROXY` or the standard proxy variables.

**Project infrastructure**
- uv workspace monorepo; each connector is an installable package with a console
  script (`wb-mcp`, `ozon-mcp`, `yandex-mcp`, `detmir-mcp`, `compare-mcp`).
- GitHub Actions CI: ruff, mypy and the test suite on Ubuntu/Windows/macOS against
  Python 3.12 and 3.13.
- `scripts/check_no_print.py` — fails the build on any stdout write in server
  code, since a stray `print()` corrupts the JSON-RPC stream.
- `scripts/start_chrome_cdp.sh` — Linux/macOS counterpart to the PowerShell
  launcher.
- Agent skill documentation for every connector.
- Test suite grown from 66 to 221 offline tests, including real trimmed fixtures
  for the Yandex SSR parser.

### Fixed

- **`wb_search` returned pages where nothing had a price.** It resolved ids through
  `search-goods.wildberries.ru`, which serves a stale index: for one live query
  every id it returned was a delisted SKU with `price: null`, while the v9 search
  endpoint returned 100 in-stock products with real prices. `wb_search` now reads
  `search.wb.ru` v9 directly — one request instead of two, 100 results per page
  instead of 30 — and keeps the old path as a flagged fallback.
- **Ozon's process teardown was Windows-only.** `taskkill` paths, creation flags
  and the child environment allowlist assumed Windows; the POSIX branch was
  untested and its test asserted a Windows path, so it could not pass on Linux or
  macOS. Now cross-platform, with both branches unit-tested on every OS.
- **`taskkill` could be redirected through the environment.** The system directory
  was resolved via `SystemRoot`/`WINDIR`, which any process able to set the
  environment could point elsewhere. Now resolved via `GetSystemDirectoryW` or a
  literal fallback.
- **Windows paths were built with forward slashes off-Windows.** Switched to
  `PureWindowsPath` so the Windows branch composes correct paths when exercised
  from a POSIX host.
- **POSIX-only calls broke type checking and tests on Windows.** `terminate_process_tree`
  referenced `os.killpg`, `os.getpgid` and `signal.SIGKILL` literally. Those names do
  not exist on Windows, so mypy failed there while passing on Linux, and the POSIX
  tests could not monkeypatch attributes the module lacked. The calls now go through
  `kill_process_group()`, which resolves them via `getattr` and raises cleanly where
  process groups are unavailable; the tests patch that function instead. CI now runs
  `mypy --platform win32` and `--platform darwin`, which is what would have caught this
  from a Linux host in the first place.
- **PEP 561 markers were missing.** Without `py.typed`, mypy treated every
  cross-package import as `Any` and reported phantom missing-return errors. All
  packages now ship the marker; the tree is mypy-clean.
- **Error bodies were truncated unconditionally.** Detsky Mir's search route
  answers 404 while rendering a full page, so an error-body cap discarded real
  content. The cap is now opt-out per call.
- **Gateway errors were not retried.** Detsky Mir emits sporadic 502s and Yandex
  occasionally answers 302 with an empty body; both are now retried, while 429 is
  still passed straight through.

### Removed

- **`detmir_search` was implemented, tested against live data, and deleted.** Its
  results were plausible-looking nonsense: a query for "лего" returned nappies and
  collagen supplements, because Detsky Mir's API ignores text filters and its
  website search route renders a promo carousel behind a 404. No search tool is
  better than a confidently wrong one; discovery goes through `detmir_categories`.

### Not included, and why

Marketplaces evaluated during this release and deliberately left out:

- **Megamarket** — its mobile API works, but ServicePipe blocks datacenter traffic
  outright and requires cookies from a browser that has passed a JS challenge.
- **Lamoda** — its GraphQL endpoint returns prices for a *known* SKU, but catalog
  and search sit behind an anti-bot redirect loop, so there is no way to discover
  products in the first place.
- **DNS** — Qrator serves a JavaScript proof-of-work challenge on all dynamic
  pages; only `robots.txt` and `sitemap.xml` are reachable anonymously.
- **Citilink** — Qrator rate-blocks the entire domain, and the data transport is
  gRPC-web requiring a reversed protobuf schema.

Details in [docs/ANTI_BOT.md](docs/ANTI_BOT.md).

[1.0.0]: https://github.com/Vladimir-Human/ru-marketplace-mcp/releases/tag/v1.0.0
