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
