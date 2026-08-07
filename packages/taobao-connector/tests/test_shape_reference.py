"""Reference shape signatures for the Taobao extractors, pinned to the capture.

Companion to the value-pinning DOM tests: ``shape_signature`` of the real
extractor's output over the committed captured pages. A field no value
assertion looks at cannot disappear or retype silently — the shape changes and
this test names the exact paths that drifted.

Goldens are measured (extractor run over the fixture), never hand-written.
The DOM half needs Node with jsdom and skips without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature
from taobao_connector import server
from taobao_connector.shape_reference import SEARCH_SHAPE_REFERENCE, missing_required_families

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_GOLDEN = [
    "items[].item_id:str",
    "items[].location:null",
    "items[].price_texts.attached:empty_array",
    "items[].price_texts.attached[]:str",
    "items[].price_texts.other:empty_array",
    "items[].sales:str",
    "items[].shop_name:str",
    "items[].title:str",
    "items[].url:str",
    "title:str",
]
# Measured again on 2026-08-07 after the price hunt was scoped to the price
# wrapper (live capture cycle): the sales count «2000+人付款» used to leak into
# price_texts.other[] and a naive read promoted it to a strikethrough price;
# it is now a decoy outside the scoped hunt, so other[]:str left the shape.

CARD_GOLDEN = [
    "description_images:int",
    "page_title:str",
    "price_texts.attached[]:str",
    "price_texts.other:empty_array",
    "sales:str",
    "shop_name:str",
    "title:str",
]
# Measured again on 2026-08-07 with the shared decoy list: the card fixture's
# sales count «2000+人付款» used to double as a price_texts.other[] candidate;
# it is now recognised as a sales decoy (still reported as sales), so the
# card's other[] shrank to an empty array.


def _extract(js_source: str, fixture: Path, page_url: str) -> dict:
    try:
        return run_extractor(js_source, fixture, page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_payload_shape_matches_the_capture() -> None:
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        FIXTURES / "search_grid.html",
        page_url="https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA",
    )
    assert shape_signature(payload) == SEARCH_GOLDEN


def test_card_payload_shape_matches_the_capture() -> None:
    payload = _extract(
        server._CARD_EXTRACT_JS,
        FIXTURES / "item_card.html",
        page_url="https://item.taobao.com/item.htm?id=123456789012",
    )
    assert shape_signature(payload) == CARD_GOLDEN


def test_live_search_shape_matches_the_selfcheck_registry() -> None:
    """The selfcheck compares live payloads against SEARCH_SHAPE_REFERENCE;
    the registry must agree with the live capture it was measured on, or the
    canary would cry drift on a healthy page."""
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        FIXTURES / "search_grid_live.html",
        page_url="https://s.taobao.com/search?q=%E8%BF%9E%E8%A1%A3%E8%A3%99",
    )
    signature = shape_signature(payload)
    assert signature == list(SEARCH_SHAPE_REFERENCE)
    assert missing_required_families(signature) == []


def test_missing_required_families_sees_a_lost_price_family() -> None:
    """The drift the wiring exists to catch: every price shape gone at once.

    Red before the wiring existed — without the registry nothing in the
    offline suite noticed a payload that extracts items but carries no key
    the parser can bind a price to.
    """
    drifted = [
        "items[].item_id:str",
        "items[].title:str",
        "items[].url:str",
        "title:str",
    ]
    assert missing_required_families(drifted) == [
        ("items[].price_texts.attached", "items[].price_cny"),
    ]
    # A legacy numeric price still satisfies the family.
    legacy = [*drifted, "items[].price_cny:float"]
    assert missing_required_families(legacy) == []
