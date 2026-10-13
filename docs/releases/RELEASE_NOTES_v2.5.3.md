# v2.5.3 — 2026-10-13

Патч про роли цен Wildberries в режиме `WB_TRANSPORT=dom`. Схемы параметров и ответов не изменились.

## Изменения

- **Плитки поиска с ценой Кошелька без подписи.** Узкая плитка теряет подпись «с WB Кошельком», но сохраняет иконку Кошелька у цены. Такие плитки читались как обычная цена: 13 октября это было 43 плитки из 214. Пример: у товара 471317158 на плитке 4 341 ₽ — это цена с Кошельком, обычная цена на странице товара 4 430 ₽. Теперь иконка считается признаком цены Кошелька.
- **Зачёркнутая цена на странице товара.** WB зачёркивает старую цену стилем CSS, а не тегом `<del>`. Подсказка была пустой, и старой ценой считалось самое большое число на странице. Теперь экстрактор читает `line-through`. Если признака зачёркивания нет, несколько цен больше не угадываются: роли остаются `null`, ответ получает предупреждение. Подпись Кошелька по-прежнему помечает самую низкую цену.
- **Совпадение цены Кошелька и обычной цены.** Отдельной правки нет. Вживую WB такую пару не показывал, а код в этом случае не придумывает данные: обычная цена — `null`, предупреждение «regular price unknown».

## Статус живой проверки

Одна машина, один адрес, 13 октября.

| Что | Доказательства |
| --- | --- |
| Плитки поиска (`WB_TRANSPORT=dom`) | 214 плиток по 4 запросам: 183 с ценой Кошелька (у 43 только иконка), 23 с обычной ценой, 0 неоднозначных |
| Страницы товаров | 471317158: обычная 4 430, Кошелёк 4 341, старая 10 490 ₽; 1621670881: 29 142 / — / 53 967 ₽; 978347944: 1 624 / 1 591 / 13 202 ₽ — старая цена из `line-through` |
| `doctor` | 8 healthy, 5 inconclusive (WB API 403 с этого адреса; Lamoda, DNS, Мегамаркет без прохождения проверки; MPStats без токена), 0 drifted |

## Проверка поставки

- Офлайн: 2268 passed, 1 skipped, 5 live deselected; покрытие ветвей 84.70 %.
- Эксплуатационные тесты скриптов: 70 passed.
- Ruff, форматирование, mypy host/win32/darwin, версия 2.5.3 в 84 местах, 30 пинов фикстур, защита stdout, счётчик тестов (2269 в 7 местах), wire budget.
- 16/16 настоящих stdio MCP-сессий на v2.5.3.

Поставка содержит 16 wheels и 16 sdists; на PyPI пакеты не публикуются.

## English

v2.5.3 fixes Wildberries price roles in `WB_TRANSPORT=dom`. Narrow search tiles drop the «с WB Кошельком» caption but keep the Wallet icon; they were read as regular prices (43 of 214 tiles on 13 October) and are now Wallet prices. Product pages strike the old price with CSS, not `<del>`; the extractor now reads line-through, and several figures without strike evidence are no longer guessed — the roles stay null with a warning. Live: 214 tiles with 0 ambiguous, three product pages with the struck price read from CSS; doctor 8 healthy, 0 drift. Offline gate: 2268 passed, coverage 84.70 %, 16/16 stdio sessions.
