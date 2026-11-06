"""Avito card details from the item page's own state: deal terms, seller checks, location.

``item_details_private_*`` is a trimmed, de-identified capture (2026-11-06) of a
private listing; its street address, map point and stations are fictional.
``item_details_company_*`` is a labelled synthetic edit with isCompany=true.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from avito_connector import item_state, server

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE, COMPANY = "item_details_private_9000000018.html", "item_details_company_9000000017.html"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()
    yield
    server._cache._data.clear()


def _serve_page(monkeypatch, html: str) -> None:
    async def fake_fetch_item_page(url, ctx):
        return 200, html, "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


async def test_card_reads_deal_terms_date_views_and_seller_checks(monkeypatch):
    _serve_page(monkeypatch, _page(PRIVATE))

    result = await server.avito_card("9000000018")

    assert result.condition == "Новое"
    assert [p.name for p in result.params] == ["Тип товара", "Вид товара", "Состояние"]
    assert result.delivery_available is True
    assert result.delivery_services == ["pvz", "courier", "seller_delivery", "seller_own_courier", "click_and_collect"]
    assert result.payment_on_delivery is True
    assert result.safe_deal is True
    assert result.price_offer_enabled is True
    assert result.bargain is None
    assert result.posted_at == "25 октября в 12:20"
    assert result.views == 8690
    assert result.today_views == 47
    assert result.seller is not None
    assert result.seller.checks == ["Надёжный продавец", "Данные подтверждены"]
    assert result.seller.tenure == "gold"
    assert result.seller.reply_time == "Отвечает около часа"
    assert result.seller.seller_since is None
    assert result.meta.healthy is True


async def test_private_seller_gets_city_and_stations_but_no_address(monkeypatch):
    html = _page(PRIVATE)
    # The page does carry a street address and a map point...
    raw = item_state.card_details(html, 9000000018, None)
    assert raw["address"] == "Москва, Тестовая улица, 1" and raw["coords"] == {"lat": 55.5, "lng": 37.5}
    # ...and a private seller's card does not return them.

    _serve_page(monkeypatch, html)
    result = await server.avito_card("9000000018")

    assert result.location == "Москва"
    assert [m.name for m in result.metro] == ["Станция-один", "Станция-два"]
    assert result.metro[0].distance == "600 м" and result.metro[0].walk == "6–10 мин."
    assert result.address is None
    assert result.coords is None
    assert "Тестовая" not in result.model_dump_json()


async def test_company_seller_gets_the_address_the_page_gives(monkeypatch):
    _serve_page(monkeypatch, _page(COMPANY))

    result = await server.avito_card("9000000017")

    assert result.address == "Москва, Тестовая улица, 1"
    assert result.coords == {"lat": 55.5, "lng": 37.5}
    assert result.location == "Москва"


def test_unknown_seller_type_is_treated_as_private():
    details = item_state.card_details(_page(COMPANY), 9000000017, None)
    details["is_company"] = None
    card: dict = {"seller": None}

    server._apply_details(card, details)

    assert card.get("address") is None
    assert card.get("coords") is None
    assert card["location"] == "Москва"
    assert [m.name for m in card["metro"]] == ["Станция-один", "Станция-два"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Цена окончательная, без торга.", False),
        ("Торг уместен при осмотре.", True),
        ("Небольшой торг.", True),
        ("Забрать у торгового центра.", None),
        ("Отличное состояние.", None),
    ],
)
def test_bargain_only_from_explicit_words(text, expected):
    assert item_state.bargain(text, []) is expected


def test_promotional_seller_badges_are_not_checks():
    bi = item_state.buyer_item(item_state.load_hydration(_page(PRIVATE)), 9000000018)
    assert bi is not None
    titles = [badge["title"] for badge in bi["item"]["sellerBadgeBar"]["iconsBadges"]]
    assert "Эковклад" in titles
    assert "Эковклад" not in item_state.card_details(_page(PRIVATE), 9000000018, None)["seller"]["checks"]


def test_json_ld_item_condition_is_the_fallback_condition():
    html = (
        '<script type="application/ld+json">{"@type": "Product", "sku": "9000000019", "name": "Приставка", '
        '"offers": {"price": "1000", "priceCurrency": "RUB", "itemCondition": "https://schema.org/UsedCondition"}}</script>'
    )

    assert server.parse_item_page(html, 9000000019)["condition"] == "Б/у"


def test_a_page_without_state_leaves_every_detail_empty():
    details = item_state.card_details("<html><body></body></html>", 1, None)

    assert details["params"] == [] and details["metro"] == [] and details["delivery_services"] == []
    assert all(details[key] is None for key in ("condition", "safe_deal", "location", "views", "address", "coords"))
