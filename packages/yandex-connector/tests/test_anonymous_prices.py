"""Yandex prices are named as what an anonymous visitor sees.

START_MODULE_CONTRACT
PURPOSE: Prove R-YANDEX-ANONYMOUS-PRICES: both everyday-price fields, the served guides and the skill say
         the price is the anonymous visitor's in the region Yandex assigns, and that a browser session can differ.
SCOPE: Pydantic field descriptions, the guide resources the Yandex server serves, the committed skills; no fetch.
DEPENDS: pytest; fastmcp Client; stdlib pathlib; yandex_connector server/models_output; mcp_core.resources.
LINKS: M-YANDEX; R-YANDEX-ANONYMOUS-PRICES; audit cycle 1 finding F-B2 (2026-11-17).
END_MODULE_CONTRACT
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client
from mcp_core.resources import guide_uri
from yandex_connector import server
from yandex_connector.models_output import ANONYMOUS_PRICE_NOTE, YandexCardResponse, YandexProduct

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.req("R-YANDEX-ANONYMOUS-PRICES")
def test_both_everyday_price_fields_say_they_are_the_anonymous_price() -> None:
    for model in (YandexProduct, YandexCardResponse):
        assert ANONYMOUS_PRICE_NOTE in (model.model_fields["price_rub"].description or ""), model.__name__
    assert "anonymous visitor" in ANONYMOUS_PRICE_NOTE and "signed-in browser" in ANONYMOUS_PRICE_NOTE


@pytest.mark.req("R-YANDEX-ANONYMOUS-PRICES")
async def test_the_served_guides_and_the_skills_carry_it() -> None:
    async with Client(server.mcp) as client:
        for key in ("yandex_search", "yandex_card"):
            assert ANONYMOUS_PRICE_NOTE in (await client.read_resource(guide_uri(key)))[0].text, key
    for rel in ("skills/yandex-connector/SKILL.md", "dsh/skills/yandex-connector/SKILL.md"):
        assert "Prices are what an anonymous visitor sees." in (ROOT / rel).read_text(encoding="utf-8"), rel
