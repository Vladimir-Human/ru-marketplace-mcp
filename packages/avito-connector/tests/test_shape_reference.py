"""Reference shape signature of the REAL Avito ``js/items`` payload.

``test_live_payload_contract.py`` pins what the parser must produce *from* the
captured response; this file pins the captured response itself — the input
contract — via ``resilience.shape_signature``. When Avito renames, retypes or
re-nests a field, the drift shows up here as a named path diff before it ever
reaches a silent-wrong page of listings.

The golden is measured — ``shape_signature`` of the committed fixture, captured
2026-07-28 from a residential session (provenance in the fixture's contract
test) — so it can only be regenerated from a real capture, never written up.

Pure Python: no Node, no network, always runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_core.resilience import shape_signature

FIXTURE = Path(__file__).parent / "fixtures" / "js_items_live.json"

# Union shapes are expected where the capture itself varies: some items carry
# a location object while others carry null, and rating.score is int on some
# rows and float on others. That variation is data, not drift.
PAYLOAD_GOLDEN = [
    "count:int",
    "itemsOnPage:int",
    "items[].addressDetailed.locationName:str",
    "items[].allowTimeStamp:int",
    "items[].category.compare:bool",
    "items[].category.id:int",
    "items[].category.name:str",
    "items[].category.pageRootId:int",
    "items[].category.rootId:int",
    "items[].category.slug:str",
    "items[].description:str",
    "items[].geo.geoReferences:empty_array",
    "items[].geo.geoReferences[].content:<truncated>",
    "items[].id:int",
    "items[].imagesCount:int",
    "items[].images[].208x208:str",
    "items[].images[].236x236:str",
    "items[].images[].416x416:str",
    "items[].images[].472x472:str",
    "items[].isMarketplace:bool",
    "items[].location.id:int",
    "items[].location.isCurrent:bool",
    "items[].location.isRegion:bool",
    "items[].location.name:str",
    "items[].location.namePrepositional:str",
    "items[].location:null",
    "items[].locationId:int",
    "items[].priceDetailed.enabled:bool",
    "items[].priceDetailed.exponent:str",
    "items[].priceDetailed.fullString:str",
    "items[].priceDetailed.hasValue:bool",
    "items[].priceDetailed.postfix:str",
    "items[].priceDetailed.string:str",
    "items[].priceDetailed.stringWithoutDiscount:null",
    "items[].priceDetailed.title.full:str",
    "items[].priceDetailed.title.short:str",
    "items[].priceDetailed.titleDative:str",
    "items[].priceDetailed.value:int",
    "items[].priceDetailed.wasLowered:bool",
    "items[].rating.score:float",
    "items[].rating.score:int",
    "items[].rating.showChevronEnd:bool",
    "items[].rating.summary:str",
    "items[].sortTimeStamp:int",
    "items[].title:str",
    "items[].urlPath:str",
    "totalCount:int",
    "totalElements:int",
]


def test_live_payload_shape_matches_the_capture() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert shape_signature(payload) == PAYLOAD_GOLDEN


def test_the_parser_bindings_survive_in_the_reference_shape() -> None:
    """The fields the parser actually binds to must stay in the reference.

    A fixture trimmed of a parser-critical key would otherwise pass the shape
    comparison (both sides computed from the same bytes) while teaching the
    suite that a missing field is normal. Pin the binding set by name.
    """
    from avito_connector.server import _parse_search_items

    raw, _total = _parse_search_items(json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert raw, "the live fixture produced no items — the reference proves nothing"

    signature = set(PAYLOAD_GOLDEN)
    for path in (
        "items[].id:int",
        "items[].title:str",
        "items[].urlPath:str",
        "items[].priceDetailed.value:int",
        "items[].sortTimeStamp:int",
    ):
        assert path in signature, f"parser-critical path {path} vanished from the reference shape"
