"""Offline tests for the Cian connector.

Every upstream call is monkeypatched at the transport dispatch layer
(``_fetch_search`` / ``_fetch_card``), so the suite runs with no network and no
browser. Fixtures are real payloads captured over CDP on 2026-09-09 and trimmed
(provenance sits next to each file): a new-building sale in Moscow whose card
carries the price only in ``bargainTerms.price``, and a long-term rent in
St. Petersburg with a deposit and a monthly period.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from cian_connector import server
from fastmcp.exceptions import ToolError

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SEARCH_SALE = _load("search_sale_live.json")
SEARCH_RENT = _load("search_rent_live.json")
SEARCH_DAILY = _load("search_daily_live.json")
CARD_SALE = _load("card_sale_live.json")
CARD_RENT = _load("card_rent_live.json")
CARD_DAILY = _load("card_daily_live.json")

WAF_BODY = (
    "<!DOCTYPE html><html><head><title>Ошибка - Циан</title></head><body>"
    "Обнаружен подозрительный трафик. Код страницы: cian_waf_block</body></html>"
)


def _card_body(fixture: dict, url: str = "https://www.cian.ru/sale/flat/331002424/") -> str:
    return json.dumps({"kind": "ok", "title": "Продажа квартиры", "url": url, "offerData": fixture["offerData"]})


@pytest.fixture(autouse=True)
def _no_cache():
    """Every test starts with an empty cache: a cached body from a previous
    case would otherwise shadow its monkeypatched fetch."""
    server._cache._data.clear()


def _patch_search(monkeypatch, result, calls: list | None = None):
    async def fake(query, ctx):
        if calls is not None:
            calls.append(query)
        return result

    monkeypatch.setattr(server, "_fetch_search", fake)


def _patch_card(monkeypatch, result, calls: list | None = None):
    async def fake(offer_id, ctx):
        if calls is not None:
            calls.append(offer_id)
        return result

    monkeypatch.setattr(server, "_fetch_card", fake)


def _ok(payload: dict) -> tuple[int, str, str]:
    return 200, json.dumps(payload, ensure_ascii=False), "cdp"


# ---------------------------------------------------------- jsonQuery ----


def test_query_for_a_flat_sale_carries_rooms_and_price_range():
    query = server._build_json_query(
        deal="sale",
        offer_type="flat",
        region="1",
        rooms=[2, 1, 1],
        price_min=10_000_000,
        price_max=12_000_000,
        area_min=None,
        area_max=None,
        page=3,
    )

    assert query["_type"] == "flatsale"
    assert query["engine_version"] == {"type": "term", "value": 2}
    assert query["region"] == {"type": "terms", "value": [1]}
    assert query["room"] == {"type": "terms", "value": [1, 2]}
    assert query["price"] == {"type": "range", "value": {"gte": 10_000_000, "lte": 12_000_000}}
    assert query["page"] == {"type": "term", "value": 3}
    assert "for_day" not in query
    assert "total_area" not in query


def test_query_for_rent_adds_the_long_term_flag_and_open_ranges():
    query = server._build_json_query(
        deal="rent",
        offer_type="flat",
        region="2",
        rooms=None,
        price_min=None,
        price_max=60_000,
        area_min=30.0,
        area_max=None,
        page=1,
    )

    assert query["_type"] == "flatrent"
    assert query["for_day"] == {"type": "term", "value": "!1"}
    assert query["price"] == {"type": "range", "value": {"lte": 60_000}}
    assert query["total_area"] == {"type": "range", "value": {"gte": 30.0}}
    assert "room" not in query


def test_query_for_rooms_houses_and_commercial_use_their_own_families():
    room = server._build_json_query(
        deal="sale",
        offer_type="room",
        region="1",
        rooms=[3],
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )
    house = server._build_json_query(
        deal="rent",
        offer_type="house",
        region="4593",
        rooms=[3],
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )
    office = server._build_json_query(
        deal="sale",
        offer_type="commercial",
        region="1",
        rooms=None,
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )

    # Rooms are flats with room=[0]; the caller's room filter is irrelevant there.
    assert room["_type"] == "flatsale" and room["room"] == {"type": "terms", "value": [0]}
    assert house["_type"] == "suburbanrent" and "room" not in house
    assert office["_type"] == "commercialsale" and "room" not in office


# ---------------------------------------------------------- cian_search ----


async def test_search_parses_a_sale_page(monkeypatch):
    calls: list = []
    _patch_search(monkeypatch, _ok(SEARCH_SALE), calls)

    result = await server.cian_search("sale", rooms=[1], price_min=10_000_000, price_max=12_000_000)

    assert result.status == "success"
    assert result.deal == "sale" and result.offer_type == "flat" and result.region == "1"
    assert result.count == 3
    assert result.total_count == 501
    assert result.tier_used == "cdp"
    assert result.meta.healthy is True
    assert calls[0]["_type"] == "flatsale" and calls[0]["room"]["value"] == [1]

    first = result.items[0]
    assert first.offer_id == 331002424
    assert first.price_rub == 11985439
    assert first.price_unit == "total"
    assert first.deal_type == "sale"
    assert first.category == "newBuildingFlatSale"
    assert first.rooms == 1
    assert first.total_area_m2 == 38.1
    assert first.kitchen_area_m2 == 16.6
    assert first.floor == 12 and first.floors_total == 22
    assert first.address == "Москва, Московский, микрорайон Первый Московский"
    assert first.metro is not None
    assert first.metro.name == "Рассказовка" and first.metro.minutes == 6 and first.metro.mode == "transport"
    assert first.url == "https://www.cian.ru/sale/flat/331002424/"
    assert first.agency_name == "Абсолют Недвижимость"
    assert first.seller_type == "developer"
    assert first.newbuilding_name == "Город-парк Первый Московский"
    assert first.created_at == "2026-06-11T19:42:24.507"
    assert first.photos == 2  # trimmed fixture keeps two
    # Cian sets no title on this offer; the short info line stands in.
    assert first.title == "1-комн.кв. · 12/22 этаж"


async def test_search_parses_a_rent_page_with_period_and_deposit(monkeypatch):
    _patch_search(monkeypatch, _ok(SEARCH_RENT))

    result = await server.cian_search("rent", region="2", rooms=[2])

    assert result.total_count == 1227
    first = result.items[0]
    assert first.offer_id == 317754437
    assert first.deal_type == "rent"
    assert first.price_rub == 60000
    assert first.price_unit == "month"
    assert first.price_period == "monthly"
    assert first.lease_term == "fewMonths"
    assert first.deposit_rub == 50000
    assert first.is_by_homeowner is False
    assert first.title == "Сдаются впервые после ремонта"
    assert first.agency_name == "SVET hotel"
    # Three stations in the payload; Академическая is 7 min by transport, but
    # Cian marks Гражданский проспект (10 min on foot) as the tile's station.
    assert first.metro is not None and first.metro.name == "Гражданский проспект" and first.metro.mode == "walk"
    assert first.url == "https://spb.cian.ru/rent/flat/317754437/"


async def test_search_a_priceless_offer_is_none_never_zero(monkeypatch):
    """Cian shows 'цена не указана' for some offers; the payload then carries no
    price keys at all. It must surface as None — a 0 would rank it cheapest."""
    payload = copy.deepcopy(SEARCH_SALE)
    offer = payload["data"]["offersSerialized"][0]
    for key in ("priceRur", "price"):
        offer["bargainTerms"].pop(key, None)
    offer.pop("priceTotalRur", None)
    _patch_search(monkeypatch, _ok(payload))

    result = await server.cian_search("sale")

    assert result.items[0].price_rub is None
    assert result.items[0].price_rub != 0
    assert result.items[1].price_rub is not None


async def test_search_reads_the_default_region_from_settings(monkeypatch):
    calls: list = []
    _patch_search(monkeypatch, _ok(SEARCH_SALE), calls)

    result = await server.cian_search("sale")

    assert result.region == server._settings.region
    assert calls[0]["region"]["value"] == [int(server._settings.region)]


@pytest.mark.parametrize("bad_region", ["", "moscow", "1 2", "1;rm -rf", "spb"])
async def test_search_rejects_a_malformed_region(bad_region):
    """Refusal must come from argument parsing, never from a failed fetch."""
    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale", region=bad_region)
    assert "bad_request" in str(excinfo.value)


@pytest.mark.parametrize("bad_rooms", [[0], [8], [10], [1, 8]])
async def test_search_rejects_unknown_room_codes(bad_rooms):
    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale", rooms=bad_rooms)
    assert "bad_request" in str(excinfo.value)


async def test_search_rejects_inverted_ranges():
    with pytest.raises(ToolError):
        await server.cian_search("sale", price_min=5, price_max=1)
    with pytest.raises(ToolError):
        await server.cian_search("sale", area_min=50.0, area_max=20.0)


async def test_search_maps_a_waf_block_to_transport_down(monkeypatch):
    _patch_search(monkeypatch, (403, WAF_BODY, "cdp_blocked"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale")

    text = str(excinfo.value)
    assert "transport_down" in text
    assert "cian.ru" in text


async def test_search_treats_a_200_block_page_as_a_block_not_drift(monkeypatch):
    """The WAF page can arrive as a 200 through the in-page fetch; the marker
    in the body decides, not the status."""
    _patch_search(monkeypatch, (200, WAF_BODY, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale")

    assert "transport_down" in str(excinfo.value)


async def test_search_maps_non_json_to_parser_drift(monkeypatch):
    _patch_search(monkeypatch, (200, "<html><body>Циан</body></html>", "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale")

    assert "parser_drift" in str(excinfo.value)


async def test_search_maps_a_changed_envelope_to_parser_drift(monkeypatch):
    _patch_search(monkeypatch, _ok({"status": "ok", "data": {"offers": []}}))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("sale")

    assert "parser_drift" in str(excinfo.value)
    assert "offersSerialized" in str(excinfo.value)


async def test_search_warns_on_empty_items_with_nonzero_total(monkeypatch):
    _patch_search(monkeypatch, _ok({"status": "ok", "data": {"offersSerialized": [], "aggregatedCount": 41}}))

    result = await server.cian_search("sale")

    assert result.count == 0
    assert result.total_count == 41
    assert result.meta.healthy is False
    assert "empty_items_with_nonzero_total" in result.meta.warnings


async def test_search_an_empty_result_with_zero_total_is_healthy(monkeypatch):
    _patch_search(monkeypatch, _ok({"status": "ok", "data": {"offersSerialized": [], "aggregatedCount": 0}}))

    result = await server.cian_search("sale")

    assert result.count == 0
    assert result.meta.healthy is True


# ------------------------------------------------------------ cian_card ----


async def test_card_parses_a_new_building_sale(monkeypatch):
    _patch_card(monkeypatch, (200, _card_body(CARD_SALE), "cdp"))

    result = await server.cian_card("331002424")

    assert result.offer_id == 331002424
    # The card carries no priceRur — the price comes from bargainTerms.price.
    assert result.price_rub == 11985439
    assert result.deal_type == "sale"
    assert result.category == "newBuildingFlatSale"
    assert result.rooms == 1
    assert result.total_area_m2 == 38.1
    assert result.floor == 12 and result.floors_total == 22
    assert result.building_material == "monolith"
    assert result.ceiling_height_m == 2.73
    assert result.description is not None and result.description.startswith("Продается однокомнатная квартира")
    assert result.views == 12907
    assert result.updated_at == "2026-09-08T22:25:42.932744Z"
    # Four changes on this offer, newest first; the two most recent are pinned.
    assert len(result.price_history) == 4
    assert [change.price_rub for change in result.price_history[:2]] == [11985439, 11866772]
    assert result.price_history[0].changed_at == "2026-09-01T19:14:33.257248Z"
    assert result.agency_name == "Абсолют Недвижимость"
    assert result.seller_type == "developer"
    assert result.agent is not None
    assert result.agent.name == "Абсолют Недвижимость"
    assert result.agent.account_type == "agency"
    assert result.agent.user_type == "developer"
    assert result.agent.agent_id == 134642318
    assert result.metro and result.metro[0].name == "Рассказовка"
    assert result.url == "https://www.cian.ru/sale/flat/331002424/"
    assert result.meta.healthy is True


async def test_card_parses_a_rent_offer(monkeypatch):
    _patch_card(monkeypatch, (200, _card_body(CARD_RENT, "https://spb.cian.ru/rent/flat/317754437/"), "cdp"))

    result = await server.cian_card("https://spb.cian.ru/rent/flat/317754437/")

    assert result.offer_id == 317754437
    assert result.deal_type == "rent"
    assert result.price_rub == 60000
    assert result.price_period == "monthly"
    assert result.lease_term == "fewMonths"
    assert result.deposit_rub == 50000
    assert result.is_by_homeowner is False
    assert result.views == 3841
    assert result.agent is not None and result.agent.name == "SVET hotel"
    assert result.url == "https://spb.cian.ru/rent/flat/317754437/"


async def test_card_without_a_price_warns_instead_of_inventing_one(monkeypatch):
    fixture = copy.deepcopy(CARD_SALE)
    offer = fixture["offerData"]["offer"]
    offer["bargainTerms"].pop("price", None)
    offer.pop("priceTotalRur", None)
    offer.pop("priceTotal", None)
    _patch_card(monkeypatch, (200, _card_body(fixture), "cdp"))

    result = await server.cian_card("331002424")

    assert result.price_rub is None
    assert result.meta.healthy is False
    assert "price_missing" in result.meta.warnings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("331002424", 331002424),
        ("  331002424 ", 331002424),
        ("https://www.cian.ru/sale/flat/331002424/", 331002424),
        ("https://spb.cian.ru/rent/flat/317754437/?mlSearchSessionGuid=abc", 317754437),
        ("https://dedovsk.cian.ru/sale/suburban/332770439/", 332770439),
        ("https://www.cian.ru/sale/commercial/332617039", 332617039),
        ("123", None),
        ("flat 331002424", None),
        ("https://www.avito.ru/moskva/kvartiry/331002424", None),
        ("https://www.cian.ru/agents/134642318/", None),
    ],
)
def test_extract_offer_id(raw, expected):
    assert server._extract_offer_id(raw) == expected


async def test_card_rejects_input_without_an_id():
    with pytest.raises(ToolError) as excinfo:
        await server.cian_card("https://www.cian.ru/agents/134642318/")
    assert "bad_request" in str(excinfo.value)


async def test_card_maps_a_waf_block_to_transport_down(monkeypatch):
    _patch_card(monkeypatch, (403, "navigation blocked: HTTP 403", "cdp_blocked"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_card("331002424")

    assert "transport_down" in str(excinfo.value)


async def test_card_maps_a_removed_offer_to_not_found(monkeypatch):
    body = json.dumps(
        {"kind": "not_found", "title": "Объявление не найдено", "url": "https://www.cian.ru/sale/flat/1/"}
    )
    _patch_card(monkeypatch, (200, body, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_card("100000001")

    assert "not_found" in str(excinfo.value)


async def test_card_without_embedded_state_is_parser_drift(monkeypatch):
    body = json.dumps(
        {"kind": "no_offer", "title": "Продажа квартиры", "url": "https://www.cian.ru/sale/flat/1/", "keys": ["x"]}
    )
    _patch_card(monkeypatch, (200, body, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_card("331002424")

    assert "parser_drift" in str(excinfo.value)


async def test_card_with_an_error_title_and_no_state_is_a_block(monkeypatch):
    body = json.dumps({"kind": "no_config", "title": "Ошибка - Циан", "url": "https://www.cian.ru/sale/flat/1/"})
    _patch_card(monkeypatch, (200, body, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.cian_card("331002424")

    assert "transport_down" in str(excinfo.value)


# ---------------------------------------------------------------- cache ----


async def test_search_serves_a_repeat_query_from_cache(monkeypatch):
    calls: list = []

    async def fake_post(query, ctx):
        calls.append(query)
        return 200, json.dumps(SEARCH_SALE, ensure_ascii=False)

    monkeypatch.setattr(server, "_cdp_post_json", fake_post)

    async def no_wait():
        return None

    monkeypatch.setattr(server, "_polite_wait", no_wait)

    first = await server.cian_search("sale", rooms=[1])
    second = await server.cian_search("sale", rooms=[1])

    assert len(calls) == 1
    assert first.tier_used == "cdp"
    assert second.tier_used == "cache"
    assert second.items[0].offer_id == first.items[0].offer_id


async def test_a_block_page_is_never_cached(monkeypatch):
    calls: list = []

    async def fake_post(query, ctx):
        calls.append(query)
        return 403, WAF_BODY

    monkeypatch.setattr(server, "_cdp_post_json", fake_post)

    async def no_wait():
        return None

    monkeypatch.setattr(server, "_polite_wait", no_wait)

    for _ in range(2):
        with pytest.raises(ToolError):
            await server.cian_search("sale")

    assert len(calls) == 2


# ------------------------------------------------------------ selfcheck ----


async def test_selfcheck_is_success_when_search_and_card_parse(monkeypatch):
    card_calls: list = []
    _patch_search(monkeypatch, _ok(SEARCH_SALE))
    _patch_card(monkeypatch, (200, _card_body(CARD_SALE), "cdp"), card_calls)

    result = await server.cian_selfcheck()

    assert result.status == "success"
    assert result.healthy is True
    assert result.checks["search"].state == "healthy"
    assert result.checks["card"].state == "healthy"
    # The card probe used the id the search probe supplied, not a hardcoded one.
    assert card_calls == [331002424]


async def test_selfcheck_is_inconclusive_on_a_block_never_drift(monkeypatch):
    _patch_search(monkeypatch, (403, WAF_BODY, "cdp_blocked"))
    _patch_card(monkeypatch, (403, WAF_BODY, "cdp_blocked"))

    result = await server.cian_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    assert result.checks["search"].state == "inconclusive"
    assert result.checks["search"].reason == "blocked"
    assert result.checks["card"].state == "inconclusive"


async def test_selfcheck_reports_drift_when_the_envelope_changed(monkeypatch):
    _patch_search(monkeypatch, _ok({"status": "ok", "data": {"offers": []}}))

    result = await server.cian_selfcheck()

    assert result.status == "drift_detected"
    assert result.healthy is False
    assert result.checks["search"].state == "drift"
    assert result.checks["card"].state == "inconclusive"


async def test_selfcheck_reports_drift_when_the_card_lost_its_state(monkeypatch):
    _patch_search(monkeypatch, _ok(SEARCH_SALE))
    body = json.dumps(
        {"kind": "no_offer", "title": "Продажа квартиры", "url": "https://www.cian.ru/sale/flat/1/", "keys": []}
    )
    _patch_card(monkeypatch, (200, body, "cdp"))

    result = await server.cian_selfcheck()

    assert result.status == "drift_detected"
    assert result.checks["search"].state == "healthy"
    assert result.checks["card"].state == "drift"


# --------------------------------------------------------- daily rent ----


def test_daily_queries_flip_the_for_day_flag_and_keep_the_rent_family():
    """Daily and long-term are the same _type with opposite for_day values;
    omitting the key would mix two markets that are not comparable."""
    long_term = server._build_json_query(
        deal="rent",
        offer_type="flat",
        region="1",
        rooms=None,
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )
    daily = server._build_json_query(
        deal="daily",
        offer_type="flat",
        region="1",
        rooms=[2],
        price_min=None,
        price_max=4000,
        area_min=None,
        area_max=None,
        page=1,
    )

    assert long_term["for_day"] == {"type": "term", "value": "!1"}
    assert daily["_type"] == "flatrent"
    assert daily["for_day"] == {"type": "term", "value": "1"}
    # Every other filter keeps working on the daily market (verified live).
    assert daily["room"] == {"type": "terms", "value": [2]}
    assert daily["price"] == {"type": "range", "value": {"lte": 4000}}


def test_daily_rooms_and_houses_keep_their_own_families():
    room = server._build_json_query(
        deal="daily",
        offer_type="room",
        region="1",
        rooms=None,
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )
    house = server._build_json_query(
        deal="daily",
        offer_type="house",
        region="1",
        rooms=None,
        price_min=None,
        price_max=None,
        area_min=None,
        area_max=None,
        page=1,
    )

    assert room["_type"] == "flatrent" and room["room"] == {"type": "terms", "value": [0]}
    assert house["_type"] == "suburbanrent"
    assert room["for_day"] == house["for_day"] == {"type": "term", "value": "1"}


async def test_daily_commercial_is_refused_by_name_not_by_an_empty_page():
    """Cian accepts the query and answers zero offers, which would read as
    'nothing available today' instead of 'this market does not exist'."""
    with pytest.raises(ToolError) as excinfo:
        await server.cian_search("daily", offer_type="commercial")

    assert "bad_request" in str(excinfo.value)
    assert "daily" in str(excinfo.value).lower()


async def test_daily_search_prices_are_per_night_and_say_so(monkeypatch):
    calls: list = []
    _patch_search(monkeypatch, _ok(SEARCH_DAILY), calls)

    result = await server.cian_search("daily", region="1")

    assert result.deal == "daily"
    assert calls[0]["for_day"] == {"type": "term", "value": "1"}
    first = result.items[0]
    assert first.category == "dailyFlatRent"
    assert first.deal_type == "rent"
    # A daily offer carries no bargainTerms.priceRur at all — only .price.
    assert first.price_rub is not None and first.price_rub > 0
    assert first.price_unit == "day"
    # Cian leaves both of these null on daily offers; price_unit is the only signal.
    assert first.price_period is None
    assert first.lease_term is None
    assert all(item.price_unit == "day" for item in result.items)


async def test_daily_card_reads_the_nightly_price(monkeypatch):
    _patch_card(monkeypatch, (200, _card_body(CARD_DAILY, "https://www.cian.ru/rent/flat/191071633/"), "cdp"))

    result = await server.cian_card("191071633")

    assert result.offer_id == 191071633
    assert result.category == "dailyFlatRent"
    assert result.price_rub == 5000
    assert result.price_unit == "day"
    assert result.price_period is None and result.lease_term is None
    assert result.meta.healthy is True


@pytest.mark.parametrize(
    ("category", "deal_type", "period", "expected"),
    [
        ("flatSale", "sale", None, "total"),
        ("newBuildingFlatSale", "sale", None, "total"),
        ("flatRent", "rent", "monthly", "month"),
        ("roomRent", "rent", None, "month"),
        ("dailyFlatRent", "rent", None, "day"),
        ("dailyRoomRent", "rent", None, "day"),
        ("dailyHouseRent", "rent", None, "day"),
        # An unfamiliar period is passed through rather than flattened to a
        # month the connector never saw.
        ("flatRent", "rent", "weekly", "weekly"),
        (None, None, None, None),
    ],
)
def test_price_unit_derivation(category, deal_type, period, expected):
    assert server._price_unit(category, deal_type, period) == expected


# ------------------------------------------------------------ inventory ----


async def test_the_server_exposes_exactly_the_two_read_tools():
    tools = sorted(t.name for t in await server.mcp.list_tools())

    assert tools == ["cian_card", "cian_search"]
