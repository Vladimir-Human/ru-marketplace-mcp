"""Reference shape signatures for the DNS extractors, pinned to the capture.

The DOM tests in ``test_search_extractor_dom.py`` / ``test_card_extractor_dom.py``
pin the *values* a known page must produce. This file pins the *shape* — the
sorted set of paths and types the extractor emits — via
``resilience.shape_signature``. A field no value assertion looks at cannot
disappear or retype silently: the shape changes and this test names the exact
paths that drifted.

The goldens live in ``dns_connector.shape_reference`` — the same registry
``dns_selfcheck`` compares live payloads against — and this file asserts the
registry still agrees with the fixtures, so it cannot go stale silently.
Measured (extractor run over the fixture), never hand-written. The DOM half
needs Node with jsdom and skips without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dns_connector import server, shape_reference
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"


def _extract(js_source: str, fixture: Path, page_url: str) -> dict:
    try:
        return run_extractor(js_source, fixture, page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_payload_shape_matches_the_capture() -> None:
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        FIXTURES / "search_grid.html",
        page_url="https://www.dns-shop.ru/search/?q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA",
    )
    assert shape_signature(payload) == list(shape_reference.SEARCH_SHAPE_REFERENCE)


def test_card_payload_shape_matches_the_capture() -> None:
    payload = _extract(
        server._CARD_EXTRACT_JS,
        FIXTURES / "card.html",
        page_url="https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk-huawei/",
    )
    assert shape_signature(payload) == list(shape_reference.CARD_SHAPE_REFERENCE)
