"""R-ITEM-HONESTY wiring tests for the Cian connector tools.

Commit fe7efcc wired ``mcp_core.resilience.validate_items`` into ``cian_search``
and ``validate_offer`` into ``cian_card``, so per-offer validation reaches the
agent through every connector tool and flips ``_meta.healthy`` to False when an
offer stops being an offer. These tests prove that wiring through the public
tool entries only; the shared validator itself is pinned in
``packages/mcp-core/tests/test_resilience.py``.

Every upstream call is monkeypatched at the transport dispatch layer
(``_fetch_search`` / ``_fetch_card``), the same stubbing as ``test_server.py``,
so the suite runs with no network and no browser. The drift payloads are
minimal offer dicts shaped like the captured fixtures, minus every title source
and price key — the shape a page takes when those selectors drift.
"""

from __future__ import annotations

import json

from cian_connector import server


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


def _titleless_offer(offer_id: int) -> dict:
    """A serialized offer that parses but yields no title and no price.

    It is the captured fixtures' offer skeleton minus every title source — no
    ``title``, no ``formattedShortInfo``, and none of the composable bits
    (roomsCount, flatType, totalArea, floorNumber) ``_compose_title`` falls
    back to — and minus every price key ``_price_of`` reads, so the row comes
    back with title=None and price_rub=None instead of invented values.
    """
    return {
        "id": offer_id,
        "dealType": "sale",
        "category": "flatSale",
        "fullUrl": f"https://www.cian.ru/sale/flat/{offer_id}/",
        "geo": {"userInput": "Москва"},
        "bargainTerms": {},
    }


# ---------------------------------------------------------- cian_search ----


async def test_search_flags_offers_that_lost_their_titles_and_prices(monkeypatch):
    """R-ITEM-HONESTY: a page whose every offer lost both its title sources and
    its price must answer unhealthy, not as a clean page of anonymous free flats.

    ``validate_items`` aggregates the per-offer ``title_missing`` code into
    ``title_missing: 2/2 items affected (likely schema drift)``; a price_rub of
    None alone stays silent on Cian rows (no in_stock or rating to contradict
    it), so the title code is the one alarm this page raises. On the pre-change
    cian_search (HEAD~1) the rows were non-empty, so ``empty_items_with_nonzero_total``
    did not fire either and the code was only ``warnings: list[str] = []`` with
    no validate_items call: warnings stayed empty and healthy stayed True, so
    BOTH the healthy and the title_missing assertions fail there
    (audit 2026-11-01).
    """
    payload = {
        "status": "ok",
        "data": {
            "offersSerialized": [_titleless_offer(331002424), _titleless_offer(333761512)],
            "aggregatedCount": 2,
        },
    }
    _patch_search(monkeypatch, _ok(payload))

    result = await server.cian_search("sale", offer_type="flat", region="1")

    assert result.count == 2
    assert result.meta.healthy is False
    assert any(w.startswith("title_missing: 2/2") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_rub is None


async def test_search_a_priced_page_with_titles_stays_healthy(monkeypatch):
    """Guard: the wiring must not fire on a page that parsed cleanly — offers
    that kept their titles and bargainTerms prices answer healthy with no
    title_missing warning, so the drift tests above are not proving a validator
    that flags everything."""
    offers = [
        {**_titleless_offer(331002424), "title": "1-комн. квартира, 38.1 м²", "bargainTerms": {"priceRur": 11985439}},
        {
            **_titleless_offer(333761512),
            "title": "однушка в Северном Измайлово",
            "bargainTerms": {"priceRur": 11900000},
        },
    ]
    _patch_search(monkeypatch, _ok({"status": "ok", "data": {"offersSerialized": offers, "aggregatedCount": 2}}))

    result = await server.cian_search("sale", offer_type="flat", region="1")

    assert result.meta.healthy is True
    assert not any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings
    assert result.items[0].price_rub is not None


# ------------------------------------------------------------ cian_card ----


async def test_card_warns_when_the_offer_lost_its_title(monkeypatch):
    """R-ITEM-HONESTY: a card whose offer parses with neither a price nor any
    title source must carry both warnings, not just the old price one.

    ``price_missing`` predates the change (the card branch appended it whenever
    row["price_rub"] is None); ``validate_offer``'s ``title_missing`` code is
    what fe7efcc added. On the pre-change cian_card (HEAD~1) the warnings list
    held only "price_missing" — healthy was already False through it, so the
    title_missing assertion is the one that fails there (audit 2026-11-01).
    """
    offer_data = {
        "offer": {
            "id": 331002424,
            "dealType": "sale",
            "category": "flatSale",
            "fullUrl": "https://www.cian.ru/sale/flat/331002424/",
            "geo": {"userInput": "Москва"},
            "description": "Продаётся однокомнатная квартира.",
            "bargainTerms": {},
        },
    }
    body = json.dumps(
        {
            "kind": "ok",
            "title": "Продажа квартиры",
            "url": "https://www.cian.ru/sale/flat/331002424/",
            "offerData": offer_data,
        },
        ensure_ascii=False,
    )
    _patch_card(monkeypatch, (200, body, "cdp"))

    result = await server.cian_card("331002424")

    assert result.title is None
    assert result.price_rub is None
    assert result.meta.healthy is False
    assert any(w.startswith("title_missing") for w in result.meta.warnings), result.meta.warnings
    assert any(w == "price_missing" for w in result.meta.warnings), result.meta.warnings


async def test_search_flags_a_page_whose_every_price_died(monkeypatch):
    """Titled offers with no price key left: the price selector drifted.

    Fails on the code before the absent-price wiring: titled rows raise no
    per-item code, so warnings stayed empty and healthy True.
    """
    offers = [{**_titleless_offer(offer_id), "title": "2-комн. квартира"} for offer_id in (331002424, 333761512)]
    payload = {"status": "ok", "data": {"offersSerialized": offers, "aggregatedCount": 2}}
    _patch_search(monkeypatch, _ok(payload))

    result = await server.cian_search("sale", offer_type="flat", region="1")

    assert result.items[0].title
    assert result.meta.healthy is False
    assert "no_prices_on_page" in result.meta.warnings
