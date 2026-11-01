"""Reference shape signature of the REAL Avito ``js/items`` payload.

``test_live_payload_contract.py`` pins what the parser must produce *from* the
captured response; this file pins the captured response itself — the input
contract — via ``resilience.shape_signature``. When Avito renames, retypes or
re-nests a field, the drift shows up here as a named path diff before it ever
reaches a silent-wrong page of listings.

The golden lives in ``avito_connector.shape_reference`` — the same registry
``avito_selfcheck`` diffs live payloads against — and this file asserts the
registry still agrees with the fixture, so it cannot go stale silently.
Measured on the committed fixture (captured 2026-07-28 from a residential
session, provenance in the fixture's contract test); regenerate only from a
fresh capture.

Pure Python: no Node, no network, always runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from avito_connector import shape_reference
from mcp_core.resilience import shape_signature

FIXTURE = Path(__file__).parent / "fixtures" / "js_items_live.json"

# Union shapes are expected where the capture itself varies: some items carry
# a location object while others carry null, and rating.score is int on some
# rows and float on others. That variation is data, not drift.


def test_live_payload_shape_matches_the_capture() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert shape_signature(payload) == list(shape_reference.SEARCH_SHAPE_REFERENCE)


def test_the_parser_bindings_survive_in_the_reference_shape() -> None:
    """The fields the parser actually binds to must stay in the reference.

    A fixture trimmed of a parser-critical key would otherwise pass the shape
    comparison (both sides computed from the same bytes) while teaching the
    suite that a missing field is normal. Pin the binding set by name.
    """
    from avito_connector.server import _parse_search_items

    raw, _total = _parse_search_items(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert raw, "the live fixture produced no items — the reference proves nothing"

    signature = set(shape_reference.SEARCH_SHAPE_REFERENCE)
    for path in (
        "catalog.items[].id:int",
        "catalog.items[].title:str",
        "catalog.items[].urlPath:str",
        "catalog.items[].priceDetailed.value:int",
        "catalog.items[].sortTimeStamp:int",
    ):
        assert path in signature, f"parser-critical path {path} vanished from the reference shape"


def test_missing_required_families_reports_only_absent_families() -> None:
    """A rename WITHIN an alias family is tolerated; the loss of a whole family
    is what the selfcheck must cry about."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert shape_reference.missing_required_families(shape_signature(payload)) == []

    for item in payload["catalog"]["items"]:
        item["idRenamed"] = item.pop("id")

    missing = shape_reference.missing_required_families(shape_signature(payload))
    # The registry grew the data.items[] aliases (2026-11-01 audit), so this
    # pins the whole id family as it now stands — the assertion is extended,
    # not relaxed: a rename inside the family must still keep it out of the
    # missing list.
    assert (
        "catalog.items[].id",
        "catalog.items[].itemId",
        "catalog.items[].item_id",
        "items[].id",
        "items[].itemId",
        "items[].item_id",
        "data.items[].id",
        "data.items[].itemId",
        "data.items[].item_id",
    ) in missing


def test_the_pre_2026_08_top_level_envelope_still_passes_the_families() -> None:
    """Avito moved listings into catalog.items[], but the parser binds both
    shapes and the canary must not cry drift when a client is served the old
    envelope — an A/B rollout must not flip it."""
    legacy_payload = {
        "count": 1,
        "items": [
            {"id": 1, "title": "Ноутбук", "priceDetailed": {"value": 59900}, "urlPath": "/x"},
        ],
    }

    assert shape_reference.missing_required_families(shape_signature(legacy_payload)) == []


def test_the_data_items_envelope_passes_the_families() -> None:
    """The third envelope ``_parse_search_items`` reads: a payload whose
    listings live only under data.items must satisfy the same families, or
    every intact key reads as drift."""
    payload = {
        "count": 1,
        "data": {
            "items": [
                {"id": 1, "title": "Ноутбук", "priceDetailed": {"value": 59900}, "urlPath": "/x"},
            ]
        },
    }

    assert shape_reference.missing_required_families(shape_signature(payload)) == []


def test_a_data_items_payload_that_lost_a_family_reports_only_that_family() -> None:
    """The other half: the aliases make the check honest, they do not silence
    it. With the price keys gone only the price family is missing — the id and
    title families survive in the same data.items envelope."""
    payload = {
        "count": 1,
        "data": {
            "items": [
                {"id": 1, "title": "Ноутбук", "urlPath": "/x"},
            ]
        },
    }
    price_family = next(family for family in shape_reference.SEARCH_REQUIRED_FAMILIES if "price" in family[0])

    assert shape_reference.missing_required_families(shape_signature(payload)) == [price_family]
