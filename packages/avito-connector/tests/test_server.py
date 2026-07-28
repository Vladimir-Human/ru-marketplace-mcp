"""Offline tests for the Avito connector.

Every upstream call is monkeypatched, so the suite runs with no network and no
geo dependency. Fixtures mirror the js/items envelope shape documented by
third-party parsers and probed in July 2026 — including the failure modes that
make this endpoint easy to parse wrongly (a 403 that looks like content, a
listing with no price).
"""

from __future__ import annotations

import json

import pytest
from avito_connector import server
from fastmcp.exceptions import ToolError

# ---------------------------------------------------------------- fixtures ----

SEARCH_PAYLOAD = {
    "totalCount": 137,
    "items": [
        {
            "id": 4612345678,
            "title": "Ноутбук ThinkPad X1 Carbon",
            "price": 45500,
            "uri": "/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
            "location": "Москва",
            "seller": {"name": "ИП Сидоров", "id": 987654, "isCompany": True},
            "date": "сегодня, 12:40",
            "images": [{"url": "https://img.avito.st/1.jpg"}, {"url": "https://img.avito.st/2.jpg"}],
        },
        {
            "id": 4612349999,
            "title": "Отдам даром клавиатуру",
            "uri": "/moskva/klaviatury/klaviatura_4612349999",
            "location": "Москва",
            "seller": {"name": "Анна"},
            "images": [],
        },
    ],
}

CARD_PAYLOAD = {
    "item": {
        "id": 4612345678,
        "title": "Ноутбук ThinkPad X1 Carbon",
        "price": 45500,
        "description": "В хорошем состоянии, батарея держит 6 часов.",
        "location": "Москва, р-н Хамовники",
        "date": "сегодня, 12:40",
        "views": 231,
        "images": [{"url": "https://img.avito.st/1.jpg"}],
        "seller": {
            "name": "ИП Сидоров",
            "id": 987654,
            "isCompany": True,
            "ratingScore": 4.8,
            "ratingCount": 152,
            "profileUrl": "/user/987654/profile",
        },
        "uri": "/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
    }
}

SELLER_PAYLOAD = {
    "totalCount": 12,
    "seller": {
        "name": "ИП Сидоров",
        "isCompany": True,
        "ratingScore": 4.8,
        "ratingCount": 152,
        "profileUrl": "/user/987654/profile",
    },
    "items": [],
}

FIREWALL_BODY = '{"too-many-requests":{"link":"ru.avito://1/firewall/captcha/show","message":"Доступ с вашего IP-адреса временно ограничен"}}'


def _ok(payload: dict):
    return 200, json.dumps(payload, ensure_ascii=False), "curl_cffi"


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """Every test starts with an empty cache: a cached body from a previous
    case would otherwise shadow its monkeypatched fetch."""
    server._cache._data.clear()


def _patch_fetch(monkeypatch, result):
    async def fake_fetch(url, ctx):
        return result

    monkeypatch.setattr(server, "_fetch", fake_fetch)


# --------------------------------------------------------------- avito_search ----


async def test_search_parses_items_and_total(monkeypatch):
    _patch_fetch(monkeypatch, _ok(SEARCH_PAYLOAD))

    result = await server.avito_search("thinkpad", page=1)

    assert result.status == "success"
    assert result.count == 2
    assert result.total_count == 137
    first = result.items[0]
    assert first.item_id == 4612345678
    assert first.price_rub == 45500
    assert first.url == "https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678"
    assert first.seller_name == "ИП Сидоров"
    assert first.images == 2


async def test_search_a_pricelss_listing_is_none_never_zero(monkeypatch):
    """The free keyboard carries no price field at all; it must surface as None,
    because a 0 would rank it cheapest in compare_prices."""
    _patch_fetch(monkeypatch, _ok(SEARCH_PAYLOAD))

    result = await server.avito_search("клавиатура")

    priceless = result.items[1]
    assert priceless.price_rub is None
    assert priceless.price_rub != 0


@pytest.mark.parametrize("bad_loc", ["", "moscow", "63 7640", "637640;rm -rf", "1"])
async def test_search_rejects_a_malformed_location_id(bad_loc):
    with pytest.raises(ToolError):
        await server.avito_search("ноутбук", location_id=bad_loc)


async def test_search_rejects_a_non_digit_category_id():
    with pytest.raises(ToolError):
        await server.avito_search("ноутбук", category_id="electronics")


async def test_search_maps_a_firewall_block_to_transport_down(monkeypatch):
    _patch_fetch(monkeypatch, (403, FIREWALL_BODY, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_search("ноутбук")

    assert "firewall" in str(excinfo.value).lower() or "block" in str(excinfo.value).lower()


async def test_search_maps_non_json_to_parser_drift(monkeypatch):
    """A 200 carrying the HTML block page instead of JSON is drift, not success."""
    _patch_fetch(monkeypatch, (200, "<html><body>Доступ ограничен</body></html>", "cdp"))

    with pytest.raises(ToolError):
        await server.avito_search("ноутбук")


async def test_search_warns_on_empty_items_with_nonzero_total(monkeypatch):
    payload = {"totalCount": 41, "items": []}
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_search("редкая вещь")

    assert result.count == 0
    assert result.meta.healthy is False
    assert "empty_items_with_nonzero_total" in result.meta.warnings


# ----------------------------------------------------------------- avito_card ----


async def test_card_parses_the_item_envelope(monkeypatch):
    _patch_fetch(monkeypatch, _ok(CARD_PAYLOAD))

    result = await server.avito_card("4612345678")

    assert result.item_id == 4612345678
    assert result.price_rub == 45500
    assert result.views == 231
    assert result.seller is not None
    assert result.seller.rating_score == 4.8
    assert result.seller.is_company is True


async def test_card_accepts_a_slug_url(monkeypatch):
    _patch_fetch(monkeypatch, _ok(CARD_PAYLOAD))

    result = await server.avito_card("https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678")

    assert result.item_id == 4612345678


@pytest.mark.parametrize(
    "bad",
    ["", "not-an-item", "https://example.com/1234567890", "123", "avito.ru/moskva"],
)
async def test_card_rejects_input_without_an_item_id(bad):
    with pytest.raises(ToolError):
        await server.avito_card(bad)


async def test_card_maps_404_to_not_found(monkeypatch):
    _patch_fetch(monkeypatch, (404, "{}", "curl_cffi"))

    with pytest.raises(ToolError):
        await server.avito_card("4612345678")


# --------------------------------------------------------------- avito_seller ----


async def test_seller_parses_reputation_and_active_count(monkeypatch):
    _patch_fetch(monkeypatch, _ok(SELLER_PAYLOAD))

    result = await server.avito_seller("987654")

    assert result.seller is not None
    assert result.seller.rating_score == 4.8
    assert result.seller.rating_count == 152
    assert result.active_items == 12


async def test_seller_warns_when_identity_is_missing(monkeypatch):
    _patch_fetch(monkeypatch, _ok({"totalCount": 3, "items": []}))

    result = await server.avito_seller("987654")

    assert "seller_identity_missing" in result.meta.warnings


# ------------------------------------------------------------ avito_selfcheck ----


async def test_selfcheck_reports_healthy_when_probes_parse(monkeypatch):
    def fake_fetch_factory(payload):
        async def fake_fetch(url, ctx):
            return _ok(payload)

        return fake_fetch

    async def fake_fetch(url, ctx):
        if "userId" in url:
            return _ok(SELLER_PAYLOAD)
        return _ok(SEARCH_PAYLOAD)

    monkeypatch.setattr(server, "_fetch", fake_fetch)

    result = await server.avito_selfcheck()

    assert result.status == "success"
    assert result.healthy is True
    assert result.checks["search"].state == "healthy"


async def test_selfcheck_maps_a_block_to_inconclusive_never_drift(monkeypatch):
    """The firewall 403 is the expected datacenter state — it must not be read
    as parser drift."""
    _patch_fetch(monkeypatch, (403, FIREWALL_BODY, "curl_cffi"))

    result = await server.avito_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    assert all(c.state == "inconclusive" for c in result.checks.values())


async def test_selfcheck_flags_drift_when_a_200_fails_the_parse_smoke(monkeypatch):
    _patch_fetch(monkeypatch, _ok({"unexpected": "envelope"}))

    result = await server.avito_selfcheck()

    assert result.status == "drift_detected"
    assert result.healthy is False


# ------------------------------------------------------------------- helpers ----


def test_extract_item_id_handles_every_accepted_shape():
    assert server._extract_item_id("4612345678") == 4612345678
    assert server._extract_item_id("/moskva/noutbuki/x_4612345678") == 4612345678
    assert server._extract_item_id("https://www.avito.ru/moskva/x_4612345678?context=1") == 4612345678
    assert server._extract_item_id("ноутбук") is None
    assert server._extract_item_id("123") is None


def test_search_url_carries_query_page_location():
    url = server._build_search_url("ноутбук сsd", 2, "637640", "4")
    assert "q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA" in url or "q=" in url
    assert "p=2" in url
    assert "locationId=637640" in url
    assert "categoryId=4" in url
    assert url.startswith("https://www.avito.ru/web/1/js/items?")


# ------------------------------------------------------------ refusal backoff ----
#
# Avito bans by IP reputation and request rate, so a refusal must slow the next
# call down and a run of them must tell the operator what to change. Verified
# live July 2026: a burst of selfcheck calls turned a working source into an
# HTTP 439/429-class refusal.


def test_a_block_is_reported_to_the_pacer(monkeypatch):
    server._pacer.reset()

    with pytest.raises(ToolError):
        server._raise_for_fetch_failure(429, "too-many-requests", "curl_cffi", "search")

    assert server._pacer.consecutive_refusals == 1


def test_a_not_found_is_not_counted_as_a_refusal():
    """404 means the ad is gone. Slowing down does not bring it back."""
    server._pacer.reset()

    with pytest.raises(ToolError):
        server._raise_for_fetch_failure(404, "", "curl_cffi", "card")

    assert server._pacer.consecutive_refusals == 0


def test_a_standing_block_tells_the_operator_what_to_change():
    server._pacer.reset()
    for _ in range(server._pacer.rotation_threshold):
        server._pacer.record_refusal()

    message = str(server._blocked_error("curl_cffi"))

    assert "standing block" in message
    assert "residential" in message
    server._pacer.reset()


def test_a_single_block_does_not_cry_wolf():
    server._pacer.reset()
    server._pacer.record_refusal()

    assert "standing block" not in str(server._blocked_error("curl_cffi"))
    server._pacer.reset()
