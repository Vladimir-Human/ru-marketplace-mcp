# v2.10.0 — 2026-11-17

Сравнение цен называет ту же модель, а не похожий товар. Цена Ozon в сравнении берётся из карточки, а не с плитки, где стоит цена по Ozon Карте. Wildberries снова читает новые карточки. Каждая ошибка подсказывает следующий шаг. Из ответов убраны имена покупателей. Ссылки принимаются и открываются только на сайтах самих магазинов. Инструменты MPStats объявлены устаревшими.

## Исправления

- **Сравнение цен: та же модель** (#310). `cheapest_comparable` больше не называет другой товар: аккумулятор вместо пылесоса Dyson, iPhone 14 вместо iPhone 16, Redmi Note 14S вместо Note 14. Предложение не становится кандидатом в трёх случаях. Первый: в названии (или в поле бренда) нет латинского слова или номера модели из запроса. Второй: это товар «для», «под» или «совместим с» этой моделью, или копия («аналог», «реплика»). Третий: сразу после модели стоит Pro, Max, Plus, Ultra, Mini, Lite, FE, Edge или OLED, которых в запросе нет. Такое предложение остаётся в `offers` и может остаться `cheapest`. 1 ТБ теперь совпадает и с «1024 ГБ», и с «1000 ГБ», как продают SSD.
- **Сравнение цен: Авито** (#311). Строка Авито не становится `cheapest_comparable`, если в запросе не просят б/у: в выдаче Авито не видно, новый товар или б/у, это видно только в карточке. Строка остаётся в `offers` и может остаться `cheapest`.
- **Сравнение цен: несколько объёмов в названии** (#316). Предложение, в названии которого перечислено несколько объёмов («128GB/256GB/512GB»), не становится `cheapest_comparable`: его цена относится к одному из вариантов, а к какому, не сказано. Пара «оперативная память / накопитель» («8/256GB») списком не считается.
- **Оплата частями** (#312). Сумма рядом со словами «Долями», «Сплит» или «частями» больше не читается как цена.
- **Ozon в сравнении** (#290, #302, #305). Плитка поиска Ozon показывает цену по Ozon Карте, даже без значка карты. Поэтому каждая плитка приходит как условная цена (`price_rub` пуст, сумма в `price_native`, `price_kind` = `card`). Самое дешёвое подходящее предложение ранжируется по обычной цене из своей карточки, а цена по карте названа в заметке. Если карточку прочитать не удалось, Ozon не ранжируется, и предупреждение `ozon_card_unread` называет причину. Сравнение с Ozon делает на один запрос больше. У `ozon_search` появилось поле `price_kind`. Если на плитке нет текущей цены, зачёркнутая старая цена больше не выдаётся за цену.
- **Wildberries** (#296, #305). `wb_card` и другие инструменты снова читают новые карточки: раньше таблица корзин обрывалась, и новые карточки отвечали `not_found`. `wb_categories` отдаёт полную ссылку, у `wb_card` появилось поле `url`, `wb_category_products` принимает все ключи фильтра из меню WB. Если WB не прислал цену до скидки, `price_original_rub` пуст, а не повторяет текущую цену.
- **Яндекс Маркет** (#300, #306). Если у строки со значком «Скидка N%» пропала цена со скидкой, зачёркнутая цена больше не выдаётся за обычную: `price_rub` пуст, и предупреждение `discount_price_missing` называет число таких строк. Условная цена (`price_with_plus`) описана как цена при оплате Яндекс Пэй, без обещания про подписку Плюс. Описания говорят, что цена — та, что видит анонимный посетитель в регионе, который Яндекс назначил запросу.
- **Lamoda** (#307). Если первоначальная зачёркнутая цена пропала или не читается, на её место больше не встаёт промежуточная: `old_price_rub` пуст.
- **Avito** (#301). `seller_id` из `avito_card` снова открывает того же продавца в `avito_seller`.
- **Циан** (#266, #308). Если API поиска просит капчу или вкладка поиска попала на проверку «вы не робот», поиск сразу отвечает `challenge_required` с командой `marketplace-mcp unblock cian`, а не зависает до сетевой ошибки. `CIAN_TIMEOUT` больше 30 секунд теперь соблюдается.
- **Размер ответа сравнения** (#292). Ответ `compare_prices` длиннее 40 000 символов теряет целые предложения с конца списка (сначала без цены, потом самые дорогие). `total_offers` хранит полное число, `cheapest` и `cheapest_comparable` не меняются, а заметка `info:offers_omitted` говорит, сколько предложений не вошло.
- **Предупреждения сравнения** (#286). Предупреждение `conditional_price: …` снова приходит в `warnings` с этим префиксом, а не внутри `source_warning:…`.
- **Ошибки** (#287). `next_step` у `bad_request` называет только тот параметр, который действительно имеется в виду. Блокировка, которую снимает человек, больше не получает выдуманную паузу.
- **Ссылки** (#288). Снова принимаются ссылки Циана без схемы, ссылки на поддомены Мегамаркета и Lamoda и переходы на www.aliexpress.ru.

## Новое

- **Ошибки с подсказкой** (#271). Каждая ошибка несёт поле `tool` и поле `next_step`: одну фразу о том, что делать дальше. Это может быть какой вызов повторить и через сколько секунд, какой инструмент найдёт товар или какую настройку должен задать пользователь. У `rate_limited`, `timeout` и `transport_down` всегда есть `retry_after_s`. Коды ошибок и `retryable` не изменились.
- **Фото в сравнении** (#279). У предложений `compare_prices` появилось поле `image_url`, если ответ поиска уже содержит фото (Яндекс Маркет, Ozon, Мегамаркет, DNS, AliExpress). Дополнительных запросов это не стоит. То же поле добавлено в поиск Ozon, Мегамаркета, DNS и AliExpress.
- **Когда и где** (#282). У каждого магазина в `source_outcomes` появились `observed_at` (время ответа магазина, UTC) и `region` (регион, который называет сам ответ; пока только Avito).

## Изменения

- **Ссылки только на сайты магазинов** (#276). Ссылка в аргументе инструмента принимается только с адресов самого магазина. Чужой адрес, локальный адрес или `file:` получают `bad_request` до любого запроса. Если сайт магазина перенаправит браузер сборщика на чужой адрес, вызов ответит ошибкой и ничего с той страницы не вернёт.
- **Условная цена рядом с обычной** (#275). Если рядом с обычной ценой есть более низкая по условию (Кошелёк WB, Яндекс Пэй), сравнение ранжирует по обычной, а более низкую называет заметкой `info:conditional_price_available` вместо предупреждения.
- **Проверка победителя** (#309). Справка по `compare_prices` просит перед тем, как назвать цену победителя, вызвать `compare_verify_offer` для этой строки.
- **Журнал MCP** (#269). Инструменты больше не шлют клиенту сообщения журнала MCP (`notifications/message`): протокол объявил этот канал устаревшим. Те же записи сервер пишет в stderr. FastMCP обновлён до 4.2.0.
- **Имена продавцов** (#274). Имя продавца на Авито приходит только для компании или магазина Авито, а у частного продавца оно `null`. Имя публикующего на Циане приходит только для агентства или застройщика.

## Устарело

- **MPStats** (#277). У MPStats есть свой MCP-сервер: `https://mcp.mpstats.io/mcp?token=<ваш токен MPStats>`, подключайте сначала его. Инструменты `mpstats_item` и `mpstats_warehouses` уйдут в v2.11.0, а до тех пор каждый их ответ несёт заметку `info:deprecated_use_official_mpstats_mcp`.

## Удалено

- **Имена покупателей** (#274). Из ответов убраны поле `user` в `wb_reviews` и `wb_questions` и поле `author` в `ozon_reviews` и в отзывах `yandex_card`. Сравнению товаров они не нужны.

## Безопасность

- Образы Docker собираются с dnspython 2.9.2 и opentelemetry-api 1.45.2: в прежних версиях специально составленный ввод мог занять память и процессор (#280).

## Известные ограничения

- Мегамаркет, Ситилинк и Lamoda в день выпуска отказывали браузеру сборщика: Мегамаркет и Ситилинк отвечали `rate_limited`, Lamoda — `transport_down`. Повторный вызов после паузы, которую назвала сама площадка, получил тот же ответ, и версия 2.9.4 в тот же день отвечала так же. Сколько ждать и что делать, написано в самой ошибке (`retry_after_s`, `next_step`).
- Циан просит пройти проверку «вы не робот»: поиск отвечает `challenge_required` с командой `marketplace-mcp unblock cian`, проверку проходит человек.
- API Wildberries в день выпуска не отвечал сборщику, и `wb_search` и `wb_card` читали сайт через его Chrome; все 40 товаров пришли с ценами. Без Chrome в таком случае они ответят ошибкой.
- Предложение, в названии которого названо несколько моделей одной линейки («Dyson V11/V15»), ещё может стать `cheapest_comparable`. Исправление войдёт в следующий выпуск.

## Как обновиться

- Новых обязательных настроек нет.
- Если вы читали `user` или `author` в отзывах и вопросах, эти поля больше не приходят.
- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.
- Через `uvx`: `uvx ru-marketplace-mcp@2.10.0 install <client> --write --force`.

## Состав

16 wheel и 16 sdist на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.10.0` и запись в MCP Registry.

---

## English

Price comparison names the same model, not a look-alike. The Ozon price in a comparison comes from the card, not from the tile, which shows the Ozon Card price. Wildberries reads new cards again. Every error says what to do next. Shopper names are gone from the results. Links are accepted and opened only on the stores' own sites. The MPStats tools are deprecated.

### Fixes

- **Comparison: the same model** (#310). `cheapest_comparable` no longer names another product: a battery for a Dyson vacuum, an iPhone 14 for an iPhone 16, a Redmi Note 14S for a Note 14. An offer is no candidate in three cases. First, its title (or brand field) lacks a Latin model word or number from the query. Second, it is goods "для", "под" or "compatible with" that model, or a copy ("аналог", "реплика"). Third, Pro, Max, Plus, Ultra, Mini, Lite, FE, Edge or OLED follows the model and the query does not name it. The offer stays in `offers` and may stay `cheapest`. 1 TB now matches both "1024 ГБ" and "1000 ГБ", as SSDs are sold.
- **Comparison: Avito** (#311). An Avito row is not `cheapest_comparable` unless the query asks for a used item: Avito search rows never say new or used, only the card does. The row stays in `offers` and may remain `cheapest`.
- **Comparison: several sizes in a title** (#316). An offer whose title lists several sizes ("128GB/256GB/512GB") is not `cheapest_comparable`: its price belongs to one variant it does not name. A RAM and storage pair ("8/256GB") is not a list.
- **Split payments** (#312). An amount next to «Долями», «Сплит» or «частями» is no longer read as the price.
- **Ozon in comparisons** (#290, #302, #305). An Ozon search tile shows the Ozon Card price, even without the card badge. So every tile arrives as a conditional price (`price_rub` null, the amount in `price_native`, `price_kind` `card`). The cheapest plausible offer is ranked by the everyday price from its card, and the card price is named in a note. When the card cannot be read, Ozon is not ranked and an `ozon_card_unread` warning says why. A comparison that includes Ozon makes one more request. `ozon_search` items gain `price_kind`. A tile without a current price no longer reports the struck original price as the price.
- **Wildberries** (#296, #305). `wb_card` and the other tools read new cards again: the basket table used to end early, so new cards answered `not_found`. `wb_categories` returns a full link, `wb_card` gains a `url` field, and `wb_category_products` accepts every filter key WB's menu hands out. When WB sends no pre-discount price, `price_original_rub` is null instead of a copy of the current price.
- **Yandex Market** (#300, #306). When a row with the «Скидка N%» badge lost its discounted price, the struck price is no longer reported as the everyday price: `price_rub` is null and a `discount_price_missing` warning counts such rows. The conditional price (`price_with_plus`) is described as the price when paying with Yandex Pay, with no claim about a Plus subscription. Descriptions say the price is what an anonymous visitor sees in the region Yandex assigns to the request.
- **Lamoda** (#307). When the original struck price is missing or unreadable, the intermediate one no longer takes its place: `old_price_rub` is null.
- **Avito** (#301). A `seller_id` from `avito_card` opens the same seller in `avito_seller` again.
- **Cian** (#266, #308). When the search API asks for a captcha or the search tab lands on the "are you a robot" check, search answers `challenge_required` at once with `marketplace-mcp unblock cian` instead of hanging until a network error. A `CIAN_TIMEOUT` above 30 seconds is now honoured.
- **Comparison answer size** (#292). A `compare_prices` answer over 40,000 characters leaves whole offers out from the end of the list (unpriced first, then the dearest). `total_offers` keeps the full count, `cheapest` and `cheapest_comparable` are unchanged, and an `info:offers_omitted` note says how many were left out.
- **Comparison warnings** (#286). The `conditional_price: …` warning again arrives in `warnings` with that prefix, not inside `source_warning:…`.
- **Errors** (#287). A `bad_request` `next_step` names only the parameter actually meant. A block a person must clear no longer gets an invented wait.
- **Links** (#288). Schemeless Cian links, Megamarket and Lamoda subdomain links and www.aliexpress.ru redirects are accepted again.

### New

- **Errors that say what next** (#271). Every error carries `tool` and `next_step`: one sentence on what to do next. That can be which call to repeat and after how many seconds, which tool finds the item, or which setting the user must set. `rate_limited`, `timeout` and `transport_down` always carry `retry_after_s`. Error codes and `retryable` are unchanged.
- **Photos in comparisons** (#279). `compare_prices` offers gain `image_url` when the search answer already holds a photo (Yandex Market, Ozon, Megamarket, DNS, AliExpress). It costs no extra request. The same field is added to the Ozon, Megamarket, DNS and AliExpress searches.
- **When and where** (#282). Each store in `source_outcomes` gains `observed_at` (when the store answered, UTC) and `region` (the region the answer itself names; today only Avito).

### Changes

- **Links only on the stores' sites** (#276). A link in a tool argument is accepted only on the store's own hosts. A foreign host, a local address or `file:` gets `bad_request` before any request. If a store's site redirects the scraping browser to a foreign host, the call answers with an error and returns nothing from that page.
- **A conditional price next to the everyday one** (#275). When a lower conditional price (WB Wallet, Yandex Pay) sits next to the everyday price, the comparison ranks by the everyday price and names the lower one in an `info:conditional_price_available` note instead of a warning.
- **Check the winner** (#309). The `compare_prices` guide asks the agent to call `compare_verify_offer` on the winning row before quoting its price.
- **MCP log** (#269). Tools no longer send MCP log messages (`notifications/message`) to the client: the protocol deprecated that channel. The server writes the same records to stderr. FastMCP is updated to 4.2.0.
- **Seller names** (#274). The Avito seller name is returned only for a company or an Avito shop; a private seller's name is `null`. The Cian publisher name is returned only for an agency or a developer.

### Deprecated

- **MPStats** (#277). MPStats runs its own MCP server, `https://mcp.mpstats.io/mcp?token=<your MPStats token>`: connect that one first. `mpstats_item` and `mpstats_warehouses` go in v2.11.0; until then every result carries an `info:deprecated_use_official_mpstats_mcp` note.

### Removed

- **Shopper names** (#274). `user` in `wb_reviews` and `wb_questions` and `author` in `ozon_reviews` and `yandex_card` reviews are gone. Comparing goods does not need them.

### Security

- Docker images build with dnspython 2.9.2 and opentelemetry-api 1.45.2: the earlier versions let crafted input eat memory and CPU (#280).

### Known limits

- Megamarket, Citilink and Lamoda refused the scraping browser on release day: Megamarket and Citilink answered `rate_limited`, Lamoda `transport_down`. A second call after the pause the site itself named got the same answer, and 2.9.4 answered the same way that day. The error itself says how long to wait and what to do (`retry_after_s`, `next_step`).
- Cian asks for its "are you a robot" check: search answers `challenge_required` with `marketplace-mcp unblock cian`, and a person passes the check.
- The Wildberries API did not answer the scraper on release day, so `wb_search` and `wb_card` read the site through its Chrome; all 40 items came with prices. Without Chrome they answer with an error in that case.
- An offer whose title names several models of one family ("Dyson V11/V15") can still become `cheapest_comparable`. The fix ships in the next release.

### Upgrading

- No new required settings.
- If you read `user` or `author` in reviews and questions, those fields are gone.
- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.
- `uvx`: `uvx ru-marketplace-mcp@2.10.0 install <client> --write --force`.

### Contents

16 wheels and 16 sdists on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.10.0`, and the MCP Registry entry.
