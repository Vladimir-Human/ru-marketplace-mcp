# Changelog

Здесь записаны все заметные изменения. Формат — по
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), нумерация версий — по
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Русский текст первый, английский — ниже в каждом разделе. Аудитория проекта
русскоязычная, и переводить для неё собственные заметки о релизе странно.

## [1.2.1] — 2026-07-28

Патч по итогам стороннего ревью. Поведение инструментов не менялось; правки
касаются документации и одного пути к модулю в тестовом харнессе.

### Исправлено

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
