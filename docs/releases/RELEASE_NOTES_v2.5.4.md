# v2.5.4 — 2026-10-30

Патч: Ozon больше не принимает первый ответ своего антибота за блокировку, передача браузера снова соблюдает границу простоя, а зависимости обновлены до версий без известных уязвимостей. Схемы параметров и ответов не изменились.

## Изменения

- **Ozon и 403 с JS-проверкой.** На первый запрос Ozon отвечает кодом 403 и JS-проверкой, которая сама перезагружает страницу. Навигация считала такой 403 блокировкой, и `ozon_search` падал несколько раз в неделю без всякой капчи. Теперь у страницы есть до 10 с на перезагрузку, а `NavBlocked` поднимается, только если 403 остался и после неё. Спасибо @dont99code (#133).
- **Граница простоя у передачи браузера.** Пока часам цикла событий меньше ~60 с от загрузки системы, граница выключалась: `last_used` хранил `0.0` как «ещё не использовался», а настоящая отметка времени тоже бывает нулевой или отрицательной. Теперь для этого используется `None`. Ошибку поймал ночной CI на свежей виртуальной машине (#129).

## Безопасность

- Зависимости в lock-файлах обновлены дальше всех опубликованных уязвимостей. pip-audit по экспортированному lock: было 64 известные уязвимости в 10 пакетах, стало 0. npm audit по lock для разработки: было 2 high, стало 0. FastMCP остаётся на линии 3.x (3.4.11). Действия в workflow закреплены по SHA коммитов, базовые образы Docker — по digest. Спасибо @ilodezis (#132).
- Dependabot теперь следит и за базовыми образами Docker (#134). uv в сборочном образе — 0.13.6 (#135), setup-uv в CI — v10 (#136).

## Статус живой проверки

Одна машина, один адрес, 30 октября, Chrome оператора через CDP. Число живых запросов за прогон ограничено, чтобы не нагружать площадки с адреса оператора. На selfcheck всех источников и сверку глазами каждого этого лимита не хватило, поэтому часть источников в этот раз не проверена.

| Источник | selfcheck | Сверка глазами |
| --- | --- | --- |
| Ozon | поиск, карточка и отзывы с сортировкой — healthy; отзывы без сортировки — `transport_down`: страница открылась, но запрос из неё ответа не вернул | не выполнена: лимит запросов к Ozon исчерпан |
| Wildberries | HTTP API: отзывы, `search_goods` и `root_basket` — healthy; карточка и поиск v9 — 403, как и 13 октября. `WB_TRANSPORT=dom`: поиск «ноутбук» вернул 40 плиток | 4 плитки: цена, старая цена, рейтинг, число оценок и срок доставки совпали со страницей. Роль цены (Кошелёк или обычная) глазами не подтверждена. Профиль Chrome вошёл в аккаунт WB, так что это цены этой сессии |
| Яндекс Маркет | success: поиск (5 из 205, все с ценой), карточка (Samsung Galaxy A17, 19 231 ₽, 13 отзывов) | частично: в выдаче браузера нашёлся 1 товар из 5. Страница показывает 1 561 ₽ с картой Яндекс Пэй «вместо» обычной цены, инструмент отдал 1 609 ₽; обычную цену в тексте страницы прочитать не удалось |
| Детский мир | success: карточка, категория, список категорий | карточка 6673568: название и цена 699 ₽ совпали |
| Taobao | success; `taobao_search` «iphone 15» — 40 товаров | 3 товара: цена в юанях, магазин, продажи и регион совпали |
| Ситилинк | success; поиск «ноутбук» — 37 товаров | 3 плитки: цены совпали. У Huawei MateBook D 16 инструмент взял обычную цену 81 990 ₽, а не клубную 81 500 ₽. Наличия в ответе поиска нет |
| AliExpress | success: 37 плиток, все с ценой; карточка разобрана | не выполнена |
| Авито | `transport_down`: curl не прошёл, страница в Chrome не открылась; карточка и продавец не проверялись | — |
| DNS | `transport_down` | — |
| Циан, Lamoda, Мегамаркет | не запускались | — |
| MPStats | нет токена | — |

**Валюта Taobao.** `compare_prices` по запросу «lego» на Taobao и Яндекс Маркете: 5 предложений в юанях (от 55 ¥) не вошли в ранжирование, у них `price_rub: null`, в `warnings` есть `foreign_currency`. Самым дешёвым стало предложение Яндекс Маркета за 1 139 ₽, ответ `complete: true`.

Дрейфа (`drift_detected`) нет. По правилам `doctor` это код 2: часть источников не проверена.

Замечено при проверке, в этом релизе не исправлено:

- Taobao ищет «iphone+15» вместо «iphone 15»: пробел в запросе кодируется плюсом, и площадка понимает его буквально. Выдача при этом по делу.
- Из первой строки выдачи Taobao инструмент взял одну плитку из пяти. Экстрактор берёт только ссылки на `item.taobao.com`; возможно, пропущенные плитки ведут на Tmall или на рекламу — это не проверено.
- В `compare_prices` вместе с Ситилинком Taobao вернул ноль товаров: вкладки грузились одновременно, и сетка Taobao ещё показывала «加载中...». Сравнение честно пометило Taobao как не ответивший (`complete: false`), но назвало причину `parser_drift`, хотя дрейфа не было.

## Проверка поставки

- Офлайн: 2275 passed, 1 skipped, 5 live deselected; покрытие ветвей 84.90 %.
- Эксплуатационные тесты скриптов: 70 passed.
- Ruff, форматирование, mypy host/win32/darwin, версия 2.5.4 в 84 местах, защита stdout, счётчик тестов (2276 в 7 местах).
- 16/16 настоящих stdio MCP-сессий на v2.5.4.
- Lock-файл и `uv sync --frozen` проверены uv 0.13.6, той же версией, что в сборочном образе.

Поставка содержит 16 wheels и 16 sdists; на PyPI пакеты не публикуются.

## English

v2.5.4 is a patch. Ozon's first answer is often a 403 with a JS check that reloads the page by itself; navigation no longer calls that a block and gives the reload up to 10 s (thanks @dont99code, #133). The browser handoff's idle bound no longer switches off while the event-loop clock is young (#129). Security: the locked dependencies moved past every published advisory, from 64 known vulnerabilities in 10 packages to none, with workflow actions pinned to SHAs and Docker base images to digests (thanks @ilodezis, #132); Dependabot watches the base images (#134), the builder's uv is 0.13.6 (#135) and CI uses setup-uv 10 (#136).

Live, 30 October, one address: Yandex Market, Detsky Mir, Taobao, Citilink and AliExpress passed selfcheck. Ozon passed search, card and sorted reviews, while plain reviews returned nothing. Wildberries answers through `WB_TRANSPORT=dom`; its HTTP card and v9 search return 403. Avito and DNS did not answer. Cian, Lamoda and Megamarket were not checked: the run's live-request limit ran out. By eye: Wildberries (4 tiles), Taobao (3), Citilink (3) and Detsky Mir (1 card) matched the site; Yandex Market was a partial match; Ozon and AliExpress were not compared. A yuan offer stayed out of the rouble ranking (`foreign_currency`, `price_rub: null`). No drift. Offline gate: 2275 passed, coverage 84.90 %, 16/16 stdio sessions at v2.5.4.
