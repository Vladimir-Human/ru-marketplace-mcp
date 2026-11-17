"""A discounted search row without its withDiscount entry has no known everyday price.

START_MODULE_CONTRACT
PURPOSE: Prove R-YANDEX-DISCOUNT-BADGE: on a zone row carrying Yandex's discount badge the base price is the
         struck figure, so a missing withDiscount entry leaves price_rub null and the base price in
         price_old_rub; a row without the badge keeps its base price as the everyday price.
SCOPE: Pure ssr.parse_zone_items over the committed search_telefon_zone.html capture, edited in memory.
DEPENDS: pytest; stdlib re/pathlib; yandex_connector.ssr.
LINKS: M-YANDEX; R-YANDEX-DISCOUNT-BADGE; issue #303.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from yandex_connector import ssr

RAW = (Path(__file__).parent / "fixtures" / "search_telefon_zone.html").read_text(encoding="utf-8")
# One escaped additionalPrices entry or featureBadges entry inside the data-zone-data attribute.
WITH_DISCOUNT = re.compile(
    r",?\{&quot;priceType&quot;:&quot;withDiscount&quot;,&quot;priceValue&quot;:&quot;\d+&quot;\}"
)
DISCOUNT_BADGE = re.compile(
    r",?\{&quot;badgeType&quot;:&quot;SIMPLEDiscountTokenType&quot;,&quot;badgeTitle&quot;:&quot;[^&]*&quot;\}"
)


def _rows(html: str) -> list[tuple[float | None, float | None]]:
    return [(row["price_rub"], row["price_old_rub"]) for row in ssr.parse_zone_items(html)]


def _bases(html: str) -> list[float]:
    return [float(payload["price"]) for payload in ssr.zone_snippets(html)]


@pytest.mark.req("R-YANDEX-DISCOUNT-BADGE")
def test_the_capture_rows_are_discounted_with_badge_and_entry() -> None:
    clean = _rows(RAW)
    assert len(clean) == 3
    assert all(price is not None and old is not None and old > price for price, old in clean)
    assert len(WITH_DISCOUNT.findall(RAW)) == 3
    assert len(DISCOUNT_BADGE.findall(RAW)) == 3


@pytest.mark.req("R-YANDEX-DISCOUNT-BADGE")
def test_a_badged_row_without_its_discount_entry_has_no_everyday_price() -> None:
    html = WITH_DISCOUNT.sub("", RAW)

    assert _rows(html) == [(None, base) for base in _bases(RAW)]


@pytest.mark.req("R-YANDEX-DISCOUNT-BADGE")
async def test_yandex_search_names_the_rows_whose_discounted_price_is_missing(monkeypatch) -> None:
    from yandex_connector import server

    first = WITH_DISCOUNT.search(RAW)
    assert first is not None
    page = RAW[: first.start()] + RAW[first.end() :]

    async def fetch(url: str, label: str, ctx=None) -> str:
        return page

    monkeypatch.setattr(server, "_fetch_html", fetch)

    result = await server.yandex_search(query="телефон")

    prices = [item.price_rub for item in result.items]
    assert prices.count(None) == 1 and len(prices) == 3
    assert (
        "discount_price_missing: 1/3 items show a discount badge without the discounted price; "
        "price_rub is null (the struck base price stays in price_old_rub)"
    ) in result.meta.warnings


@pytest.mark.req("R-YANDEX-DISCOUNT-BADGE")
def test_a_row_without_badge_or_entry_keeps_its_base_price_as_the_everyday_price() -> None:
    html = DISCOUNT_BADGE.sub("", WITH_DISCOUNT.sub("", RAW))

    assert _rows(html) == [(base, None) for base in _bases(RAW)]
