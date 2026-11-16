"""CITILINK_TIMEOUT is the budget of the rendered read (R-SETTINGS-WIRED).

The setting existed and nothing read it: every rendered read ran under the
shared kit's own 30-second evaluation budget. It is now this connector's own
budget, so an operator can retune it without touching the shared runtime — and
the default stays the 30 seconds the read had before.
"""

from __future__ import annotations

import pytest
from citilink_connector import server
from citilink_connector.settings import CitilinkSettings
from pydantic import ValidationError

SEARCH_EXTRACTED = {
    "title": "ноутбук — DNS",
    "items": [
        {
            "product_id": "5f4dcc3b5aa764d61d8327de",
            "title": "Ноутбук Lenovo IdeaPad 3",
            "price_meta": "52999",
            "price_text": "52 999₽",
            "old_price_text": "62 999",
            "price_texts": {"attached": ["52 999₽"], "other": ["62 999"]},
            "url": "https://www.citilink.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/",
        }
    ],
}


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache.clear()


@pytest.mark.req("R-SETTINGS-WIRED")
async def test_the_configured_timeout_reaches_the_rendered_read(monkeypatch):
    """The env var must land in the browser read, not in a dead constant."""
    captured: dict[str, object] = {}

    async def fake_render_json(url, extract_js, wait_ms, **kwargs):
        captured.update(kwargs)
        return SEARCH_EXTRACTED

    monkeypatch.setenv("CITILINK_TIMEOUT", "12")
    monkeypatch.setattr(server, "_settings", CitilinkSettings())
    monkeypatch.setattr(server, "render_json", fake_render_json)

    await server.citilink_search("ноутбук")

    assert captured["evaluate_timeout"] == 12.0


@pytest.mark.req("R-SETTINGS-WIRED")
def test_the_default_budget_is_the_one_the_read_had_before(monkeypatch):
    monkeypatch.delenv("CITILINK_TIMEOUT", raising=False)

    assert CitilinkSettings().timeout == 30.0


@pytest.mark.req("R-SETTINGS-WIRED")
@pytest.mark.parametrize("value", [4.9, 45.1])
def test_a_budget_outside_five_to_forty_five_seconds_is_refused(value):
    with pytest.raises(ValidationError):
        CitilinkSettings(timeout=value)
