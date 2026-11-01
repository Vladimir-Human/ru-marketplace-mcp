"""Golden shape for the captured Megamarket search normalization.

The golden lives in ``megamarket_connector.shape_reference`` — measured by
running the real ``_parse_items`` parser over the captured payload — and this
file asserts the registry still agrees with the fixture, so it cannot go stale
silently. The drift half proves the runtime shape-drift warning fires through
the public ``megamarket_search`` entry when a parser-critical alias family
vanishes from the raw envelope.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from mcp_core.resilience import shape_signature
from megamarket_connector import server, shape_reference

FIXTURE = Path(__file__).parent / "fixtures" / "search_live.json"


async def _no_redirect(url, ctx=None):
    """Stand in for the CDP redirect probe: the URL is already final."""
    return url


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """The same process-state resets test_server.py applies, so the tool never
    reaches for Chrome or sleeps the pacer from this file either."""
    server._cache._data.clear()
    server._address_cache.clear()
    monkeypatch.setattr(server, "_address_id", None)
    monkeypatch.setattr(server, "_address_resolved", False)
    monkeypatch.setattr(server, "_address_source", None)
    monkeypatch.setattr(server, "_USE_PROFILE_ADDRESS", False)
    monkeypatch.setattr(server, "_search_params_cache", {})
    monkeypatch.setattr(server, "_min_gap", 0.0)
    monkeypatch.setattr(server, "_final_catalog_url", _no_redirect)


def _patch_post(monkeypatch, payload):
    async def fake_post(api_path, body, ctx, what):
        return payload

    monkeypatch.setattr(server, "_post", fake_post)


def test_live_search_normalization_matches_shape_golden() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items, total, container_found = server._parse_items(payload)

    assert shape_signature({"items": items, "total": total, "container_found": container_found}) == list(
        shape_reference.SEARCH_SHAPE_REFERENCE
    )


async def test_search_warns_when_a_required_key_family_vanishes(monkeypatch):
    """Items that still parse after losing a whole alias family are structural
    drift the agent must see in the tool's own response, not only via doctor."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for item in drifted["items"]:
        goods = item.get("goods") or {}
        goods.pop("title", None)
        goods.pop("name", None)
        goods.pop("goodsName", None)
    _patch_post(monkeypatch, drifted)

    result = await server.megamarket_search("стиральная машина")

    assert result.count == 3
    assert result.meta.healthy is False
    # The expected message is built from the registry's own title family (its
    # items[] aliases come first in the join), so the assertion pins the exact
    # message rather than a prefix.
    title_family = next(f for f in shape_reference.SEARCH_REQUIRED_FAMILIES if "items[].goods.title" in f)
    shape_warnings = [w for w in result.meta.warnings if w.startswith("shape_drift:")]
    assert shape_warnings == [f"shape_drift: megamarket_search: missing {'/'.join(title_family)}"]


async def test_search_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    _patch_post(monkeypatch, payload)

    result = await server.megamarket_search("стиральная машина")

    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_selfcheck_cries_drift_when_a_required_family_vanishes(monkeypatch):
    """The canary must fail the same way the tool does when a family is lost."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    drifted = copy.deepcopy(payload)
    for item in drifted["items"]:
        goods = item.get("goods") or {}
        goods.pop("title", None)
        goods.pop("name", None)
        goods.pop("goodsName", None)
    _patch_post(monkeypatch, drifted)

    result = await server.megamarket_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert entry.reason == "shape_drift"
    assert any("items[].goods.title" in note for note in entry.notes)
