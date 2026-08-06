"""Reference shape signatures for the Citilink extractors, pinned to the capture.

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
from citilink_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_GOLDEN = [
    "items[].old_price_text:null",
    "items[].old_price_text:str",
    "items[].price_meta:str",
    "items[].price_text:str",
    "items[].price_texts.attached[]:str",
    "items[].price_texts.other:empty_array",
    "items[].price_texts.other[]:str",
    "items[].product_id:str",
    "items[].title:str",
    "items[].url:str",
    "title:str",
]

CARD_GOLDEN = [
    "is_available:bool",
    "old_price_text:str",
    "page_title:str",
    "price_meta:str",
    "price_text:str",
    "price_texts.attached[]:str",
    "price_texts.other[]:str",
    "title:str",
]


def _extract(js_source: str, fixture: Path, page_url: str) -> dict:
    try:
        return run_extractor(js_source, fixture, page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_payload_shape_matches_the_capture() -> None:
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        FIXTURES / "search_grid.html",
        page_url="https://www.citilink.ru/catalog/noutbuki/",
    )
    assert shape_signature(payload) == SEARCH_GOLDEN


def test_card_payload_shape_matches_the_capture() -> None:
    payload = _extract(
        server._CARD_EXTRACT_JS,
        FIXTURES / "card.html",
        page_url="https://www.citilink.ru/product/smartfon-apple-iphone-16-128gb-2038477/",
    )
    assert shape_signature(payload) == CARD_GOLDEN
