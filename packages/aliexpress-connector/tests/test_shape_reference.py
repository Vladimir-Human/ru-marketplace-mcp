"""Reference shape signatures for the AliExpress extractors, pinned to the capture.

The DOM tests in ``test_parser_live.py`` pin the *values* a known page must
produce. This file pins the *shape* — the sorted set of paths and types the
extractor emits — via ``resilience.shape_signature``. A field no value
assertion looks at cannot disappear or retype silently: the shape changes and
this test names the exact paths that drifted.

The goldens live in ``aliexpress_connector.shape_reference`` — the same registry
``aliexpress_selfcheck`` compares live payloads against — and this file asserts
the registry still agrees with the fixtures, so it cannot go stale silently.
Measured (extractor run over the fixture), never hand-written. The DOM half
needs Node with jsdom and skips without it; the drift half is pure Python and
always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aliexpress_connector import server, shape_reference
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"

# Two clean tiles: every required key present, prices regular (no coupon), so
# the complete-payload companion can assert a healthy answer with no warnings.
COMPLETE_PAYLOAD = {
    "punish": False,
    "page_title": "realme watch с бесплатной доставкой на AliExpress",
    "items": [
        {
            "item_id": "1005010003103368",
            "title": "Realme Watch 5 смарт-часы",
            "price_texts": {"attached": ["6 967 ₽", "2 939 ₽"], "other": ["2 334 купили"]},
            "rating": "4,7",
            "orders": "2 334",
            "sku_id": "12000050856992499",
            "url": "https://aliexpress.ru/item/1005010003103368.html",
        },
        {
            "item_id": "1005012078314868",
            "title": "realme Watch S5 умные часы",
            "price_texts": {"attached": ["5 579 ₽"], "other": []},
            "rating": None,
            "orders": "190",
            "sku_id": None,
            "url": "https://aliexpress.ru/item/1005012078314868.html",
        },
    ],
}


def _extract(source: str, fixture: Path, url: str) -> dict:
    try:
        return run_extractor(source, fixture, page_url=url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()


def _patch_search(monkeypatch, payload):
    async def fake_render(url, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render_search", fake_render)


def test_search_extractor_shape_matches_the_capture() -> None:
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        FIXTURES / "search_grid.html",
        "https://aliexpress.ru/wholesale?SearchText=x",
    )

    assert shape_signature(payload) == list(shape_reference.SEARCH_SHAPE_REFERENCE)


def test_card_extractor_shape_matches_the_capture() -> None:
    payload = _extract(
        server._CARD_EXTRACT_JS,
        FIXTURES / "card.html",
        "https://aliexpress.ru/item/x.html",
    )

    assert shape_signature(payload) == list(shape_reference.CARD_SHAPE_REFERENCE)


async def test_search_warns_when_a_required_key_family_vanishes(monkeypatch):
    """Tiles that still parse without a parser-critical key are structural
    drift the agent must see in the tool's own response, not only via doctor."""
    drifted = {
        "punish": COMPLETE_PAYLOAD["punish"],
        "page_title": COMPLETE_PAYLOAD["page_title"],
        "items": [
            {key: value for key, value in item.items() if key != "price_texts"} for item in COMPLETE_PAYLOAD["items"]
        ],
    }
    _patch_search(monkeypatch, drifted)

    result = await server.aliexpress_search("realme watch")

    assert result.count == 2
    assert result.meta.healthy is False
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == ["shape_drift: aliexpress_search: missing items[].price_texts.attached"]


async def test_search_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    _patch_search(monkeypatch, COMPLETE_PAYLOAD)

    result = await server.aliexpress_search("realme watch")

    assert result.meta.healthy is True
    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_selfcheck_reports_shape_drift_when_a_required_path_vanishes(monkeypatch):
    """A page that still yields tiles but lost a parser-critical field is
    structural drift — loud, with the missing path named."""
    # The canary's parse smoke needs at least three tiles, so mirror that
    # here: enough tiles, every price attached, and the url family removed.
    tile = {
        "item_id": "1005010003103368",
        "title": "Realme Watch 5 смарт-часы",
        "price_texts": {"attached": ["6 967 ₽", "2 939 ₽"], "other": []},
        "rating": "4,7",
        "orders": "2 334",
        "sku_id": None,
        "url": "https://aliexpress.ru/item/1005010003103368.html",
    }
    drifted = {
        "punish": False,
        "page_title": "x",
        "items": [{k: v for k, v in t.items() if k != "url"} for t in (tile, dict(tile), dict(tile))],
    }

    async def fake_search(url, ctx):
        return drifted

    monkeypatch.setattr(server, "_cdp_render_search", fake_search)

    result = await server.aliexpress_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert entry.reason == "shape_drift"
    assert any("items[].url" in note for note in entry.notes)
