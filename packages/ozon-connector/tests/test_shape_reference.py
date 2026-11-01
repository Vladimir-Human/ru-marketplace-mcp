"""Golden shape for the normalized Ozon composer search tiles.

The golden lives in ``ozon_connector.shape_reference`` — measured by running
the real ``_search_items_from_payload`` parser over the captured payload — and
this file asserts the registry still agrees with the fixture, so it cannot go
stale silently. The drift half proves the runtime shape-drift warning fires
through the public ``ozon_search`` entry when the tile parser loses its only
conditionally-emitted parser-critical key (the url family derived from
``action.link``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from mcp_core.resilience import shape_signature
from ozon_connector import server, shape_reference

FIXTURE = Path(__file__).parent / "fixtures" / "search_composer_live.json"


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _strip_tile_actions(payload: dict) -> dict:
    """Remove action.link from every tile so the url family vanishes."""
    drifted = copy.deepcopy(payload)
    states = {}
    for key, val in drifted["widgetStates"].items():
        if not key.startswith("tileGridDesktop-"):
            states[key] = val
            continue
        data = json.loads(val) if isinstance(val, str) else val
        for it in data.get("items") or []:
            if isinstance(it, dict):
                it.pop("action", None)
        states[key] = json.dumps(data, ensure_ascii=False)
    drifted["widgetStates"] = states
    return drifted


def _patch_composer(monkeypatch, payload: dict) -> None:
    async def fake_fetch(path, ctx):
        return 200, json.dumps(payload, ensure_ascii=False), "fake"

    monkeypatch.setattr(server, "_fetch_composer", fake_fetch)


def test_live_composer_normalization_matches_shape_golden() -> None:
    payload = _load()
    items = server._search_items_from_payload(payload)

    assert shape_signature({"items": items}) == list(shape_reference.SEARCH_SHAPE_REFERENCE)


async def test_search_warns_when_a_required_key_family_vanishes(monkeypatch):
    """Tiles that still parse without their url are structural drift the agent
    must see in the tool's own response: every result silently stopped being
    navigable, and nothing else in the pipeline warns about it."""
    _patch_composer(monkeypatch, _strip_tile_actions(_load()))

    result = await server.ozon_search("x")

    assert result.count > 0
    assert result.meta.healthy is False
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == ["shape_drift: ozon_search: missing items[].url"]


async def test_search_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    _patch_composer(monkeypatch, _load())

    result = await server.ozon_search("x")

    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_selfcheck_cries_drift_when_a_required_family_vanishes(monkeypatch):
    """The canary must fail the same way the tool does when a family is lost."""
    search_body = json.dumps(
        {
            "widgetStates": {
                "tileGridDesktop-1": json.dumps(
                    {
                        "items": [
                            {
                                "sku": 123,
                                "mainState": [
                                    {
                                        "type": "textDS",
                                        "id": "name",
                                        "textDS": {"text": "Filter without a link"},
                                    }
                                ],
                            }
                        ],
                    }
                ),
            },
        }
    )
    card_body = json.dumps(
        {
            "widgetStates": {
                "webPrice-1": json.dumps({"price": "1 000 ₽", "cardPrice": "900 ₽", "isAvailable": True}),
                "webReviewProductScore-1": json.dumps({"totalScore": 4.7, "reviewsCount": 5}),
            },
        }
    )
    reviews_body = json.dumps(
        {
            "widgetStates": {
                "webListReviews-1": json.dumps({"paging": {"total": 1}, "reviews": [{"content": {"comment": "ok"}}]}),
            },
        }
    )

    async def fake_fetch(path, ctx):
        if path.startswith("/search/"):
            return 200, search_body, "fake"
        if "/reviews/" in path:
            return 200, reviews_body, "fake"
        return 200, card_body, "fake"

    monkeypatch.setattr(server, "_fetch_composer", fake_fetch)

    result = await server.ozon_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert entry.smoke["shape_drift"] == "shape_drift: ozon_search: missing items[].url"
