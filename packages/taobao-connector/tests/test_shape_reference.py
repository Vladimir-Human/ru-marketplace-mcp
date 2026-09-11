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
    "anchors_total:int",
    "body_snippet:str",
    "items[].item_id:str",
    "items[].location:null",
    "items[].price_texts.attached:empty_array",
    "items[].price_texts.attached[]:str",
    "items[].price_texts.other:empty_array",
    "items[].sales:str",
    "items[].shop_name:str",
    "items[].title:str",
    "items[].url:str",
    "login_anchors:int",
    "title:str",
]
# Measured again on 2026-08-07 after the price hunt was scoped to the price
# wrapper (live capture cycle): the sales count «2000+人付款» used to leak into
# price_texts.other[] and a naive read promoted it to a strikethrough price;
# it is now a decoy outside the scoped hunt, so other[]:str left the shape.
# Measured again 2026-09-10 (login-wall triage): the extractor now surfaces the
# structural wall markers (anchors_total, login_anchors, body_snippet) that
# catch the EMPTY-title wall variant — they are part of the golden so losing
# them is loud, exactly like losing a price path.

CARD_GOLDEN = [
    "anchors_total:int",
    "body_snippet:str",
    "description_images:int",
    "login_anchors:int",
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
# Measured again 2026-09-10: the card extractor carries the same three
# structural wall markers as the search extractor (item pages redirect to the
# same login wall).


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
        "anchors_total:int",
        "body_snippet:str",
        "items[].item_id:str",
        "items[].title:str",
        "items[].url:str",
        "login_anchors:int",
        "title:str",
    ]
    assert missing_required_families(drifted) == [
        ("items[].price_texts.attached", "items[].price_cny"),
    ]
    # A legacy numeric price still satisfies the family.
    legacy = [*drifted, "items[].price_cny:float"]
    assert missing_required_families(legacy) == []


def test_missing_required_families_sees_lost_wall_markers() -> None:
    """The 2026-09-10 regression inverted: if a future extractor stops
    emitting the structural wall markers, the title-less login wall silently
    becomes readable as drift again. Losing any one of anchors_total /
    login_anchors / body_snippet must be LOUD at runtime, not only in the
    dev-time golden above."""
    healthy = list(SEARCH_SHAPE_REFERENCE)
    assert missing_required_families(healthy) == []
    for lost in ("anchors_total:int", "login_anchors:int", "body_snippet:str"):
        without = [entry for entry in healthy if entry != lost]
        missing = missing_required_families(without)
        assert missing, f"losing {lost} went silent"
        assert any(lost.split(":")[0] in family for family in missing)
