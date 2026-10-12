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
