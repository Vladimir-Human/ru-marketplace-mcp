"""Closed and gone Avito ads: not_found with Avito's own closure text, decided by service data.

Fixtures are trimmed, de-identified live captures of 2026-11-06 (provenance
sidecars): ``item_closed_*`` is a withdrawn ad its owner reports as sold
(«Объявление снято с публикации.»), ``item_gone_*`` an ad gone within a day
(«Объявление закрыто.»). The open no-JSON-LD page in the last tests is the
active capture with its schema.org Product removed in code — fresh captures of
2026-11-06 carry no Product at all, so the DOM path is the common one.
"""

from __future__ import annotations

import json
import re
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


def _serve_page(monkeypatch, html: str) -> None:
    async def fake_fetch_item_page(url, ctx):
        return 200, html, "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


async def _error(monkeypatch, html: str, item_id: str) -> dict:
    _serve_page(monkeypatch, html)
    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(item_id)
    return json.loads(str(excinfo.value))


async def test_a_gone_ad_page_is_not_found_not_parser_drift(monkeypatch):
    """«Объявление закрыто.» (loader type notFound) used to be parser_drift: no Product, no DOM item id."""
    error = await _error(monkeypatch, _page("item_gone_9000000032.html"), "9000000032")

    assert error["error"] == "not_found"
    assert "Объявление закрыто." in error["message"]
    assert error["retryable"] is False


async def test_a_closed_ad_page_is_not_found_with_avitos_closure_text(monkeypatch):
    error = await _error(monkeypatch, _page("item_closed_9000000031.html"), "9000000031")

    assert error["error"] == "not_found"
    assert "Объявление снято с публикации." in error["message"]
    assert "sold" not in error["message"].lower()


def test_closed_state_reads_service_data_only():
    assert item_state.closed_state(_page("item_closed_9000000031.html"), 9000000031) == (
        True,
        "Объявление снято с публикации.",
    )
    assert item_state.closed_state(_page("item_gone_9000000032.html"), 9000000032) == (True, "Объявление закрыто.")
    assert item_state.closed_state(_page("item_state_active_9000000012.html"), 9000000012) == (True, None)
    assert item_state.closed_state("<html><body>Объявление не найдено</body></html>", 1) == (False, None)
    # An open buyerItem for the requested id wins over an auxiliary sub-loader notFound entry.
    sub_not_found = json.dumps(
        {
            "loaderData": {
                "sub": {"type": "notFound", "message": "Объявление закрыто."},
                "main": {"type": "buyer", "buyerItem": {"item": {"id": 42, "isActive": True}}},
            }
        }
    )
    page_with_sub_404 = (
        f"<script>window.__staticRouterHydrationData = JSON.parse({json.dumps(sub_not_found)});</script>"
    )
    assert item_state.closed_state(page_with_sub_404, 42) == (True, None)
    # isActive=false without closedItem/isClosed is not treated as known-open.
    inactive_only = json.dumps({"loaderData": {"main": {"buyerItem": {"item": {"id": 42, "isActive": False}}}}})
    page_inactive = f"<script>window.__staticRouterHydrationData = JSON.parse({json.dumps(inactive_only)});</script>"
    assert item_state.closed_state(page_inactive, 42) == (False, None)


def test_a_closed_state_of_another_item_is_not_this_ads():
    """Another id's closed buyerItem and its banner are not attributed to the requested ad.

    v2.9.0 returned (False, the banner text), and avito_card answered not_found for the requested id.
    """
    assert item_state.closed_state(_page("item_closed_9000000031.html"), 9000000099) == (True, None)
    # A partial buyerItem without an integer item.id is not mistaken for another item's page.
    idless = json.dumps({"loaderData": {"main": {"buyerItem": {"closedItem": True, "item": None}}}})
    banner = '<div data-marker="item-view/closed-warning">Объявление снято с публикации.</div>'
    page_idless = f"{banner}<script>window.__staticRouterHydrationData = JSON.parse({json.dumps(idless)});</script>"
    assert item_state.closed_state(page_idless, 9000000099) == (False, "Объявление снято с публикации.")
    # Direct card_availability on a closed page does not claim contradicting signals when none are open.
    status, evidence = item_state.card_availability(_page("item_closed_9000000031.html"), 9000000031)
    assert status == "unknown"
    assert "contradicting status signals" not in evidence


async def test_another_items_closed_page_is_an_identity_mismatch_not_a_closure(monkeypatch):
    """main read «снято с публикации» in the visible text and answered not_found for the requested id."""
    error = await _error(monkeypatch, _page("item_closed_9000000031.html"), "9000000099")

    assert error["error"] == "parser_drift"
    assert "снято" not in error["message"]


def _state_edited(name: str, edit) -> str:
    """A capture with its page state edited in code (unit edge cases only)."""
    html = _page(name)
    match = re.search(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)', html)
    assert match
    state = json.loads(json.loads('"' + match.group(1) + '"'))
    edit(state["loaderData"]["catalog-or-main-or-item"]["buyerItem"])
    encoded = json.dumps(json.dumps(state, ensure_ascii=False), ensure_ascii=False)[1:-1]
    return html[: match.start(1)] + encoded + html[match.end(1) :]


def test_closure_flags_against_an_active_flag_are_not_a_closure():
    def reopen(bi):
        bi["item"]["isActive"] = True

    assert item_state.closed_state(_state_edited("item_closed_9000000031.html", reopen), 9000000031) == (True, None)


async def test_an_open_card_with_closure_flags_is_unknown_with_both_sides(monkeypatch):
    def close(bi):
        bi["closedItem"] = True
        bi["closedItemStatus"] = "Объявление снято с публикации."

    _serve_page(monkeypatch, _state_edited("item_state_active_9000000012.html", close))

    result = await server.avito_card("9000000012")

    assert result.availability == "unknown"
    assert "buyerItem.closedItem=true" in result.availability_evidence
    assert "item.isActive=true" in result.availability_evidence


async def test_a_closure_banner_on_an_open_page_is_unknown_not_not_found(monkeypatch):
    banner = '<div data-marker="item-view/closed-warning"><p>Объявление снято с публикации.</p></div>'
    html = _page("item_state_active_9000000012.html").replace("</h1>", "</h1>" + banner, 1)
    _serve_page(monkeypatch, html)

    result = await server.avito_card("9000000012")

    assert result.availability == "unknown"
    assert any("closed-warning" in entry for entry in result.availability_evidence)


def _open_page_without_product(description: str) -> str:
    """The active capture without its schema.org Product, with the item-view DOM the card reads."""
    html = re.sub(r'<script type="application/ld\+json">.*?</script>', "", _page("item_state_active_9000000012.html"))
    dom = (
        '<span data-marker="item-view/item-id">№ 9000000012</span>'
        '<span data-marker="item-view/item-price">2 790 ₽</span>'
        f'<div data-marker="item-view/item-description">{description}</div>'
    )
    return html.replace("</h1>", "</h1>" + dom, 1)


@pytest.mark.parametrize(
    "description",
    [
        "Объявление снято с публикации? Нет, актуально.",
        "Страница не найдена у конкурентов — у нас всё есть. Ошибка 404 не про нас.",
    ],
)
async def test_seller_text_never_closes_an_open_ad(monkeypatch, description):
    """Without a Product the visible text was searched for closure words — the seller's description included."""
    _serve_page(monkeypatch, _open_page_without_product(description))

    result = await server.avito_card("9000000012")

    assert result.title == "ТВ-приставка Xiaomi TV Box S 2nd Gen"
    assert result.price_rub == 2790
    assert result.availability == "active"


async def test_a_page_without_any_state_still_falls_back_to_the_visible_text(monkeypatch):
    error = await _error(monkeypatch, "<html><body><h1>Объявление не найдено</h1></body></html>", "9000000016")

    assert error["error"] == "not_found"
