"""The seller id a card hands on opens that same seller (audit cycle 1, finding F-A6, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove seller_handle and _seller_profile_url round-trip every profile kind, including brand slugs
         that a bare id would send to /user/<id>/profile (a 404 live).
SCOPE: Pure parse helpers; the avito_seller shell with a stubbed page fetch (URL only).
DEPENDS: avito_connector.parse, avito_connector.server; pytest.
LINKS: R-AVITO-SELLER-ROUNDTRIP.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from avito_connector import server
from avito_connector.parse import _seller_profile_url, seller_handle

HEX = "0123456789abcdef0123456789abcdef"


@pytest.mark.req("R-AVITO-SELLER-ROUNDTRIP")
@pytest.mark.parametrize(
    ("profile_url", "handle"),
    [
        ("https://www.avito.ru/brands/tot_samy_sashaa", "brands/tot_samy_sashaa"),  # the audit's card
        ("https://www.avito.ru/brands/i224915198", "brands/i224915198"),
        (f"https://www.avito.ru/brands/{HEX}", HEX),
        ("https://www.avito.ru/user/123456/profile", "123456"),
        (f"https://www.avito.ru/user/{HEX}/profile", f"user/{HEX}"),
    ],
)
def test_the_handed_on_seller_id_opens_the_same_profile(profile_url: str, handle: str) -> None:
    assert seller_handle(profile_url) == handle
    assert _seller_profile_url(handle) == profile_url


@pytest.mark.req("R-AVITO-SELLER-ROUNDTRIP")
@pytest.mark.parametrize("raw", ["brands/", "user/a/b", "brands/a b", "shop/abc", "brands/../x"])
def test_a_prefixed_handle_stays_strict(raw: str) -> None:
    with pytest.raises(server.BadRequestError):
        _seller_profile_url(raw)


@pytest.mark.req("R-AVITO-SELLER-ROUNDTRIP")
def test_avito_seller_opens_the_brand_page_for_a_cards_brand_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    async def fake_page(url: str, ctx: Any) -> tuple[int, str, str]:
        opened.append(url)
        return 404, "", "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_page)
    with pytest.raises(server.ToolError):
        asyncio.run(server.avito_seller("brands/tot_samy_sashaa"))
    assert opened == ["https://www.avito.ru/brands/tot_samy_sashaa"]
