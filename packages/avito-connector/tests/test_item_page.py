"""Offline item-page contracts: rendered HTML, never the generic catalog API.

The captured page is immutable; expected values are independently written here,
not extracted from the same JSON-LD that the parser is meant to validate.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from avito_connector import server
from fastmcp.exceptions import ToolError
from mcp_core.errors import NotFoundError, ParserDriftError

FIXTURE = Path(__file__).parent / "fixtures" / "item_page_7924452716.html"
ITEM_ID = 7924452716
ITEM_URL = "https://www.avito.ru/moskva/telefony/iphone_15_128_gb_sim_esim_7924452716"
DOM_FIXTURE = FIXTURE.with_name("item_page_dom_7931870037.html")
DOM_ITEM_ID = 7931870037
DOM_ITEM_URL = "https://www.avito.ru/voronezh/noutbuki/shustrye_noutbuki_dlya_ucheby_raboty_pvz_na_ssd_7931870037"
TITLE = "iPhone 15, 128 ГБ, SIM + eSIM"
DESCRIPTION = (
    "📱iРhоnе 15 128gb и другие • Оригинал • Запечатанный • Полный рабочий функционал "
    "⭐️Почему выбирают нас • Нам можно доверять: работаем честно и прозрачно с 2017 года "
    "• Высокий сервис и обслуживание • Большой ассортимент и широкий выбор аксессуаров "
    "• Служба поддержки и заботы клиентов после покупки ⠀ 📲Дополнительные услуги "
    "• Настроим новый смартфон под вас • Перенесём данные со старого телефона на новый "
    "• Установим санкционные приложения Сбер, Т-Банк и др. "
    "• Наклеим качественно защитное стекло и подберём чехол ⠀ 📦Доставка "
    "• По Москве в день заказа курьером • По Московской области в день заказа в течение дня "
    "• Авито Доставка по Москве и в регионы (Почта РФ, Яндекс, СДЭК, Боксберри) ⠀ 🏢Самовывоз "
    "• Офис продаж в центре Москвы в 5 минутах от м. Авиамоторное "
    "• Можно распаковать и проверить устройство на месте по чек-листу "
    "❗️Просьба ставить нужную модель в резерв за час ⠀ 💵Способы оплаты "
    "• Наличные при получении • Рассрочка от Авито • Оплата по карте через сервис Авито "
    "🩵Приятных покупок!"
)
CATALOG_BODY = json.dumps({"catalog": {"items": [{"id": ITEM_ID, "title": "Search hit"}]}, "totalCount": 1})


def _html(payload: dict) -> str:
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></head><body></body></html>"
    )


def _product() -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Product",
        "sku": str(ITEM_ID),
        "name": TITLE,
        "description": "Описание объявления",
        "image": ["https://www.avito.ru/img/share/auto/52107895470"],
        "offers": {"@type": "Offer", "price": "39990", "priceCurrency": "RUB", "url": ITEM_URL},
    }


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """A pre-fix card gets the misleading catalog success, never real HTTP/CDP."""
    server._cache._data.clear()
    server._pacer.reset()

    async def fake_fetch(url, ctx):
        return 200, CATALOG_BODY, "cdp"

    def unexpected_transport(*args, **kwargs):
        raise AssertionError("offline test attempted an unmocked transport")

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    monkeypatch.setattr(server, "_sync_curl_get", unexpected_transport)
    monkeypatch.setattr(server, "_cdp_fetch", unexpected_transport)
    monkeypatch.setattr(server, "open_page", unexpected_transport)
    yield
    server._cache._data.clear()
    server._pacer.reset()


def _patch_item_page(monkeypatch, html: str, status: int = 200):
    async def fake_fetch_item_page(url, ctx):
        return status, html, "cdp"

    # Keep old code executable: its catalog-only success must fail the assertion.
    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page, raising=False)


def _patch_navigation(monkeypatch, error: Exception):
    calls = []

    async def no_wait():
        return None

    @asynccontextmanager
    async def fake_open_page(url, **kwargs):
        calls.append(url)
        raise error
        yield  # pragma: no cover -- make this an async context manager

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "open_page", fake_open_page)
    return calls


def test_captured_item_page_matches_exact_ground_truth():
    item = server.parse_item_page(FIXTURE.read_text(encoding="utf-8"), ITEM_ID)

    assert item["item_id"] == ITEM_ID
    assert item["title"] == TITLE
    assert item["price_rub"] == 39990.0
    assert isinstance(item["price_rub"], float)
    assert item["images"] == 2
    assert item["url"] == ITEM_URL
    assert item["description"] == DESCRIPTION
    for field in ("location", "posted_at", "views", "seller"):
        assert item.get(field) is None
    assert "in_stock" not in item


async def test_card_opens_full_url_without_query_and_reads_page_content(monkeypatch):
    events = []
    html = FIXTURE.read_text(encoding="utf-8")

    class Page:
        url = ITEM_URL

        async def content(self):
            events.append("content")
            return html

        async def evaluate(self, *args, **kwargs):
            raise AssertionError("item cards must read rendered HTML, not fetch JSON")

    class RawPage:
        url = ITEM_URL

        async def evaluate(self, expression, arg=None):
            assert expression == "() => document.documentElement.outerHTML"
            assert "fetch" not in expression
            assert arg is None
            events.append("outerHTML")
            return html

    async def polite_wait():
        events.append("paced")

    @asynccontextmanager
    async def fake_open_page(url, **kwargs):
        assert events == ["paced"]
        assert set(kwargs["allowed_hosts"]) == {"avito.ru", "www.avito.ru"}
        if url == ITEM_URL:
            page = Page()
        else:
            assert url == f"https://www.avito.ru/{ITEM_ID}"
            page = RawPage()
        events.append("opened")
        yield page
        events.append("closed")

    monkeypatch.setattr(server, "_polite_wait", polite_wait)
    monkeypatch.setattr(server, "open_page", fake_open_page)

    result = await server.avito_card(ITEM_URL + "?context=tracking&from=search#details")

    assert result.status == "success"
    assert result.item_id == ITEM_ID
    assert result.title == TITLE
    assert result.price_rub == 39990
    assert result.images == 2
    assert result.description == DESCRIPTION
    assert result.url == ITEM_URL
    assert result.tier_used == "cdp"
    assert result.location is None
    assert result.posted_at is None
    assert result.views is None
    assert result.seller is None
    assert "in_stock" not in result.model_dump()
    assert events == ["paced", "opened", "content", "closed"]

    # Raw CDP exposes evaluate, not content. A second request in this same case
    # must snapshot the rendered DOM, never perform an in-page network fetch.
    events.clear()
    raw_result = await server.avito_card(str(ITEM_ID))

    assert raw_result.model_dump() == result.model_dump()
    assert events == ["paced", "opened", "outerHTML", "closed"]


def _dom_html(*, title=TITLE, price="39\u00a0990 ₽", item_id=ITEM_ID):
    return (
        f'<h1 data-marker="item-view/title-info">{title}</h1>'
        f'<div data-marker="item-view/item-price-container">'
        f'<span data-marker="item-view/item-price">{price}</span></div>'
        f'<span data-marker="item-view/item-id">№ {item_id}</span>'
        '<div data-marker="item-view/item-description"><p>Описание объявления</p></div>'
    )


async def test_card_captured_dom_page_matches_exact_ground_truth(monkeypatch):
    html = DOM_FIXTURE.read_text(encoding="utf-8")
    assert "application/ld+json" not in html
    item = server.parse_item_page(html)  # The id must come from the DOM, not the argument.

    assert item["item_id"] == DOM_ITEM_ID
    assert item["title"] == "Шустрые ноутбуки для учебы, работы, пвз на SSD"
    assert item["price_rub"] == 8700.0
    assert isinstance(item["price_rub"], float)
    assert item["description"].startswith("🍃 Бoлее 20 нoутбукoв в наличии")
    assert "Наш склад находится в Воронеже" in item["description"]
    assert "Рейтинг профиля — 5.0" in item["description"]
    assert "лучшие варианты из наличия с фото и характеристиками" in item["description"]
    assert item["images"] is None  # Unknown; the retained seller logo is not a listing photo.
    assert server.parse_item_seller(html) == {
        "name": "Роман 36 ноутбуков",
        "rating_score": 5.0,
        "rating_count": 116,
        "is_company": True,
        "profile_url": "https://www.avito.ru/brands/i224915198",
    }
    _patch_item_page(monkeypatch, html)

    result = await server.avito_card(DOM_ITEM_URL)

    assert result.status == "success"
    assert result.item_id == DOM_ITEM_ID
    assert result.title == item["title"]
    assert result.price_rub == 8700.0
    assert result.description == item["description"]
    assert result.url == DOM_ITEM_URL
    assert result.tier_used == "cdp"
    assert result.meta.healthy is True
    assert result.seller is not None
    assert result.seller.name == "Роман 36 ноутбуков"
    assert result.seller.rating_score == 5.0
    assert result.seller.rating_count == 116
    assert result.seller.is_company is True
    assert result.seller.profile_url == "https://www.avito.ru/brands/i224915198"
    assert result.seller.seller_id == "i224915198"


async def test_selfcheck_first_dom_result_is_healthy(monkeypatch):
    calls = []
    profile_url = "https://www.avito.ru/brands/i224915198"

    async def fake_fetch(url, ctx):
        calls.append("search")
        payload = {
            "totalCount": 1,
            "items": [{"id": DOM_ITEM_ID, "title": "Ноутбуки", "price": 8700, "uri": DOM_ITEM_URL}],
        }
        return 200, json.dumps(payload, ensure_ascii=False), "cdp"

    async def fake_fetch_item_page(url, ctx):
        if url == DOM_ITEM_URL:
            calls.append("card")
            return 200, DOM_FIXTURE.read_text(encoding="utf-8"), "cdp"
        assert url == profile_url
        calls.append("seller")
        # Synthetic profile smoke only; the card and its seller block are captured.
        return 200, "<title>Роман 36 ноутбуков - отзывы на Авито</title>", "cdp"

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)

    result = await server.avito_selfcheck()

    assert calls == ["search", "card", "seller"]
    assert result.status == "success"
    assert result.healthy is True
    assert all(check.state == "healthy" for check in result.checks.values())
    assert result.checks["card"].notes == ["item page card parsed"]


async def test_card_dom_item_id_mismatch_is_parser_drift(monkeypatch):
    html = DOM_FIXTURE.read_text(encoding="utf-8").replace("№&nbsp;7931870037", "№&nbsp;4612345678")
    _patch_item_page(monkeypatch, html)

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(DOM_ITEM_URL)

    assert "parser_drift" in str(excinfo.value)


async def test_card_dom_without_title_or_price_is_parser_drift(monkeypatch):
    _patch_item_page(monkeypatch, "<title>Авито — Объявления на сайте Авито</title>" + _dom_html(title="", price=""))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(str(ITEM_ID))

    assert "parser_drift" in str(excinfo.value)


@pytest.mark.parametrize("page_id", ["", "№ 4612345678", f"№ {ITEM_ID} и 4612345678"])
def test_dom_missing_or_ambiguous_item_id_is_parser_drift(page_id):
    html = _dom_html().replace(f"№ {ITEM_ID}", page_id)

    with pytest.raises(ParserDriftError):
        server.parse_item_page(html, ITEM_ID)


@pytest.mark.parametrize("page_id", ["", "№ 4612345678"])
def test_dom_conflicting_item_id_markers_are_parser_drift(page_id):
    html = _dom_html() + f'<span data-marker="item-view/item-id">{page_id}</span>'

    with pytest.raises(ParserDriftError):
        server.parse_item_page(html, ITEM_ID)


@pytest.mark.parametrize(
    "price",
    [
        "",
        "Цена по запросу",
        "0 ₽",
        "39 990",
        "39 990 $",
        "39 990 RUB",
        "39 990 ₽ 49 990 ₽",
        "39 990 49 990 ₽",
        "39990 49990 ₽",
        "39..990 ₽",
    ],
    ids=[
        "missing",
        "on-request",
        "zero",
        "no-currency",
        "usd",
        "currency-text",
        "ambiguous",
        "shared-currency",
        "two-amounts",
        "malformed-grouping",
    ],
)
def test_dom_missing_or_non_ruble_prices_are_none_never_zero(price):
    item = server.parse_item_page(_dom_html(price=price), ITEM_ID)

    assert item["title"] == TITLE
    assert item["price_rub"] is None


def test_dom_price_only_card_requires_matching_id():
    item = server.parse_item_page(_dom_html(title=""), ITEM_ID)

    assert item["item_id"] == ITEM_ID
    assert item["title"] is None
    assert item["price_rub"] == 39990.0


def test_product_path_wins_over_conflicting_dom_markers():
    html = _html(_product()) + _dom_html(title="Не то объявление", price="1 ₽", item_id=4612345678)

    item = server.parse_item_page(html, ITEM_ID)

    assert item["title"] == TITLE
    assert item["price_rub"] == 39990.0
    assert item["images"] == 1
    assert item["url"] == ITEM_URL


def test_empty_or_mismatched_product_does_not_fall_back_to_dom():
    for product in ({"@type": "Product"}, {**_product(), "sku": "4612345678"}):
        with pytest.raises(ParserDriftError):
            server.parse_item_page(_html(product) + _dom_html(), ITEM_ID)


async def test_card_without_json_ld_is_parser_drift(monkeypatch):
    _patch_item_page(monkeypatch, "<html><body>Обычная страница объявления</body></html>")

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(str(ITEM_ID))

    assert "parser_drift" in str(excinfo.value)


async def test_catalog_json_is_never_an_empty_successful_card(monkeypatch):
    # Both paths return the observed catalog envelope. Before the fix, _fetch
    # produced a successful card with an id but no title/price/description.
    async def fake_fetch(url, ctx):
        return 200, CATALOG_BODY, "cdp"

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    _patch_item_page(monkeypatch, CATALOG_BODY)

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(str(ITEM_ID))

    assert "parser_drift" in str(excinfo.value)


def test_bare_product_without_graph_is_supported():
    item = server.parse_item_page(_html(_product()))

    assert item["item_id"] == ITEM_ID
    assert item["title"] == TITLE
    assert item["price_rub"] == 39990
    assert item["url"] == ITEM_URL
    assert item["images"] == 1


def test_requested_sku_wins_over_the_first_product():
    unrelated = _product()
    unrelated.update(sku="4612345678", name="Не то объявление")
    unrelated["offers"] = {"price": "1", "priceCurrency": "RUB"}
    html = _html({"@graph": [unrelated, {"@type": "BreadcrumbList"}, _product()]})

    item = server.parse_item_page(html, ITEM_ID)

    assert item["item_id"] == ITEM_ID
    assert item["title"] == TITLE
    assert item["price_rub"] == 39990


def test_a_product_for_another_sku_is_drift_not_the_card():
    """Review of #119: a lone Product for a different ad must not pass as this ad."""
    unrelated = _product()
    unrelated.update(sku="4612345678", name="Не то объявление")

    with pytest.raises(server.ParserDriftError):
        server.parse_item_page(_html({"@graph": [unrelated]}), ITEM_ID)


@pytest.mark.parametrize(
    "offers",
    [
        {"price": "39990", "priceCurrency": "USD"},
        {"priceCurrency": "RUB"},
        {"price": None, "priceCurrency": "RUB"},
    ],
    ids=["non-rub", "missing-price", "null-price"],
)
def test_non_rub_or_missing_prices_are_none_never_zero(offers):
    product = _product()
    product["offers"] = {**offers, "url": ITEM_URL}

    item = server.parse_item_page(_html(product), ITEM_ID)

    assert item["title"] == TITLE
    assert item["price_rub"] is None
    assert item["price_rub"] != 0


def test_empty_product_is_raw_parser_drift():
    with pytest.raises(ParserDriftError):
        server.parse_item_page(_html({"@type": "Product"}), ITEM_ID)


def test_malformed_json_ld_is_raw_parser_drift():
    with pytest.raises(ParserDriftError):
        server.parse_item_page('<script type="application/ld+json">{"@type":</script>', ITEM_ID)


@pytest.mark.parametrize(
    "text", ["Объявление не найдено", "Объявление снято с публикации", "Ошибка 404. Страница не найдена"]
)
def test_not_found_text_is_raw_not_found(text):
    with pytest.raises(NotFoundError):
        server.parse_item_page(f"<html><body><h1>{text}</h1></body></html>", ITEM_ID)


async def test_navigation_block_is_transport_down_not_parser_drift(monkeypatch):
    calls = _patch_navigation(monkeypatch, server.NavBlocked(503, ITEM_URL))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(ITEM_URL)

    assert calls == [ITEM_URL]
    assert "transport_down" in str(excinfo.value)
    assert "parser_drift" not in str(excinfo.value)
    assert server._pacer.consecutive_refusals == 0


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_navigation_refusal_is_counted_and_reported(monkeypatch, status):
    _patch_navigation(monkeypatch, server.NavBlocked(status, ITEM_URL))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(ITEM_URL)

    assert "transport_down" in str(excinfo.value)
    assert server._pacer.consecutive_refusals == 1


async def test_navigation_404_is_not_found_not_a_refusal(monkeypatch):
    _patch_navigation(monkeypatch, server.NavBlocked(404, ITEM_URL))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(ITEM_URL)

    assert "not_found" in str(excinfo.value)
    assert server._pacer.consecutive_refusals == 0


async def test_navigation_timeout_is_transport_down(monkeypatch):
    calls = _patch_navigation(monkeypatch, TimeoutError("offline navigation timed out"))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(ITEM_URL)

    assert calls == [ITEM_URL]
    assert "transport_down" in str(excinfo.value)
    assert "parser_drift" not in str(excinfo.value)
    assert server._pacer.consecutive_refusals == 0
