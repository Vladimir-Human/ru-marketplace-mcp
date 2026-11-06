"""Avito card details from the item page's own state: deal terms, seller checks, location.

``item_details_private_*`` is a trimmed, de-identified capture (2026-11-06) of a
private listing; its street address, map point and stations are fictional.

``item_details_company_*`` is a trimmed, de-identified capture (2026-11-06) of a
company seller's services listing (no schema.org Product on the page, so the card
reads the item-view DOM); its address and map point are fictional. The rule
combinations below edit the private capture in code (``_seller_type``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from avito_connector import item_state, server

FIXTURES = Path(__file__).parent / "fixtures"
PRIVATE = "item_details_private_9000000018.html"
_STATE_PRIVATE = '\\"isCompany\\": false'


def _seller_type(html: str, *, state: bool | None, dom: str | None) -> str:
    """The private capture with the page-state seller type and the DOM seller label set in code."""
    assert html.count(_STATE_PRIVATE) == 1
    if state is None:
        html = html.replace(_STATE_PRIVATE + ", ", "").replace(_STATE_PRIVATE, "")
    else:
        html = html.replace(_STATE_PRIVATE, f'\\"isCompany\\": {json.dumps(state)}')
    if dom is not None:
        html = html.replace("<body>", f'<body><div data-marker="seller-info/label">{dom}</div>', 1)
    return html


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


@pytest.mark.parametrize(("state", "dom"), [(True, "Компания"), (True, None), (None, "Компания"), (None, "Магазин")])
async def test_address_only_when_every_stated_source_says_company(monkeypatch, state, dom):
    """Unit test of the rule on an edited private capture, not a company-page fixture."""
    _serve_page(monkeypatch, _seller_type(_page(PRIVATE), state=state, dom=dom))

    result = await server.avito_card("9000000018")

    assert result.address == "Москва, Тестовая улица, 1"
    assert result.coords == {"lat": 55.5, "lng": 37.5}
    assert result.location == "Москва"


@pytest.mark.parametrize(
    ("state", "dom"),
    [
        (False, "Компания"),
        (True, "Частное лицо"),
        (False, "Магазин"),
        (False, None),
        (None, "Частное лицо"),
        (None, None),
    ],
)
async def test_contradicting_or_private_seller_types_never_expose_the_address(monkeypatch, state, dom):
    """Review #198 P1: hydration and DOM were OR-ed, so one «company» source exposed
    the street address and map point even when the other said private."""
    _serve_page(monkeypatch, _seller_type(_page(PRIVATE), state=state, dom=dom))

    result = await server.avito_card("9000000018")

    assert result.address is None
    assert result.coords is None
    assert "Тестовая" not in result.model_dump_json()
    assert result.location == "Москва"


def test_top_level_buyer_item_is_company_is_not_a_fallback():
    """Review #198 P1: buyerItem.isCompany is no longer read as a seller type."""
    assert item_state.is_company({"isCompany": True, "seller": {}}) is None
    assert item_state.is_company({"isCompany": True, "seller": {"isCompany": False}}) is False
    assert item_state.is_company({"seller": {"isCompany": "true"}}) is None


def test_unknown_seller_type_is_treated_as_private():
    details = item_state.card_details(_page(PRIVATE), 9000000018, None)
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
        # Review #198 P2: these three used to come back True.
        ("Торг: нет.", False),
        ("Торг не предлагать, цена окончательная.", False),
        ("Торг - нет", False),
        ("Торг невозможен.", False),
        ("Торг? Нет, цена окончательная.", False),
        ("Торг отсутствует.", False),
        ("Не торгуюсь.", False),
        ("Торг уместен. Без торга.", None),
        ("Торг не запрещён.", None),
    ],
)
def test_bargain_only_from_explicit_words(text, expected):
    assert item_state.bargain(text, []) is expected


@pytest.mark.parametrize(
    ("value", "text", "expected"),
    [
        ("Нет", None, False),
        ("нет", "Отличное состояние.", False),
        ("Да", None, True),
        ("Да", "Торг уместен.", True),
        ("Да", "Без торга.", None),
        ("Да", "Торг уместен. Без торга.", None),
        ("Нет", "Небольшой торг.", None),
        ("По договорённости", None, None),
    ],
)
def test_bargain_reads_the_explicit_parameter_first(value, text, expected):
    """Review #198 P2: the parameter «Торг = Нет» matched the bare word and gave True."""
    assert item_state.bargain(text, [{"name": "Торг", "value": value}]) is expected
    assert item_state.bargain(None, [{"name": "Торг", "value": "Да"}, {"name": "Торг", "value": "Нет"}]) is None


@pytest.mark.parametrize("value", ["false", "pending", {"status": "pending"}, ["x"], 1])
def test_only_a_true_verification_flag_is_a_seller_check(value):
    """Review #198 P1: any truthy value used to read as «ИНН подтверждён»."""
    item = {"seller": {"verification": {"isInnVerified": value, "isVerifiedSeller": value}}}

    assert item_state._seller_checks(item) == []
    item["seller"]["verification"]["isInnVerified"] = True
    assert item_state._seller_checks(item) == ["ИНН подтверждён"]


def _details_with(**safe_deal_or_offer) -> dict:
    bi = {"item": {"id": 1}, **safe_deal_or_offer}
    html = (
        "<script>window.__staticRouterHydrationData = JSON.parse("
        + json.dumps(json.dumps({"loaderData": {"x": {"buyerItem": bi}}}))
        + ");</script>"
    )
    return item_state.card_details(html, 1, None)


@pytest.mark.parametrize(
    ("safe_deal", "expected"),
    [
        ({"deliveryButtonEnabled": True, "deliveryButton": {"title": "Купить с доставкой"}}, True),
        ({"deliveryButtonEnabled": True, "deliveryButton": {"title": "Написать продавцу"}}, None),
        ({"deliveryButtonEnabled": True, "deliveryButton": {}}, None),
        ({"deliveryButtonEnabled": True}, None),
        ({"deliveryButtonEnabled": False, "deliveryButton": {"title": "Купить с доставкой"}}, False),
        ({"deliveryButtonEnabled": "true", "deliveryButton": {"title": "Купить с доставкой"}}, None),
    ],
)
def test_safe_deal_is_not_false_without_a_title(safe_deal, expected):
    """Review #198 P2: an enabled button with no recognisable title gave safe_deal=False."""
    assert _details_with(safeDealDesktop=safe_deal)["safe_deal"] is expected


@pytest.mark.parametrize(
    ("offer", "expected"), [({"buttonTitle": "Отправить"}, True), ("form", None), (None, None), ([], None)]
)
def test_price_offer_enabled_is_never_a_guessed_false(offer, expected):
    """Review #198 P2: a non-dict bargainOffer gave price_offer_enabled=False."""
    assert _details_with(bargainOffer=offer)["price_offer_enabled"] is expected


def test_delivery_services_are_deduplicated_and_bounded():
    """Review #198 P2: an inflated services array went to the answer unbounded."""
    services = [{"type": kind, "available": True, "enabled": True} for kind in ["delivery", "deliveryCourier"] * 50]
    services += [{"type": f"kind{n}", "available": True, "enabled": True} for n in range(40)]

    result = _details_with(safeDealDesktop={"services": services})["delivery_services"]

    assert result[:2] == ["pvz", "courier"]
    assert len(result) <= 10
    assert len(result) == len(set(result))


def test_seller_checks_are_deduplicated_and_bounded():
    badges = [{"title": "Данные подтверждены"}] * 30 + [{"title": f"Документы проверены {n}"} for n in range(30)]
    item = {"sellerBadgeBar": {"iconsBadges": badges, "badges": badges}}

    checks = item_state._seller_checks(item)

    assert checks[0] == "Данные подтверждены"
    assert len(checks) <= 10
    assert len(checks) == len(set(checks))


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


COMPANY = "item_details_company_9000000033.html"


async def test_a_real_company_page_returns_the_address_and_its_deal_terms(monkeypatch):
    """Review #198 P2: a captured company page instead of a synthetic isCompany flip."""
    _serve_page(monkeypatch, _page(COMPANY))

    result = await server.avito_card("9000000033")

    assert result.title == "Монтаж кондиционеров, фикстура"
    assert result.price_rub is None  # «от 5 500 ₽» is a starting price, not one RUB price
    assert result.address == "Москва, Тестовая улица, 2"
    assert result.coords == {"lat": 55.6, "lng": 37.6}
    assert result.location == "Москва"
    assert result.safe_deal is False
    assert result.price_offer_enabled is None  # bargainOffer is null on this page
    assert result.delivery_services == []
    assert result.views == 5913 and result.today_views == 3
    assert result.seller is not None
    assert result.seller.checks == ["Надёжный исполнитель"]
    assert (result.seller.tenure, result.seller.seller_since) == ("gold", "мая 2015")
    assert result.availability == "active"  # as the live call answered
    assert result.meta.healthy is True


async def test_a_real_company_page_with_a_private_dom_label_hides_the_address(monkeypatch):
    html = _page(COMPANY).replace(
        "<body>",
        '<body><div data-marker="seller-info/label">Компания</div>'
        '<div data-marker="seller-info/label">Частное лицо</div>',
        1,
    )
    _serve_page(monkeypatch, html)

    result = await server.avito_card("9000000033")

    assert result.address is None and result.coords is None
    assert "Тестовая" not in result.model_dump_json()


async def test_hydration_text_outside_script_and_malformed_containers_are_safe(monkeypatch):
    spoof = json.dumps(
        json.dumps(
            {
                "loaderData": {
                    "x": {
                        "buyerItem": {
                            "seller": {"isCompany": True},
                            "item": {
                                "id": 9000000018,
                                "isActive": True,
                                "deliveryInfo": {"isReserved": True},
                                "geo": {"address": "Москва, Спуф, 1", "coords": {"lat": 1.0, "lng": 2.0}},
                            },
                        }
                    }
                }
            }
        )
    )
    html = _page(PRIVATE).replace(
        "<body>",
        f"<body><div>window.__staticRouterHydrationData = JSON.parse({spoof});</div>",
        1,
    )
    _serve_page(monkeypatch, html)

    result = await server.avito_card("9000000018")

    assert result.availability == "active"
    assert result.address is None and result.coords is None
    malformed = _details_with(
        paramsBlock={"items": {"not": "a list"}},
        item={"id": 1, "badgeBar": {"badges": {"not": "a list"}}, "sellerAddressInfo": {"geoReferences": {"k": "v"}}},
    )
    assert malformed["params"] == [] and malformed["item_badges"] == [] and malformed["metro"] == []
