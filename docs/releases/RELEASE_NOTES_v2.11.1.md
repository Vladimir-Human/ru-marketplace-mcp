# v2.11.1 — 2026-11-18

Патч-релиз. Карточка Ozon для товара из-за рубежа теперь показывает доплату за таможенный платёж отдельным полем, а поиск Wildberries и AliExpress и полное сравнение цен больше не обрываются, пока страница ещё грузится или браузер ждёт свободный слот. Переход на чужой домен теперь отклоняется до того, как браузер его запросит.

## Исправления

- **Ozon: доплата за таможенный платёж** (#337). У товара из-за рубежа карточка показывает под ценой доплату за таможенный платёж, которая в цену не входит. Теперь `ozon_card` возвращает её в поле `customs_duty_rub` (это оценка Ozon) и добавляет предупреждение `customs_duty` о том, что доплата в цену не входит. `compare_prices` больше не ставит такое предложение в один ряд с ценами, в которые пошлина уже включена, и называет примерную сумму с доплатой.
- **Wildberries: строки сравнения без цен** (#338). Строки в сравнении цен больше не приходят без цен, когда страница поиска ещё загружалась: поиск ждёт, пока карточки покажут цены. Если карточки 6 секунд остаются без цен (например, всё распродано), страница читается как есть. Страница, которая вернулась вовсе без карточек, открывается ещё один раз.
- **AliExpress: поиск на медленной странице** (#338). Поиск больше не падает с ошибкой «zero product tiles», когда страница грузилась медленно: поиск ждёт появления ссылок на товары до 8 секунд и открывает страницу ещё один раз. Если товаров так и нет, ошибка говорит, сколько ссылок и сколько текста было на странице.
- **Полное сравнение цен: ожидание браузера** (#339). Ozon, Avito и другие площадки, читающие через Chrome, больше не падают с «CDP timeout» во время полного сравнения цен, пока ждут свободный слот для навигации: секунды ожидания разрешения возвращаются в таймаут попытки (всего до двух таймаутов), поэтому попытка успевает прочитать страницу, а не истекает в очереди.

## Безопасность

- **Переходы между доменами** (#340). Если страница магазина отвечает перенаправлением на чужой домен, браузер больше не запрашивает этот сайт — такой запрос отклоняется до отправки, и вызов завершается ошибкой без данных страницы. Раньше чужой сайт успевал получить запрос из профиля браузера, а вызов отклонялся уже после загрузки. Так же проверяется каждое звено цепочки перенаправлений.

## Известные ограничения

- В день выпуска на машине владельца сверены с живыми страницами семь площадок: Wildberries (40 из 40 товаров с ценами), Ozon (8 из 8), Яндекс Маркет (8 из 8), Детский мир (20 из 20), Авито (50 из 50), DNS (4 из 4) и AliExpress (36 из 36); у каждой открыта и первая карточка. HTTP API поиска Wildberries в этот день отказывал (HTTP 403), поэтому `wb_search` читал страницы через Chrome.
- Lamoda и Ситилинк в день выпуска отказывали браузеру на машине владельца (`transport_down`), так же и на версии 2.11.0.
- Мегамаркет в день выпуска отвечал браузеру на машине владельца отказом `rate_limited` (HTTP 403), и после паузы, и на версии 2.11.0.
- Циан просит пройти проверку «вы не робот» (`challenge_required` с командой `marketplace-mcp unblock cian`), а Taobao на популярных запросах без входа открывает страницу входа; в этом выпуске обе площадки с живыми страницами не сверялись.

## Как обновиться

- В один клик для Claude Desktop: скачайте `ru-marketplace-mcp-compare-2.11.1.mcpb` со страницы релиза и откройте его в Claude Desktop.
- Через `uvx`: `uvx ru-marketplace-mcp@2.11.1 install <client> --write --force` (доступны `claude`, `claude-code`, `cursor`, `vscode`, `windsurf`, `antigravity`, `gemini`, `codex`, `dsh`).
- Из исходников: `git pull --ff-only`, затем `uv sync --all-packages --frozen` и перезапуск серверов.

## Состав

15 wheel и 15 sdist рабочих пакетов и бандл `ru-marketplace-mcp-compare-2.11.1.mcpb` на GitHub Release, единый пакет `ru-marketplace-mcp` на PyPI, образ `ghcr.io/vladimir-human/ru-marketplace-mcp:2.11.1`, запись в MCP Registry (`pypi` и `oci`) и пакет для DSH в npm.

---

## English

Patch release. An Ozon card for a cross-border product now carries the customs payment in its own field, and Wildberries and AliExpress searches and a full price comparison no longer give up while the page is still loading or the browser waits for a free slot. A redirect to a foreign domain is now refused before the browser requests it.

### Fixes

- **Ozon: customs payment on the card** (#337). A cross-border product's card shows a customs payment under the price that the price leaves out. `ozon_card` now returns it as `customs_duty_rub` (Ozon's estimate) and adds a `customs_duty` warning that it is not in the price. `compare_prices` no longer ranks such an offer against prices that already include duty and names the estimated total.
- **Wildberries: comparison rows without prices** (#338). Rows in a price comparison no longer come back without prices when the page was still loading: the search waits until the tiles show prices. If the tiles stay without prices for 6 seconds (for example, everything is sold out), the page is read as it is, and a page that came back without any tiles is opened once more.
- **AliExpress: search on a slow page** (#338). Search no longer fails with "zero product tiles" when the page was slow: it waits for the item links for up to 8 seconds and opens the page once more. When the page still holds no tiles, the error says how many links and how much text the page had.
- **Full price comparison: waiting for the browser** (#339). Ozon, Avito and the other browser sources no longer fail with "CDP timeout" during a full price comparison while they wait for a free Chrome slot: the seconds spent waiting for a navigation permit are added back to the attempt's timeout (at most twice its timeout), so the attempt reads the page instead of expiring in the queue.

### Security

- **Cross-domain redirects** (#340). When a store page answers with a redirect to a foreign domain, the browser no longer requests that site — the request is refused before it is sent, and the call ends with an error and no page data. The foreign site used to receive a request from the browser profile, and the call was refused only after the page loaded. Every hop of a redirect chain is checked the same way.

### Known limits

- On release day seven sources were checked against live pages on the owner's machine: Wildberries (40 of 40 items priced), Ozon (8 of 8), Yandex Market (8 of 8), Detsky Mir (20 of 20), Avito (50 of 50), DNS (4 of 4) and AliExpress (36 of 36), each with its first card. The Wildberries search API refused that day (HTTP 403), so `wb_search` read the pages through Chrome.
- Lamoda and Citilink refused the browser on the owner's machine on release day (`transport_down`), on 2.11.0 as well.
- Megamarket answered `rate_limited` (HTTP 403 refusal) on the owner's machine on release day, after its pause too, and on 2.11.0 as well.
- Cian asks for its "not a robot" check (`challenge_required` with `marketplace-mcp unblock cian`), and Taobao opens its login page for popular queries without an account; neither was checked against live pages for this release.

### Upgrading

- One-click for Claude Desktop: download `ru-marketplace-mcp-compare-2.11.1.mcpb` from the GitHub Release page and open it in Claude Desktop.
- `uvx`: `uvx ru-marketplace-mcp@2.11.1 install <client> --write --force` (`claude`, `claude-code`, `cursor`, `vscode`, `windsurf`, `antigravity`, `gemini`, `codex`, `dsh`).
- Checkout: `git pull --ff-only`, `uv sync --all-packages --frozen`, and restart the servers.

### Contents

15 workspace wheels and 15 sdists plus `ru-marketplace-mcp-compare-2.11.1.mcpb` on GitHub Release, the bundled `ru-marketplace-mcp` package on PyPI, `ghcr.io/vladimir-human/ru-marketplace-mcp:2.11.1`, the MCP Registry entry (`pypi` and `oci`), and the DSH bundle on npm.
