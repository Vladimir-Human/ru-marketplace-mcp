"""The Megamarket search parser against a LIVE captured payload.

``fixtures/search_live.json`` is the real catalogService/catalog/search
response for «стиральная машина» captured 2026-08-07 through the operator's
Chrome (provenance in ``search_live.provenance.json``), trimmed to the first
three nested goods/favoriteOffer items. Before this fixture existed the parser
ran only on invented payloads — and the live capture is the only thing that
exercises the shape that answers 200 with items after the two-step collection
resolution. This pins it against the bytes the API actually serves.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from megamarket_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_live.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_live_payload_keeps_its_container_and_total() -> None:
    """The parser's third return value separates "matched nothing" from "the
    shape moved" — on the live bytes the container is present and total is the
    captured 25 772."""
    items_raw, total, container_found = server._parse_items(_payload())
    assert container_found is True
    assert total == 25772
    assert len(items_raw) == 3


def test_live_items_parse_to_the_displayed_values() -> None:
    items_raw, _total, _cf = server._parse_items(_payload())
    assert [(it["item_id"], it["title"], it["price_rub"], it["is_available"]) for it in items_raw] == [
        (
            "100026555148",
            "Стиральная машина LG F10B8LD7 белый",
            29990.0,
            True,
        ),
        (
            "600010713443",
            "Стиральная машина Hotpoint-Ariston NSB 6039 K VE RU белый",
            29199.0,
            True,
        ),
        (
            "100061264823",
            "Стиральная машина Samsung WW65AG4S20CXLP серый",
            35180.0,
            True,
        ),
    ]


def test_live_prices_are_finite_positive_rubles() -> None:
    """The doctrine pinned by the audit waves, checked against live bytes."""
    items_raw, _total, _cf = server._parse_items(_payload())
    for it in items_raw:
        for price in (it.get("price_rub"), it.get("old_price_rub")):
            if price is not None:
                assert price > 0 and math.isfinite(price)
