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
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature

FIXTURE = Path(__file__).parent / "fixtures" / "search_grid.html"

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
