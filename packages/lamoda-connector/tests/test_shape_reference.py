"""Reference shape signature for the Lamoda search extractor, pinned to the capture.

Companion to the value-pinning DOM test: ``shape_signature`` of the real
extractor's output over the committed captured grid. A field no value
assertion looks at cannot disappear or retype silently — the shape changes and
this test names the exact paths that drifted.

The golden is measured (extractor run over the fixture), never hand-written.
Needs Node with jsdom and skips without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lamoda_connector import server
from lamoda_connector.shape_reference import SEARCH_SHAPE_REFERENCE, missing_required_families
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid.html"
LIVE_FIXTURE = Path(__file__).parent / "fixtures" / "search_grid_live.html"

SEARCH_GOLDEN = [
    "items[].brand:null",
    "items[].price_texts.attached[]:str",
    "items[].price_texts.other:empty_array",
    "items[].price_texts.other[]:str",
    "items[].sku:str",
    "items[].title:str",
    "items[].url:str",
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
        FIXTURE,
        page_url="https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
    )
    assert shape_signature(payload) == SEARCH_GOLDEN


def test_live_search_shape_matches_the_selfcheck_registry() -> None:
    """The selfcheck compares live payloads against SEARCH_SHAPE_REFERENCE;
    the registry must agree with the live capture it was measured on, or the
    canary would cry drift on a healthy page."""
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        LIVE_FIXTURE,
        page_url="https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
    )
    signature = shape_signature(payload)
    assert signature == list(SEARCH_SHAPE_REFERENCE)
    assert missing_required_families(signature) == []


def test_missing_required_families_sees_a_lost_price_family() -> None:
    """The drift the wiring exists to catch: every price shape gone at once.

    Red before the wiring existed — without the registry nothing in the
    offline suite noticed a payload that extracts tiles but carries no key
    the parser can bind a price to.
    """
    drifted = [
        "items[].sku:str",
        "items[].title:str",
        "items[].url:str",
        "title:str",
    ]
    assert missing_required_families(drifted) == [
        ("items[].price_texts.attached", "items[].price_text", "items[].price_rub"),
    ]
    # A legacy numeric price still satisfies the family.
    legacy = [*drifted, "items[].price_rub:float"]
    assert missing_required_families(legacy) == []
