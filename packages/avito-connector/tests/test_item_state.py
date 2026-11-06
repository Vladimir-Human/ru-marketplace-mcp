"""Avito availability: reserved, sold and closed listings are not passed off as on sale.

Fixtures: ``item_state_reserved_*`` and ``item_state_active_*`` are trimmed,
de-identified captures of real item pages (2026-11-06); the closed, sold,
unknown and removed variants are labelled synthetic edits of the active one;
``js_items_reserved.json`` is a trimmed, de-identified js/items capture.
Provenance sidecars say which is which.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from avito_connector import item_state, server
from fastmcp.exceptions import ToolError

FIXTURES = Path(__file__).parent / "fixtures"


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


@pytest.mark.parametrize(
    ("fixture", "item_id", "expected"),
    [
        ("item_state_reserved_9000000011.html", 9000000011, "reserved"),
        ("item_state_active_9000000012.html", 9000000012, "active"),
        ("item_state_closed_9000000013.html", 9000000013, "closed"),
        ("item_state_sold_9000000014.html", 9000000014, "sold"),
        ("item_state_unknown_9000000015.html", 9000000015, "unknown"),
    ],
)
def test_card_availability_from_page_state(fixture, item_id, expected):
    availability, evidence = item_state.card_availability(_page(fixture), item_id)

    assert availability == expected
    assert evidence


def test_reserved_page_says_instock_in_json_ld_and_is_still_reserved():
    html = _page("item_state_reserved_9000000011.html")
    assert "schema.org/InStock" in html

    availability, evidence = item_state.card_availability(html, 9000000011)

    assert availability == "reserved"
    assert "item.deliveryInfo.isReserved=true" in evidence
    assert "safeDealDesktop.reserved=true" in evidence
    assert any("Товар зарезервирован" in entry for entry in evidence)


def test_page_state_of_another_item_is_ignored():
    """A recommendation's state must not decide the requested ad's status."""
    availability, _ = item_state.card_availability(_page("item_state_reserved_9000000011.html"), 9000000099)

    # The banner on the page is still read; the reservation flags are not attributed.
    assert availability == "reserved"
    bi = item_state.buyer_item(item_state.load_hydration(_page("item_state_reserved_9000000011.html")), 9000000099)
    assert bi is None


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


async def test_card_reports_a_reserved_listing(monkeypatch):
    _serve_page(monkeypatch, _page("item_state_reserved_9000000011.html"))

    result = await server.avito_card("9000000011")

    assert result.availability == "reserved"
    assert result.title == "ТВ-приставка Xiaomi TV Box S"
    assert result.price_rub == 4500
    assert result.meta.healthy is True


async def test_card_reports_an_active_listing(monkeypatch):
    _serve_page(monkeypatch, _page("item_state_active_9000000012.html"))

    result = await server.avito_card("9000000012")

    assert result.availability == "active"
    assert result.availability_evidence == ["item.isActive=true", "item.deliveryInfo.isReserved=false"]


async def test_card_answers_closed_for_a_closed_ad_page(monkeypatch):
    _serve_page(monkeypatch, _page("item_state_removed_9000000016.html"))

    result = await server.avito_card("9000000016")

    assert result.availability == "closed"
    assert result.availability_evidence == ["page text: «Объявление снято с публикации»"]
    assert result.title is None
    assert result.price_rub is None
    assert result.url == "https://www.avito.ru/9000000016"
    assert result.meta.healthy is True
    assert result.meta.warnings[0].startswith("info:")


async def test_card_still_raises_not_found_for_a_missing_ad(monkeypatch):
    _serve_page(monkeypatch, "<html><body><h1>Объявление не найдено</h1></body></html>")

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card("9000000016")

    assert json.loads(str(excinfo.value))["error"] == "not_found"


def _serve_search(monkeypatch) -> None:
    body = (FIXTURES / "js_items_reserved.json").read_text(encoding="utf-8")

    async def fake_fetch(url, ctx):
        return 200, body, "curl_cffi"

    monkeypatch.setattr(server, "_fetch", fake_fetch)


async def test_search_drops_reserved_hits_by_default(monkeypatch):
    _serve_search(monkeypatch)

    result = await server.avito_search("xiaomi tv box s")

    assert [item.availability for item in result.items] == ["active", "unknown"]
    assert result.count == 2
    assert result.excluded_inactive == 1
    assert result.meta.healthy is True


async def test_search_keeps_reserved_hits_on_request(monkeypatch):
    _serve_search(monkeypatch)

    result = await server.avito_search("xiaomi tv box s", include_inactive=True)

    assert [item.availability for item in result.items] == ["active", "reserved", "unknown"]
    assert result.count == 3
    assert result.excluded_inactive == 0
