# v1.2.0 — шесть новых маркетплейсов и один общий сервер

MCP-серверы для чтения российских маркетплейсов. Только чтение: ни ключей, ни
паролей, ни аккаунта.

Было пять источников и 22 инструмента. Стало **одиннадцать источников и 42
инструмента**, и всё это теперь можно подключить одной строкой конфига вместо
десяти.

## Что нового

**Шесть площадок:**

- **Авито** (4) — объявления, карточки, репутация продавца. Это классифайд, а не
  каталог: отзывов на товар не существует, поэтому сигналом доверия служит
  рейтинг продавца.
- **Мегамаркет** (3) — поиск и карточки через мобильный JSON API.
- **Lamoda** (3) — карточки с размерами через GraphQL, поиск через браузер.
- **DNS** (3) и **Ситилинк** (3) — электроника.
- **Taobao** (3) — китайские товары. Цены остаются в юанях и **никогда** не
  конвертируются: зашитый курс молча устареет.

**Объединённый сервер `marketplace-mcp`** — одна запись в конфиге MCP-клиента
вместо двенадцати. Внутри те же 41 инструмент плюс `marketplace_sources`,
который честно говорит, какие источники поднялись, а какие нет и почему.

**Сравнение цен по девяти источникам сразу.** Детский мир исключён намеренно:
его API принимает текстовый запрос и игнорирует его, возвращая весь каталог.
Лучше не иметь инструмента, чем иметь такой.

## Заметные исправления

**Цена могла оказаться платежом по рассрочке.** Экстракторы выбирали цену как
наименьшее число на плитке, а плитка DNS показывает «от 5 751 ₽/ мес.» рядом с
ценой 58 999 ₽. Такое значение проходит валидацию и выглядит правдоподобно — то
есть хуже, чем пустое поле. Теперь ценой считается только число, привязанное к
знаку валюты; рассрочка, бонусы, бейдж скидки и счётчик пунктов выдачи
отбрасываются.

**Поиск DNS возвращал 24 ссылки без названий и цен.** Плитка искалась через
`closest()`, а он проверяет сам элемент раньше предков — и «карточкой»
становилась ссылка на картинку, у которой нет текста. Страница с товарами
выглядела пустой.

**Ситилинк не находил цену никогда.** Знак ₽ у него лежит в отдельном элементе
от цифр, а фильтр требовал их в одной строке.

**Поиск Авито падал целиком из-за одного поля.** `location` приходит объектом, а
не строкой; Pydantic отклонял ответ, и вместе с одним полем терялась вся
страница объявлений. Там же нашлись ещё два дефекта: у каждого результата поиска
отсутствовала ссылка (ключ `urlPath`, а искали `uriPath` — одна буква) и дата
публикации.

**Поиск Wildberries за последней страницей отдавал первую.** `page=20` возвращала
те же 100 товаров, что и `page=1` — HTTP 200, без ошибки и без признака. Тот, кто
листает выдачу, собирал дубли и считал, что пагинирует.

## Что стоит знать перед использованием

**Семь источников требуют вашего Chrome.** Ozon, Авито, Taobao, Мегамаркет,
Lamoda, DNS и Ситилинк отказывают датацентровым адресам: капча, редирект-петля
или JavaScript proof-of-work. Они читаются внутри браузера, в который вы вошли
сами. Инструкция — в [docs/CDP_SETUP.md](docs/CDP_SETUP.md).

**Три источника работают без всего** — Wildberries, Яндекс Маркет, Детский мир.

**Успешный selfcheck не означает, что данные верны.** Он говорит только, что
транспорт ответил и парсер вернул форму. У DNS был ровно такой случай: после
одной правки selfcheck позеленел, а все названия и цены остались пустыми. Если
число важно — откройте страницу и сравните.

**У Яндекса цена на странице — это цена с Плюсом.** Инструмент отдаёт обычную
цену в `price_rub`, подписочную — в `price_with_plus`. Сравнение цен ранжирует по
обычной.

## Насколько это проверено

Честно, потому что проверить может каждый.

| Проверено сверкой с живым сайтом | Поставляется, сверка за вами |
|---|---|
| Детский мир, Яндекс Маркет, Авито, карточка Wildberries | Ozon, Мегамаркет, Lamoda, Taobao, DNS, Ситилинк |

Для четырёх источников название, цена, продавец и наличие сверены с живыми
страницами и совпали. Остальные шесть отвечают отказом на датацентровый адрес,
поэтому проверить их можно только с вашей машины — прогоните `*_selfcheck` и
сверьте два-три товара глазами.

Парсеры DNS и Ситилинка отдельно проверены на снятой с живых страниц разметке:
тесты прогоняют настоящий JS коннектора и сверяют результат с ценами, которые в
тот момент были на сайте. Не хватает только сквозного прогона через ваш Chrome.

**Известное расхождение:** поиск Wildberries отдаёт цену примерно на полпроцента
выше карточки, а карточка совпадает с сайтом до рубля. Измерено на одном товаре —
WB режет частые запросы к поиску. Для точной цены берите `wb_card`.

## Совместимость

Имена, сигнатуры и формы ответов 22 инструментов v1.1.0 не менялись. Все
изменения — добавления.

## Установка

```bash
git clone https://github.com/Vladimir-Human/ru-marketplace-mcp
cd ru-marketplace-mcp
uv sync --all-packages
```

Один сервер вместо двенадцати:

```json
{
  "mcpServers": {
    "marketplace": {
      "command": "uv",
      "args": ["run", "--directory", "/путь/к/ru-marketplace-mcp", "marketplace-mcp"]
    }
  }
}
```

Полная инструкция и таблица инструментов — в [README.md](README.md), история
изменений — в [CHANGELOG.md](CHANGELOG.md), отчёт аудита — в
[AUDIT_REPORT.md](AUDIT_REPORT.md).

---

# v1.2.0 — six new marketplaces and one unified server (English)

MCP servers for reading Russian marketplaces. Read-only: no keys, no passwords,
no account.

Five sources and 22 tools became **eleven sources and 42 tools**, and the whole
set now connects through a single config entry instead of twelve.

## New

**Six marketplaces:** Avito (4 tools), Megamarket (3), Lamoda (3), DNS (3),
Citilink (3), Taobao (3). Taobao prices stay in yuan and are never converted — a
hardcoded rate would go stale silently.

**A unified `marketplace-mcp` server** — one MCP client entry instead of twelve,
carrying the same 41 tools plus `marketplace_sources`, which reports which
sources actually mounted and why the others did not.

**Price comparison across nine sources.** Detsky Mir is deliberately excluded:
its API accepts a text query and ignores it, returning the whole catalogue.

## Notable fixes

**A price could turn out to be an instalment.** Extractors picked the smallest
number on a tile, and a DNS tile shows "от 5 751 ₽/ мес." next to a 58 999 ₽
price. That value validates and looks plausible, which makes it worse than an
empty field. A price is now only a number attached to a currency glyph.

**DNS search returned 24 links with no titles and no prices** — tiles were
resolved with `closest()`, which tests the element itself first and landed on the
image link, which has no text.

**Citilink never found a price** — it renders the ₽ glyph in a separate element
from the digits.

**Avito search failed entirely over one field** — `location` arrives as an object,
not a string. Two more defects surfaced with it: every search hit was missing its
URL and its publication date.

**Wildberries search served page 1 again past the end of the result set** —
`page=20` returned the same 100 products as `page=1`, HTTP 200, no marker.

## Before you rely on it

Seven sources need your own logged-in Chrome (Ozon, Avito, Taobao, Megamarket,
Lamoda, DNS, Citilink); three work anonymously (Wildberries, Yandex Market,
Detsky Mir). **A passing selfcheck does not mean the data is right** — it means
the transport answered. On Yandex Market the price shown on the page is the Plus
subscriber price; the everyday price is `price_rub`.

**Verified against live pages:** Detsky Mir, Yandex Market, Avito, and the
Wildberries card path. **Shipped but unverified from a datacenter address:** Ozon,
Megamarket, Lamoda, Taobao, DNS, Citilink — run their `*_selfcheck` from your own
machine and compare a couple of products by eye.

**Known discrepancy:** Wildberries search reports a price about half a percent
above the card, and the card matches the site exactly. Measured on one product.
Use `wb_card` when the number matters.

## Compatibility

The 22 tools of v1.1.0 keep their names, signatures and wire shapes. Every change
is additive.

---

Автор и мейнтейнер: [@Vladimir-Human](https://github.com/Vladimir-Human) · MIT
