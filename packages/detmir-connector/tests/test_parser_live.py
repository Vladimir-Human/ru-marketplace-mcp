"""The Detsky Mir parser against LIVE captured API bodies.

``fixtures/card_live.json`` and ``fixtures/category_live.json`` are the real
api.detmir.ru responses captured 2026-08-07 with the connector's own client
(provenance in the matching ``*.provenance.json``), trimmed to what the parser
reads plus light unread keys. Before these fixtures existed the parser ran
only on invented payloads — this pins it against the bytes the API actually
serves.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from detmir_connector import server

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_live_card_parses_to_the_displayed_values() -> None:
    node = server._product_node(_load("card_live.json"))
    assert node is not None
    product = server._parse_product(node)
    assert product.product_id == 6673568
    assert product.title == "Кукла пупс Demi Star в розовом комбинезоне"
    assert product.price_rub == 699.0
    assert product.old_price_rub is None
    assert product.available_online is True
    assert product.url == "https://www.detmir.ru/product/index/id/6673568/"


def test_live_category_parses_its_products_and_meta() -> None:
    payload = _load("category_live.json")
    items_raw = server._as_list(payload.get("items"))
    products = [server._parse_product(item) for item in items_raw]
    assert [(p.title, p.price_rub) for p in products] == [
        ("Кукла пупс Demi Star высота 35 см", 2499.0),
        ("Кукла пупс CRY BABIES Кэти", 4999.0),
        ("Кукла пупс Demi Star с аксессуарами высота 35 см", 2499.0),
    ]
    meta = server._as_dict(payload.get("meta"))
    assert meta.get("title") == "Пупсы"
    assert server.R.coerce_int(server.R.first_present(meta, "length", "total", default=None)) == 665


def test_live_prices_are_finite_positive_rubles() -> None:
    """The doctrine pinned by the audit waves, checked against live bytes: a
    Detsky Mir price is a finite positive float or None, never 0/inf/nan."""
    payloads = []
    card_node = server._product_node(_load("card_live.json"))
    assert card_node is not None
    payloads.append(server._parse_product(card_node))
    for item in server._as_list(_load("category_live.json").get("items")):
        payloads.append(server._parse_product(item))
    for product in payloads:
        for price in (product.price_rub, product.old_price_rub):
            if price is not None:
                assert price > 0 and math.isfinite(price)
