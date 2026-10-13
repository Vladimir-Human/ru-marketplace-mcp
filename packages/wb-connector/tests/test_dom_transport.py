"""WB_TRANSPORT=dom: page parsing and the render/stop contract, all offline.

Fixture text is what the 2026-09-30 live session read in the scraping-profile
Chrome (search «UPD2018», product nm 1345073040), split into the lines
``innerText`` yields. One tile is also given flattened onto a single line, the
shape a layout change would produce, to prove parsing does not depend on the
line breaks.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from wb_connector import dom_transport as D
from wb_connector import server

SEARCH_PAYLOAD = {
    "title": "Интернет\u2011магазин Wildberries: широкий ассортимент товаров - ",
    "tiles": [
        {
            # verbatim from wb_dom_diag.py, 2026-09-30 (server-opened tab)
            "nm": "1517577386",
            "lines": [
                "−6%",
                "4 486 ₽",
                "4 897 ₽",
                "с WB Кошельком",
                "QULE",
                "/",
                "Адаптер DP USB3.1 Gen2 UPD2018",
                "5",
                "· 1 оценка",
                "11 октября",
            ],
            "label": "Адаптер DP USB3.1 Gen2 UPD2018 QULE",
            "del": ["4 897 ₽"],
            "wallet": [],
        },
        {
            "nm": "1119419020",
            "lines": [
                "−45%",
                "РАСПРОДАЖА",
                "4 961 ₽",
                "9 338 ₽",
                "с WB Кошельком",
                "JPHZNB",
                "/",
                "Платы расширения UPD2018 DP + USB3.1",
                "11 октября",
            ],
            "label": "Платы расширения UPD2018 DP + USB3.1 JPHZNB",
            "del": ["9 338 ₽"],
            "wallet": [],
        },
        {
            # flattened variant: everything on one line, no DOM hints
            "nm": "1552408950",
            "lines": [
                "−7% 5 155 ₽ 5 684 ₽ с WB Кошельком Нет бренда / Карта расширения для Dell Sunic upd2018 TX5M0 11 октября"
            ],
            "label": "Карта расширения для Dell Sunic upd2018 TX5M0",
            "del": [],
            "wallet": [],
        },
        {
            # no Wallet label: the lower figure is the regular price
            "nm": "1000000001",
            "lines": ["1 200 ₽", "1 500 ₽", "Бренд", "/", "Товар", "от 100 ₽/мес"],
            "label": "",
            "del": ["1 500 ₽"],
            "wallet": [],
        },
        {"nm": "not-a-number", "lines": ["1 ₽"]},
    ],
}

CARD_PAYLOAD = {
    # verbatim from wb_dom_diag.py, 2026-09-30: WB product pages render no <h1>
    "title": "Платы расширения UPD2018 DP + USB3.1 TX5M0 JPHZNB 1345073040",
    "url": "https://www.wildberries.ru/catalog/1345073040/detail.aspx",
    "h1": "",
    "lines": [
        "JPHZNB / Платы расширения JPHZNB UPD2018 DP + USB3.1 TX5M0",
        "Нет оценок",
        "4 961 ₽",
        "5 063 ₽",
        "9 338 ₽",
        "Купить",
        "В корзину",
        "Главная",
        "Электроника",
        "Комплектующие для ПК",
        "Материнские платы",
        "JPHZNB",
        "РАСПРОДАЖА",
        "Похожие",
        "JPHZNB",
        "Платы расширения JPHZNB UPD2018 DP + USB3.1 TX5M0",
        "Нет оценок",
        "Артикул",
        "1345073040",
        "Свойство 1",
        "Цвет: Full height version",
        "ИНН",
        "0000000000",
        "Наименование продавца",
        "Jiyuan Qiyun Network Technology Co., Ltd.",
        "Адрес продавца",
        "CN, Huling Science and Technology Building, West Section of Huanghe Avenue, Other Districts, Jiyuan City, Henan Province, 410881999",
        "Номер регистрации",
        "91419001MA40X2YF91",
        "Длина упаковки",
        "22 см",
        "Характеристики и описание",
        "Возврат через заявку",
        "Материнские Платы",
        "JPHZNB",
        "В каталог бренда",
        "Материнские Платы",
        "Все товары категории",
        "4 961 ₽",
        "5 063 ₽",
        "9 338 ₽",
        "Розыгрыш",
        "Купить сейчас",
        "Добавить в корзину",
        "11 октября,",
        "склад продавца",
        "Находки из Китая",
        "5,0",
        "Оценки0",
        "Отзывов пока нет — ваш может стать первым",
        "Поделитесь мнением о покупке и помогите другим покупателям сделать выбор",
        "Смотрите также",
        "−6%",
        "5 878 ₽",
        "6 415 ₽",
        "с WB Кошельком",
        "MXRSDF",
        "/",
        "Расширительная карта AOC-SLG3-2E4R NVME",
        "11 октября",
    ],
    "wallet": [],
    "del": [],
    "seller": ["Находки из Китая 5,0"] * 7 + ["Находки из Китая"],
}


SAMSUNG_CARD_PAYLOAD = {
    # verbatim from wb_card_dump.py (nm 164379765, 2026-09-30): Russian seller, 222 ratings, no Wallet price
    "title": 'SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB Samsung 164379765 купить за 20\u00a0825\u00a0₽ в интернет\u2011магазине Wildberries',
    "h1": "",
    "lines": [
        'Samsung / SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB',
        "5",
        "·",
        "222 оценки",
        '2,5" · 500 ГБ',
        "20 825 ₽",
        "29 249 ₽",
        "Купить",
        "В корзину",
        "Главная",
        "Электроника",
        "Комплектующие для ПК",
        "Твердотельные накопители SSD",
        "Samsung",
        "Похожие",
        "Samsung",
        "Оригинал",
        'SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB',
        "5 · 222 оценки",
        "42 вопроса",
        '2,5" · 500 ГБ',
        "Артикул",
        "164379765",
        "Гарантийный срок",
        "5 лет",
        "Форм-фактор накопителя",
        '2,5"',
        "Объем накопителя",
        "500 ГБ",
        "Интерфейс",
        "SATA",
        "Тип памяти накопителя",
        "3D NAND TLC (Samsung)",
        "Максимальная скорость записи",
        "530 Мб/с",
        "Характеристики и описание",
        "Возврат через заявку",
        "Внутренние Ssd-Накопители",
        "SAMSUNG",
        "В каталог бренда",
        "Внутренние Ssd-Накопители",
        "Все товары категории",
        "20 825 ₽",
        "29 249 ₽",
        "Хорошая цена",
        "Купить сейчас",
        "Добавить в корзину",
        "4 октября,",
        "склад продавца",
        "Modern Device",
        "4,9",
        "Все 27 предложений от 7 369 ₽",
        "Все",
        "7 369 ₽",
        "Нет оценок",
        "11 октября",
        "UJII",
        "7 623 ₽",
        "Нет оценок",
        "11 октября",
        "FGGG",
        "11 964 ₽",
        "Нет оценок",
        "11 октября",
        "IT Склад",
        "Оценки222",
        "Вопросы42",
        "5,0",
        "Выбор покупателей",
        "222 оценки",
    ],
    "wallet": [],
    "del": ["16 346 ₽", "19 048 ₽", "25 670 ₽", "28 814 ₽", "14 234 ₽", "34 760 ₽", "25 600 ₽", "68 020 ₽"],
    "seller": ["Modern Device 4,9"] * 7 + ["Modern Device"],
}


# --------------------------------------------------------------- price logic


@pytest.mark.parametrize(
    ("prices", "kwargs", "expected"),
    [
        ([4961, 5063, 9338], {}, (5063, 4961, 9338)),  # product page: wallet < regular < struck
        ([4961, 9338], {"wallet_label": True}, (None, 4961, 9338)),  # search tile
        ([1000, 1500], {}, (1000, None, 1500)),  # no wallet discount
        ([1000], {}, (1000, None, None)),
        ([], {}, (None, None, None)),
        ([4961, 5063, 9338], {"wallet_hint": 4961.0}, (5063, 4961, 9338)),
        ([4961, 9338], {"wallet_hint": 4961.0}, (None, 4961, 9338)),  # 9338/4961 > 1.15 → struck
        ([4961, 5063], {"wallet_hint": 4961.0}, (5063, 4961, None)),  # small gap → regular
        ([1200, 1500], {"crossed_hint": 1500.0}, (1200, None, 1500)),
        # acceptance 2026-09-30: <del> hint present AND the Wallet label
        ([4486, 4897], {"crossed_hint": 4897.0, "wallet_label": True}, (None, 4486, 4897)),
        # acceptance 2026-09-30: product page with a <del> hint
        ([4961, 5063, 9338], {"crossed_hint": 9338.0}, (5063, 4961, 9338)),
    ],
)
def test_assign_prices(prices: list[float], kwargs: dict[str, Any], expected: tuple) -> None:
    assert D.assign_prices([float(p) for p in prices], **kwargs) == expected


def test_prices_in_skips_instalments_and_reads_nbsp() -> None:
    assert D.prices_in("от 1 234 ₽/мес") == []
    assert D.prices_in("4\u00a0961\u00a0₽ 5\u2009063 ₽") == [4961.0, 5063.0]
    assert D.prices_in("−45% РАСПРОДАЖА") == []


@pytest.mark.parametrize("sign", ["-", "−", "–"])
@pytest.mark.parametrize("space", ["", " ", "\u00a0"])
def test_prices_in_rejects_negative_discount_badges(sign: str, space: str) -> None:
    assert D.prices_in(f"Скидка {sign}{space}1 500 ₽; цена 2 000 ₽") == [2000.0]


@pytest.mark.parametrize("amount", ["1 234,56", "1\u00a0234.56", "1234,5"])
def test_prices_in_preserves_fractional_roubles(amount: str) -> None:
    expected = 1234.5 if amount.endswith(",5") else 1234.56
    assert D.prices_in(f"{amount} ₽") == [expected]


# --------------------------------------------------------------- search grid


def test_search_tiles_parse_and_flag_wallet_prices() -> None:
    items, warnings = D.parse_search_page(SEARCH_PAYLOAD)
    by_nm = {item["nm_id"]: item for item in items}
    assert set(by_nm) == {1517577386, 1119419020, 1552408950, 1000000001}

    qule = by_nm[1517577386]
    assert (qule["brand"], qule["name"]) == ("QULE", "Адаптер DP USB3.1 Gen2 UPD2018")
    assert (qule["price_rub"], qule["wallet_price_rub"], qule["price_original_rub"]) == (4486.0, 4486.0, 4897.0)
    assert qule["price_kind"] == "wallet"
    assert (qule["review_rating"], qule["feedbacks"]) == (5.0, 1)
    assert qule["delivery"] == "11 октября"

    ours = by_nm[1119419020]
    assert (ours["brand"], ours["name"]) == ("JPHZNB", "Платы расширения UPD2018 DP + USB3.1")
    assert (ours["price_rub"], ours["price_kind"], ours["price_original_rub"]) == (4961.0, "wallet", 9338.0)

    flat = by_nm[1552408950]
    assert flat["brand"] == ""  # «Нет бренда»
    assert (flat["price_rub"], flat["price_original_rub"]) == (5155.0, 5684.0)
    assert flat["price_kind"] == "wallet"

    hinted = by_nm[1000000001]
    assert (hinted["price_rub"], hinted["price_original_rub"], hinted["price_kind"]) == (1200.0, 1500.0, "regular")
    assert (hinted["brand"], hinted["name"]) == ("Бренд", "Товар")

    assert any("Wallet price" in w for w in warnings)


# --------------------------------------------------------------- product page


def test_card_page_reference_nm() -> None:
    item, warnings = D.parse_card_page(CARD_PAYLOAD, 1345073040)
    assert warnings == []
    assert item is not None
    assert item["name"] == "Платы расширения JPHZNB UPD2018 DP + USB3.1 TX5M0"
    assert item["brand"] == "JPHZNB"
    assert (item["price_rub"], item["wallet_price_rub"], item["price_original_rub"]) == (5063.0, 4961.0, 9338.0)
    assert item["price_kind"] == "regular"
    assert item["in_stock"] is True
    assert (item["delivery"], item["warehouse"]) == ("11 октября", "склад продавца")
    assert (item["supplier"], item["supplier_rating"]) == ("Находки из Китая", 5.0)
    assert (item["review_rating"], item["feedbacks"]) == (None, 0)
    assert item["returns"] == "Возврат через заявку"
    assert item["seller_legal_name"] == "Jiyuan Qiyun Network Technology Co., Ltd."
    assert item["seller_country"] == "CN"
    assert item["seller_registration"] == "91419001MA40X2YF91"
    assert item["seller_inn"] is None  # 0000000000 is a placeholder
    assert item["cross_border"] is True


def test_card_page_with_del_hint_keeps_wallet_and_regular_apart() -> None:
    item, _ = D.parse_card_page({**CARD_PAYLOAD, "del": ["9 338 ₽", "6 415 ₽"]}, 1345073040)
    assert item is not None
    assert (item["price_rub"], item["wallet_price_rub"], item["price_original_rub"]) == (5063.0, 4961.0, 9338.0)


def test_card_page_ignores_neighbour_hints_seen_live() -> None:
    """Acceptance 2026-09-30, second run: first <del> was the carousel's 6 415 ₽ and the
    first [class*=seller] text was «Смотрите также»."""
    payload = {**CARD_PAYLOAD, "del": ["6 415 ₽", "9 338 ₽"], "seller": ["Смотрите также", "Находки из Китая 5,0"]}
    item, _ = D.parse_card_page(payload, 1345073040)
    assert item is not None
    assert (item["price_rub"], item["wallet_price_rub"], item["price_original_rub"]) == (5063.0, 4961.0, 9338.0)
    assert (item["supplier"], item["supplier_rating"]) == ("Находки из Китая", 5.0)


def test_store_fallback_skips_section_headings() -> None:
    assert D._store_from_seller_texts(["Смотрите также", "Магазин Ромашка 4,7"]) == ("Магазин Ромашка", 4.7)
    assert D._store_from_seller_texts(["Смотрите также"]) == ("", None)


def test_card_page_name_from_title_when_no_brand_line() -> None:
    lines = [line for line in CARD_PAYLOAD["lines"] if " / " not in line]
    item, _ = D.parse_card_page({**CARD_PAYLOAD, "lines": lines}, 1345073040)
    assert item is not None
    assert item["name"] == "Платы расширения UPD2018 DP + USB3.1 TX5M0 JPHZNB"


def test_unusable_render_is_not_cached() -> None:
    assert D._usable(CARD_PAYLOAD, "card") is True
    assert D._usable({"lines": ["Главная"]}, "card") is False
    assert D._usable({"tiles": []}, "search") is False


def test_card_page_store_fallback_without_seller_hint() -> None:
    payload = {**CARD_PAYLOAD, "seller": []}
    item, _ = D.parse_card_page(payload, 1345073040)
    assert item is not None
    assert (item["supplier"], item["supplier_rating"]) == ("Находки из Китая", 5.0)


def test_card_page_other_nm_is_skipped() -> None:
    item, warnings = D.parse_card_page(CARD_PAYLOAD, 1111111111)
    assert item is None
    assert "page shows 1345073040" in warnings[0]


def test_card_page_empty_render() -> None:
    item, warnings = D.parse_card_page({"h1": "", "title": "Интернет‑магазин Wildberries", "lines": []}, 42)
    assert item is None and "rendered no product" in warnings[0]


def test_card_page_russian_seller_is_domestic() -> None:
    lines = [
        "Бренд / Товар",
        "4,8 · 1 234 оценки",
        "1 000 ₽",
        "Добавить в корзину",
        "завтра",
        "Магазин 4,9",
        "Артикул",
        "42",
        "Адрес продавца",
        "Россия, Москва",
        "ИНН",
        "7700000001",
    ]
    item, _ = D.parse_card_page({"h1": "Товар", "lines": lines, "seller": []}, 42)
    assert item is not None
    assert item["cross_border"] is False and item["seller_country"] == "RU"
    assert (item["review_rating"], item["feedbacks"]) == (4.8, 1234)
    assert item["seller_inn"] == "7700000001"
    assert item["delivery"] == "завтра"


# --------------------------------------------------------------- render contract


class _FakePage:
    def __init__(self, probes: list[dict[str, Any]], extract: dict[str, Any]) -> None:
        self._probes = probes
        self._extract = extract
        self.url = "https://www.wildberries.ru/"

    async def evaluate(self, expression: str, arg: object = None) -> Any:
        if expression is D.PROBE_JS:
            probe = self._probes.pop(0) if len(self._probes) > 1 else self._probes[0]
            return json.dumps(probe)
        return json.dumps(self._extract)


def _patch_open_page(monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> list[str]:
    import mcp_core.transport.chrome_cdp as cdp

    opened: list[str] = []

    @asynccontextmanager
    async def fake_open_page(url: str, wait_ms: int = 0, *, allowed_hosts: Any = None):
        opened.append(url)
        assert allowed_hosts == D.ALLOWED_HOSTS
        yield page

    monkeypatch.setattr(cdp, "open_page", fake_open_page)
    monkeypatch.setattr(D._settings, "dom_min_gap", 0.0)
    monkeypatch.setattr(D._settings, "dom_wait_s", 2.0)
    D._dom_cache.clear()
    return opened


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_render_waits_out_the_browser_check(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage(
        [{"state": "challenge"}, {"state": "ok", "tiles": 3}, {"state": "ok", "tiles": 3}],
        SEARCH_PAYLOAD,
    )
    opened = _patch_open_page(monkeypatch, page)
    data = _run(D.render(D.search_url("UPD2018"), D.SEARCH_EXTRACT_JS, "search"))
    assert data["tiles"] and opened == ["https://www.wildberries.ru/catalog/0/search.aspx?search=UPD2018"]
    # cached: a second call does not navigate again
    _run(D.render(D.search_url("UPD2018"), D.SEARCH_EXTRACT_JS, "search"))
    assert len(opened) == 1


def test_card_render_waits_for_the_store_block(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage(
        [
            {"state": "ok", "sku": True, "rub": True, "store": False},
            {"state": "ok", "sku": True, "rub": True, "store": False},
            {"state": "ok", "sku": True, "rub": True, "store": True},
        ],
        CARD_PAYLOAD,
    )
    _patch_open_page(monkeypatch, page)
    monkeypatch.setattr(D._settings, "dom_store_wait_s", 10.0)
    _run(D.render(D.card_url(1345073040), D.CARD_EXTRACT_JS, "card"))
    assert page._probes == [{"state": "ok", "sku": True, "rub": True, "store": True}]  # polled until the store showed


def test_card_render_store_wait_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage([{"state": "ok", "sku": True, "rub": True, "store": False}], CARD_PAYLOAD)
    _patch_open_page(monkeypatch, page)
    monkeypatch.setattr(D._settings, "dom_store_wait_s", 0.0)
    data = _run(D.render(D.card_url(1345073041), D.CARD_EXTRACT_JS, "card"))
    assert data["lines"]  # returned without the store, no error


def test_card_render_waits_briefly_for_other_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage(
        [
            {"state": "ok", "sku": True, "rub": True, "store": True, "offers": False},
            {"state": "ok", "sku": True, "rub": True, "store": True, "offers": True},
        ],
        SAMSUNG_CARD_PAYLOAD,
    )
    _patch_open_page(monkeypatch, page)
    monkeypatch.setattr(D._settings, "dom_offers_wait_s", 10.0)
    _run(D.render(D.card_url(164379765), D.CARD_EXTRACT_JS, "card"))
    assert page._probes == [{"state": "ok", "sku": True, "rub": True, "store": True, "offers": True}]


def test_card_render_offers_wait_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage([{"state": "ok", "sku": True, "rub": True, "store": True, "offers": False}], CARD_PAYLOAD)
    _patch_open_page(monkeypatch, page)
    monkeypatch.setattr(D._settings, "dom_offers_wait_s", 0.0)
    data = _run(D.render(D.card_url(1345073042), D.CARD_EXTRACT_JS, "card"))
    assert data["lines"]


def test_render_stops_on_suspicious_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage([{"state": "blocked", "captcha": False}], {})
    opened = _patch_open_page(monkeypatch, page)
    with pytest.raises(ToolError) as exc:
        _run(D.render(D.card_url(1), D.CARD_EXTRACT_JS, "card"))
    body = json.loads(str(exc.value))
    assert body["error"] == "transport_down" and "Подозрительная активность" in body["message"]
    assert len(opened) == 1  # no retry


def test_render_captcha_requires_the_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage([{"state": "blocked", "captcha": True}], {})
    _patch_open_page(monkeypatch, page)
    with pytest.raises(ToolError) as exc:
        _run(D.render(D.card_url(2), D.CARD_EXTRACT_JS, "card"))
    body = json.loads(str(exc.value))
    assert body["error"] == "challenge_required" and body["requires_user_action"] is True


def test_render_challenge_that_never_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage([{"state": "challenge"}], {})
    _patch_open_page(monkeypatch, page)
    monkeypatch.setattr(D._settings, "dom_wait_s", 0.5)
    with pytest.raises(ToolError) as exc:
        _run(D.render(D.card_url(3), D.CARD_EXTRACT_JS, "card"))
    assert "did not clear" in json.loads(str(exc.value))["message"]


def test_render_reports_the_page_empty_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage([{"state": "ok", "tiles": 0, "empty": True}], {"tiles": []})
    _patch_open_page(monkeypatch, page)
    data = _run(D.render(D.search_url("zzzqqq"), D.SEARCH_EXTRACT_JS, "search"))
    assert data["page_says_empty"] is True


# --------------------------------------------------------------- tool wiring


@pytest.mark.parametrize(
    ("page", "says_empty", "expected"),
    [
        (1, False, "parser_drift"),  # no tiles and no notice: the selectors drifted
        (1, True, "no_results"),  # WB itself said nothing was found
        (2, False, "no_results"),  # past the last page
    ],
)
def test_dom_search_empty_grid_is_drift_unless_the_page_says_empty(
    monkeypatch: pytest.MonkeyPatch, page: int, says_empty: bool, expected: str
) -> None:
    monkeypatch.setattr(server._settings, "transport", "dom")

    async def fake_render(url: str, js: str, want: str) -> dict[str, Any]:
        return {"tiles": [], "page_says_empty": says_empty}

    monkeypatch.setattr(D, "render", fake_render)
    if expected == "parser_drift":
        with pytest.raises(ToolError) as exc:
            _run(server.wb_search(query="UPD2018", page=page))
        assert json.loads(str(exc.value))["error"] == "parser_drift"
    else:
        assert _run(server.wb_search(query="UPD2018", page=page)).status == "no_results"


def test_dom_empty_search_keeps_nondefault_region_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server._settings, "transport", "dom")

    async def fake_render(url: str, js: str, want: str) -> dict[str, Any]:
        return {"tiles": [], "page_says_empty": True}

    monkeypatch.setattr(D, "render", fake_render)
    result = _run(server.wb_search(query="nothing", dest="-1029256"))
    assert result.status == "no_results"
    data = result.model_dump(mode="json")
    assert data["meta"]["source"] == "wb_search"
    assert data["meta"]["healthy"] is True
    assert data["meta"]["warnings"] == server._dom_dest_warning("-1029256")
    assert json.loads(result.model_dump_json())["meta"] == data["meta"]


def test_dom_empty_search_without_warnings_keeps_compact_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server._settings, "transport", "dom")

    async def fake_render(url: str, js: str, want: str) -> dict[str, Any]:
        return {"tiles": [], "page_says_empty": True}

    monkeypatch.setattr(D, "render", fake_render)
    result = _run(server.wb_search(query="nothing"))
    assert result.model_dump(mode="json") == {
        "status": "no_results",
        "query": "nothing",
        "page": 1,
        "total_ids": 0,
        "meta": None,
    }


def test_tools_route_to_dom_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server._settings, "transport", "dom")

    async def fake_render(url: str, js: str, want: str) -> dict[str, Any]:
        return SEARCH_PAYLOAD if want == "search" else CARD_PAYLOAD

    monkeypatch.setattr(D, "render", fake_render)

    search = _run(server.wb_search(query="UPD2018"))
    assert search.count == 4 and all(i.transport == "dom" for i in search.items)
    assert all(i.brand for i in search.items if i.nm_id in (1517577386, 1119419020))
    assert any("Wallet price" in w for w in search.meta.warnings)

    card = _run(server.wb_card(nm_ids=[1345073040]))
    assert card.count == 1 and card.items[0].transport == "dom"
    only = card.items[0]
    assert (only.price_rub, only.wallet_price_rub, only.price_kind) == (5063.0, 4961.0, "regular")
    assert any("cross-border" in w for w in card.meta.warnings)


def test_card_dom_caps_navigations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server._settings, "transport", "dom")
    monkeypatch.setattr(server._settings, "dom_max_cards", 2)
    seen: list[str] = []

    async def fake_render(url: str, js: str, want: str) -> dict[str, Any]:
        seen.append(url)
        return {"h1": "", "lines": []}

    monkeypatch.setattr(D, "render", fake_render)
    card = _run(server.wb_card(nm_ids=[1, 2, 3]))
    assert len(seen) == 2 and card.count == 0
    assert any("read the first 2" in w for w in card.meta.warnings)


def test_api_transport_is_the_default() -> None:
    assert type(server._settings).model_fields["transport"].default == "api"


def test_card_page_russian_seller_with_ratings_seen_live() -> None:
    item, warnings = D.parse_card_page(SAMSUNG_CARD_PAYLOAD, 164379765)
    assert item is not None
    assert (item["brand"], item["name"]) == ("Samsung", 'SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB')
    assert (item["review_rating"], item["feedbacks"]) == (5.0, 222)  # was (2.0, 22): «222 оценки» read as «2» + «22»
    assert (item["price_rub"], item["wallet_price_rub"], item["price_original_rub"]) == (20825.0, None, 29249.0)
    assert (item["supplier"], item["supplier_rating"]) == ("Modern Device", 4.9)
    assert (item["delivery"], item["warehouse"]) == ("4 октября", "склад продавца")
    assert item["warranty"] == "5 лет"
    assert item["original_badge"] is True
    assert (item["other_offers_count"], item["other_offers_from_rub"]) == (27, 7369.0)
    assert item["seller_legal_name"] is None and item["cross_border"] is None
    assert any("anomalously cheap" in w and "-65%" in w for w in warnings)


def test_rating_count_line_alone_is_not_a_rating() -> None:
    assert D.parse_rating(["222 оценки"]) == (None, None)
    assert D.parse_rating(["5", "·", "222 оценки"]) == (5.0, 222)
    assert D.parse_rating(["4,8 1 234 оценки"]) == (4.8, 1234)
    assert D.parse_rating(["5 · 1 оценка"]) == (5.0, 1)


def test_reference_card_has_no_offer_anomaly_and_no_badge() -> None:
    item, warnings = D.parse_card_page(CARD_PAYLOAD, 1345073040)
    assert item is not None
    assert item["original_badge"] is None and item["other_offers_count"] is None
    assert not any("anomalously" in w for w in warnings)


def test_reviews_score_h1_is_not_the_product_name() -> None:
    """Live 2026-09-30 after 0012: the longer wait let the reviews widget render, whose
    «5,0» score is the only <h1> on the page; it came back as the product name."""
    item, _ = D.parse_card_page({**SAMSUNG_CARD_PAYLOAD, "h1": "5,0"}, 164379765)
    assert item is not None
    assert item["name"] == 'SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB'
    lines = [line for line in SAMSUNG_CARD_PAYLOAD["lines"] if " / " not in line]
    item, _ = D.parse_card_page({**SAMSUNG_CARD_PAYLOAD, "lines": lines, "h1": "Название товара"}, 164379765)
    assert item is not None and item["name"] == "Название товара"


# Live 2026-10-01 (wb_card_dump.py, lines 0-61 verbatim): the same nm 164379765 as above,
# but its main offer (Modern Device) is unavailable — «Нет в наличии», no «Купить» / «В корзину». The page still
# shows «Все 26 предложений от 7 413 ₽»: those prices and sellers belong to OTHER cards (nm 1605653581 …).
# `offers` is what CARD_EXTRACT_JS collected from the block's /catalog/<nm>/detail.aspx tiles in the same session.
UNAVAILABLE_CARD_PAYLOAD = {
    "title": 'SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB Samsung 164379765 купить в интернет‑магазине Wildberries',
    "h1": "",
    "lines": [
        'Samsung / SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB',
        "5",
        "·",
        "222 оценки",
        '2,5" · 500 ГБ',
        "Нет в наличии",
        "В избранное",
        "Главная",
        "Электроника",
        "Комплектующие для ПК",
        "Твердотельные накопители SSD",
        "Samsung",
        "Похожие",
        "Samsung",
        "Оригинал",
        'SSD накопитель 2.5" 870 EVO MZ-77E500BW 500GB',
        "5 · 222 оценки",
        "42 вопроса",
        '2,5" · 500 ГБ',
        "Артикул",
        "164379765",
        "Гарантийный срок",
        "5 лет",
        "Форм-фактор накопителя",
        '2,5"',
        "Объем накопителя",
        "500 ГБ",
        "Интерфейс",
        "SATA",
        "Тип памяти накопителя",
        "3D NAND TLC (Samsung)",
        "Максимальная скорость записи",
        "530 Мб/с",
        "Характеристики и описание",
        "Внутренние Ssd-Накопители",
        "SAMSUNG",
        "В каталог бренда",
        "Внутренние Ssd-Накопители",
        "Все товары категории",
        "Нет в наличии",
        "В избранное",
        "Modern Device",
        "4,9",
        "Все 26 предложений от 7 413 ₽",
        "Все",
        "7 413 ₽",
        "Нет оценок",
        "11 октября",
        "UJII",
        "7 669 ₽",
        "Нет оценок",
        "11 октября",
        "FGGG",
        "12 037 ₽",
        "Нет оценок",
        "11 октября",
        "IT Склад",
        "Оценки222",
        "Вопросы42",
        "5,0",
        "Выбор покупателей",
        "222 оценки",
    ],
    "wallet": [],
    "del": [],
    "seller": ["Modern Device 4,9"] * 7 + ["Modern Device"],
    "offers": [
        {"nm": "1605653581", "lines": ["7 413 ₽", "Нет оценок", "11 октября", "UJII"]},
        {"nm": "1605660096", "lines": ["7 669 ₽", "Нет оценок", "11 октября", "FGGG"]},
        {"nm": "1060828177", "lines": ["12 037 ₽", "Нет оценок", "11 октября", "IT Склад"]},
    ],
}

OTHER_SELLER_FIELDS = (
    "price_rub",
    "wallet_price_rub",
    "price_original_rub",
    "supplier",
    "supplier_rating",
    "delivery",
    "warehouse",
)


def test_unavailable_main_offer_does_not_take_another_sellers_fields() -> None:
    """Was: supplier «UJII», price 7413, delivery «11 октября» — nm 1605653581's offer glued onto nm 164379765."""
    item, warnings = D.parse_card_page(UNAVAILABLE_CARD_PAYLOAD, 164379765)
    assert item is not None
    assert item["in_stock"] is False
    assert (item["price_rub"], item["wallet_price_rub"], item["price_original_rub"]) == (None, None, None)
    assert (item["supplier"], item["supplier_rating"]) == ("", None)
    assert (item["delivery"], item["warehouse"], item["returns"]) == (None, None, None)
    assert item["price_kind"] == ""
    # what belongs to the card itself stays
    assert (item["review_rating"], item["feedbacks"]) == (5.0, 222)
    assert item["warranty"] == "5 лет" and item["original_badge"] is True
    assert (item["other_offers_count"], item["other_offers_from_rub"]) == (26, 7413.0)
    assert warnings == [
        "wb_dom_card: nm 164379765 main offer unavailable; 26 other sellers from 7413 ₽ (cheapest nm 1605653581)"
    ]


def test_unavailable_main_offer_lists_the_other_sellers() -> None:
    item, _ = D.parse_card_page(UNAVAILABLE_CARD_PAYLOAD, 164379765)
    assert item is not None
    assert item["other_offers"] == [
        {"nm_id": 1605653581, "supplier": "UJII", "price_rub": 7413.0, "price_kind": "", "delivery": "11 октября"},
        {"nm_id": 1605660096, "supplier": "FGGG", "price_rub": 7669.0, "price_kind": "", "delivery": "11 октября"},
        {"nm_id": 1060828177, "supplier": "IT Склад", "price_rub": 12037.0, "price_kind": "", "delivery": "11 октября"},
    ]


def test_unavailable_main_offer_without_offer_tiles_still_blanks_the_card() -> None:
    """An older payload (text only, no `offers`): the block's text must not leak either."""
    payload = {k: v for k, v in UNAVAILABLE_CARD_PAYLOAD.items() if k != "offers"}
    item, warnings = D.parse_card_page(payload, 164379765)
    assert item is not None
    assert item["in_stock"] is False and item["price_rub"] is None and item["supplier"] == ""
    assert item["other_offers"] == []
    assert "main offer unavailable; 26 other sellers from 7413 ₽" in warnings[0]
    assert "cheapest nm" not in warnings[0]


def test_other_offers_are_the_five_cheapest_and_never_this_card() -> None:
    tiles = [{"nm": str(1000 + i), "lines": [f"{9000 - i * 100} ₽", "Нет оценок", "завтра", f"S{i}"]} for i in range(8)]
    tiles.append({"nm": "164379765", "lines": ["100 ₽", "5,0", "завтра", "Self"]})
    tiles.append({"nm": "1000", "lines": ["50 ₽", "завтра", "Dup"]})  # the same nm twice: first tile wins
    item, _ = D.parse_card_page({**UNAVAILABLE_CARD_PAYLOAD, "offers": tiles}, 164379765)
    assert item is not None
    assert [o["nm_id"] for o in item["other_offers"]] == [1007, 1006, 1005, 1004, 1003]
    assert item["other_offers"][0]["supplier"] == "S7"


def test_available_main_offer_ignores_other_sellers_block() -> None:
    """With a buy block the price and store come from it only; the 30.09 expectations stay, offers are added."""
    payload = {
        **SAMSUNG_CARD_PAYLOAD,
        "seller": ["UJII"],  # a page-wide «seller» element belonging to the offers block must not win
        "offers": UNAVAILABLE_CARD_PAYLOAD["offers"],
    }
    item, warnings = D.parse_card_page(payload, 164379765)
    assert item is not None
    assert (item["price_rub"], item["supplier"], item["supplier_rating"]) == (20825.0, "Modern Device", 4.9)
    assert (item["delivery"], item["warehouse"]) == ("4 октября", "склад продавца")
    assert item["in_stock"] is True
    assert [o["supplier"] for o in item["other_offers"]] == ["UJII", "FGGG", "IT Склад"]
    assert any("anomalously cheap" in w for w in warnings)
    assert not any("main offer unavailable" in w for w in warnings)


def test_unavailable_card_does_not_compare_prices() -> None:
    _, warnings = D.parse_card_page(UNAVAILABLE_CARD_PAYLOAD, 164379765)
    assert not any("anomalously" in w for w in warnings)


def test_other_offers_html_fragment_matches_the_tiles() -> None:
    """The captured block (provenance: card_other_offers_164379765.provenance.json) carries what the JS reads."""
    html = (Path(__file__).parent / "fixtures" / "card_other_offers_164379765.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'href="https://www\.wildberries\.ru/catalog/(\d+)/detail\.aspx"', html)
    assert hrefs == [offer["nm"] for offer in UNAVAILABLE_CARD_PAYLOAD["offers"]]
    assert "/catalog/164379765/other-sellers" in html
    tiles = re.split(r"<li[^>]*>", html)[1:]
    for tile, offer in zip(tiles, UNAVAILABLE_CARD_PAYLOAD["offers"], strict=True):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tile).replace("&nbsp;", " ")).strip()
        assert text == " ".join(offer["lines"])


def test_card_extractor_collects_offer_tiles() -> None:
    assert "other-sellers" in D.CARD_EXTRACT_JS and "offers: offers" in D.CARD_EXTRACT_JS


def test_api_items_have_no_other_offers() -> None:
    from wb_connector.models_output import WbCardItem

    assert WbCardItem().other_offers == []


# Live 2026-10-01 (wb_card_dump.py 1345073040, lines 0-54 verbatim): the card has its own offer and ONE other
# seller, whose block header is «1 предложение от 4 735 ₽» — singular, no «Все». `offers` is what CARD_EXTRACT_JS
# read from the block's tile in the same session (HTML: fixtures/card_one_other_offer_1345073040.html).
ONE_OTHER_OFFER_PAYLOAD = {
    "title": "Платы расширения UPD2018 DP + USB3.1 TX5M0 JPHZNB 1345073040 купить за 5 005 ₽ в интернет‑магазине Wildberries",
    "h1": "",
    "lines": [
        "JPHZNB / Платы расширения JPHZNB UPD2018 DP + USB3.1 TX5M0",
        "Нет оценок",
        "4 904 ₽",
        "5 005 ₽",
        "9 231 ₽",
        "Купить",
        "В корзину",
        "Главная",
        "Электроника",
        "Комплектующие для ПК",
        "Материнские платы",
        "JPHZNB",
        "РАСПРОДАЖА",
        "Похожие",
        "JPHZNB",
        "Платы расширения JPHZNB UPD2018 DP + USB3.1 TX5M0",
        "Нет оценок",
        "Артикул",
        "1345073040",
        "Свойство 1",
        "Цвет: Full height version",
        "ИНН",
        "0000000000",
        "Наименование продавца",
        "Jiyuan Qiyun Network Technology Co., Ltd.",
        "Адрес продавца",
        "CN, Huling Science and Technology Building, West Section of Huanghe Avenue, Other Districts, Jiyuan City, Henan Province, 410881999",
        "Номер регистрации",
        "91419001MA40X2YF91",
        "Длина упаковки",
        "22 см",
        "Характеристики и описание",
        "Возврат через заявку",
        "Материнские Платы",
        "JPHZNB",
        "В каталог бренда",
        "Материнские Платы",
        "Все товары категории",
        "4 904 ₽",
        "5 005 ₽",
        "9 231 ₽",
        "Розыгрыш",
        "Купить сейчас",
        "Добавить в корзину",
        "12 октября,",
        "склад продавца",
        "Находки из Китая",
        "5,0",
        "1 предложение от 4 735 ₽",
        "Все",
        "4 735 ₽",
        "Нет оценок",
        "12 октября",
        "Находки из Китая",
        "Оценки0",
    ],
    "wallet": ["4 904 ₽", "5 005 ₽ 9 231 ₽", "5 005 ₽", "9 231 ₽", "4 904 ₽ 5 005 ₽ 9 231 ₽", "4 904 ₽"],
    "del": ["6 341 ₽", "7 180 ₽", "10 663 ₽", "2 964 ₽", "838 ₽", "1 441 ₽", "5 305 ₽", "1 602 ₽"],
    "seller": ["Находки из Китая 5,0"] * 7 + ["Находки из Китая"],
    "offers": [{"nm": "1552408950", "lines": ["4 735 ₽", "Нет оценок", "12 октября", "Находки из Китая"]}],
}


def test_single_other_offer_header_gives_count_and_floor() -> None:
    """Was: other_offers listed 1552408950, but count and floor were None — «1 предложение от …» matched nothing."""
    item, warnings = D.parse_card_page(ONE_OTHER_OFFER_PAYLOAD, 1345073040)
    assert item is not None
    assert (item["other_offers_count"], item["other_offers_from_rub"]) == (1, 4735.0)
    assert item["other_offers"] == [
        {
            "nm_id": 1552408950,
            "supplier": "Находки из Китая",
            "price_rub": 4735.0,
            "price_kind": "",
            "delivery": "12 октября",
        }
    ]
    assert (item["price_rub"], item["wallet_price_rub"], item["price_original_rub"]) == (5005.0, 4904.0, 9231.0)
    assert (item["supplier"], item["supplier_rating"]) == ("Находки из Китая", 5.0)
    assert item["in_stock"] is True and warnings == []


def test_single_other_offer_block_is_cut_out_of_the_card_lines() -> None:
    lines = ONE_OTHER_OFFER_PAYLOAD["lines"]
    cut = D._without_offers_block(lines, ONE_OTHER_OFFER_PAYLOAD["offers"])
    assert not any("предложение" in line for line in cut)
    assert "4 735 ₽" not in cut and cut[-1] == "Оценки0"


@pytest.mark.parametrize(
    ("line", "count", "floor"),
    [
        ("1 предложение от 4 735 ₽", 1, 4735.0),
        ("Все 26 предложений от 7 413 ₽", 26, 7413.0),
        ("Все 2 предложения от 999 ₽", 2, 999.0),
        ("5 предложений от 1 200 000 ₽", 5, 1200000.0),
    ],
)
def test_other_offers_header_forms(line: str, count: int, floor: float) -> None:
    match = D._OFFERS_RE.search(line)
    assert match is not None and (int(match.group(1)), float(re.sub(r"\D", "", match.group(2)))) == (count, floor)
    assert D._OFFERS_HEAD_RE.match(line)


def test_other_offers_html_fragment_single_tile_matches() -> None:
    html = (Path(__file__).parent / "fixtures" / "card_one_other_offer_1345073040.html").read_text(encoding="utf-8")
    assert "1&nbsp;предложение" in html or "1 предложение" in html
    assert re.findall(r'href="https://www\.wildberries\.ru/catalog/(\d+)/detail\.aspx"', html) == ["1552408950"]
    assert "/catalog/1345073040/other-sellers" in html


def test_card_probe_waits_for_the_singular_header() -> None:
    assert r"предложени\S*\s+от" in D.PROBE_JS
