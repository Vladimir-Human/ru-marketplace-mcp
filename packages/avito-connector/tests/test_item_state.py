"""Avito availability: reserved listings are not passed off as on sale, and seller text never decides.

Fixtures: ``item_state_reserved_*`` and ``item_state_active_*`` are trimmed,
de-identified captures of real item pages (2026-11-06; the active one was
re-checked against a fresh capture of the same listing), and
``js_items_reserved.json`` is a trimmed, de-identified js/items capture.
Provenance sidecars say how. Edge cases (contradictions, seller text that
looks like a status, absent flags) are built in the tests below from those
captures or as plain dicts; none of them is a fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from avito_connector import item_state, server
from fastmcp.exceptions import ToolError

FIXTURES = Path(__file__).parent / "fixtures"
ACTIVE = "item_state_active_9000000012.html"
RESERVED = "item_state_reserved_9000000011.html"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()
    yield
    server._cache._data.clear()


def _serve_page(monkeypatch, html: str, status: int = 200) -> None:
    async def fake_fetch_item_page(url, ctx):
        return status, html, "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


def _with_title(html: str, old: str, new: str) -> str:
    """The captured page with a seller-written title swapped in (h1 and schema.org name)."""
    assert html.count(old) >= 2
    return html.replace(f">{old}<", f">{new}<").replace(f'"name": "{old}"', f'"name": "{new}"')


@pytest.mark.parametrize(
    ("fixture", "item_id", "expected"),
    [(RESERVED, 9000000011, "reserved"), (ACTIVE, 9000000012, "active")],
)
def test_card_availability_from_page_state(fixture, item_id, expected):
    availability, evidence = item_state.card_availability(_page(fixture), item_id)

    assert availability == expected
    assert evidence


def test_reserved_page_says_instock_in_json_ld_and_is_still_reserved():
    html = _page(RESERVED)
    assert "schema.org/InStock" in html

    availability, evidence = item_state.card_availability(html, 9000000011)

    assert availability == "reserved"
    assert evidence == [
        "item.deliveryInfo.isReserved=true",
        "safeDealDesktop.reserved=true",
        "safedeal-item-card: «Товар зарезервирован»",
    ]


@pytest.mark.parametrize("title", ["Не продано, торг", "Зарезервировано? нет, свободно", "Товар зарезервирован, шучу"])
async def test_a_seller_title_that_looks_like_a_status_does_not_decide(monkeypatch, title):
    """Review #194 P1: item-view/title-info is the seller's own h1; its words used to
    beat isActive=true/isReserved=false («Не продано» -> sold, «Зарезервировано» -> reserved)."""
    html = _with_title(_page(ACTIVE), "ТВ-приставка Xiaomi TV Box S 2nd Gen", title)
    _serve_page(monkeypatch, html)

    result = await server.avito_card("9000000012")

    assert result.title == title
    assert result.availability == "active"
    assert all(title not in entry for entry in result.availability_evidence)


def test_seller_text_in_the_contacts_block_does_not_decide():
    """The contacts block shows the seller's chosen name, so it is never read for the status."""
    html = _page(ACTIVE).replace(
        "<body>",
        '<body><div data-marker="item-view/item-view-contacts"><span>Товар зарезервирован</span>'
        "<span>Продано</span></div>",
    )

    assert item_state.card_availability(html, 9000000012)[0] == "active"


def test_a_reserved_banner_against_free_flags_is_unknown_with_both_sides():
    """Review #194: when the banner and the page flags disagree, say unknown and show both."""
    html = _page(ACTIVE).replace(
        "<span><span>Купить с доставкой</span></span>", "<span><span>Товар зарезервирован</span></span>"
    )

    availability, evidence = item_state.card_availability(html, 9000000012)

    assert availability == "unknown"
    assert "safedeal-item-card: «Товар зарезервирован»" in evidence
    assert "item.deliveryInfo.isReserved=false" in evidence
    assert "safeDealDesktop.reserved=false" in evidence


def test_reservation_flags_that_disagree_are_unknown():
    bi = {
        "safeDealDesktop": {"reserved": False},
        "item": {"id": 1, "isActive": True, "deliveryInfo": {"isReserved": True}},
    }

    availability, evidence = item_state.classify(bi, {})

    assert availability == "unknown"
    assert evidence == [
        "contradicting status signals",
        "item.deliveryInfo.isReserved=true",
        "safeDealDesktop.reserved=false",
    ]


@pytest.mark.parametrize("reservation_info", [{"some": "shape"}, "pending", True])
def test_reservation_info_alone_is_not_a_reservation(reservation_info):
    """Review #194 P3: a truthy reservationInfo of an uncaptured shape counted as reserved."""
    bi = {
        "reservationInfo": reservation_info,
        "item": {"id": 1, "isActive": True, "deliveryInfo": {"isReserved": False}},
    }

    assert item_state.classify(bi, {})[0] == "active"
    bi["item"]["deliveryInfo"] = {}
    assert item_state.classify(bi, {})[0] == "unknown"


@pytest.mark.parametrize("value", ["true", 1, {"value": True}])
def test_non_boolean_flags_are_no_signal(value):
    bi = {"item": {"id": 1, "isActive": True, "deliveryInfo": {"isReserved": value}}}

    assert item_state.classify(bi, {}) == ("unknown", ["no explicit status signal on the page"])


def test_page_state_of_another_item_is_ignored():
    """A recommendation's state must not decide the requested ad's status."""
    html = _page(RESERVED)

    assert item_state.buyer_item(item_state.load_hydration(html), 9000000099) is None
    availability, evidence = item_state.card_availability(html, 9000000099)
    # Only the service banner of this page remains.
    assert (availability, evidence) == ("reserved", ["safedeal-item-card: «Товар зарезервирован»"])


def test_status_words_inside_scripts_are_not_banners():
    html = (
        '<div data-marker="safedeal-item-card"><script>var t = "Товар зарезервирован";</script>'
        "<span>Купить с доставкой</span></div>"
    )
    assert item_state.marker_texts(html) == {"safedeal-item-card": "Купить с доставкой"}
    assert item_state.card_availability(html, None)[0] == "unknown"


def test_active_needs_an_explicit_reservation_flag():
    bi = {"item": {"id": 1, "isActive": True, "deliveryInfo": {}}}
    assert item_state.classify(bi, {})[0] == "unknown"
    bi["item"]["deliveryInfo"]["isReserved"] = False
    assert item_state.classify(bi, {})[0] == "active"
    bi["item"]["isActive"] = False
    assert item_state.classify(bi, {})[0] == "unknown"


async def test_card_reports_a_reserved_listing(monkeypatch):
    _serve_page(monkeypatch, _page(RESERVED))

    result = await server.avito_card("9000000011")

    assert result.availability == "reserved"
    assert result.title == "ТВ-приставка Xiaomi TV Box S"
    assert result.price_rub == 4500
    assert result.meta.healthy is True


async def test_card_reports_an_active_listing(monkeypatch):
    _serve_page(monkeypatch, _page(ACTIVE))

    result = await server.avito_card("9000000012")

    assert result.availability == "active"
    assert result.availability_evidence == [
        "item.isActive=true",
        "item.deliveryInfo.isReserved=false",
        "safeDealDesktop.reserved=false",
    ]


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><h1>Объявление не найдено</h1></body></html>",
        # Review #194 P1: «Продано» in recommendations or the footer is not a status.
        "<html><body><h1>Объявление не найдено</h1><div>Похожие: Продано 3 шт</div></body></html>",
        "<html><body><div>Объявление снято с публикации.</div><footer>Товар продан</footer></body></html>",
    ],
)
async def test_a_missing_or_removed_ad_stays_not_found(monkeypatch, html):
    """The contract of main: a missing or removed ad is not_found, never a healthy card."""
    _serve_page(monkeypatch, html)

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card("9000000016")

    assert json.loads(str(excinfo.value))["error"] == "not_found"


def _serve_search(monkeypatch) -> None:
    body = (FIXTURES / "js_items_reserved.json").read_text(encoding="utf-8")

    async def fake_fetch(url, ctx):
        return 200, body, "curl_cffi"

    monkeypatch.setattr(server, "_fetch", fake_fetch)


async def test_search_keeps_reserved_hits_by_default(monkeypatch):
    """Review #194 P2: reserved hits vanished by default with only a count left behind."""
    _serve_search(monkeypatch)

    result = await server.avito_search("xiaomi tv box s")

    assert [item.availability for item in result.items] == ["active", "reserved"]
    assert result.count == 2
    assert result.excluded_inactive == 0
    assert result.meta.warnings == []
    assert result.meta.healthy is True


async def test_search_drops_reserved_hits_on_request_and_says_so(monkeypatch):
    _serve_search(monkeypatch)

    result = await server.avito_search("xiaomi tv box s", include_inactive=False)

    assert [item.availability for item in result.items] == ["active"]
    assert result.count == 1
    assert result.excluded_inactive == 1
    assert result.total_count == 756
    assert result.meta.warnings == [
        "info: 1 reserved hit(s) dropped (include_inactive=false); total_count still counts them"
    ]
    assert result.meta.healthy is True


def test_a_search_hit_without_the_flag_is_unknown():
    assert item_state.search_availability({"id": 1}) == "unknown"
    assert item_state.search_availability({"id": 1, "isReserved": "true"}) == "unknown"
    assert item_state.search_availability({"id": 1, "isReserved": True}) == "reserved"
    assert item_state.search_availability({"id": 1, "isReserved": False}) == "active"
