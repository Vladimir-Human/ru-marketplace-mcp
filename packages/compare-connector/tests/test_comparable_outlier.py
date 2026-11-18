"""A replica-priced like-for-like title and an earbud's own charging case (audit cycle 2, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-OWN-CHARGING-CASE on recorded and constructed titles, and R-COMPARE-COMPARABLE-OUTLIER on
         the split itself and through one compare_prices call with a stubbed source.
SCOPE: decide._looks_like_an_accessory, decide._split_comparable_outliers, server.compare_prices (citilink stub).
DEPENDS: pytest; compare_connector.decide, compare_connector.server; citilink_connector output models.
LINKS: M-COMPARE; R-COMPARE-OWN-CHARGING-CASE; R-COMPARE-COMPARABLE-OUTLIER; A7 benchmark (tests/benchmark).
END_MODULE_CONTRACT
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import decide, server
from compare_connector.models_output import MarketOffer

QUERY = "apple airpods pro 2"


@pytest.mark.req("R-COMPARE-OWN-CHARGING-CASE")
@pytest.mark.parametrize(
    "title",
    [
        # Recorded on Yandex Market in audit cycle 2: the cheapest genuine offer.
        "Беспроводные наушники Apple AirPods Pro 2 (2023) MagSafe Charging Case (USB C) MTJV3",
        "Apple AirPods Pro 2 with MagSafe Charging Case (USB-C)",
        "Наушники Apple AirPods Pro 2 Wireless Charging Case, белый",
    ],
)
def test_an_earbuds_own_charging_case_is_no_accessory(title: str) -> None:
    assert not decide._looks_like_an_accessory(title, QUERY)


@pytest.mark.req("R-COMPARE-OWN-CHARGING-CASE")
@pytest.mark.parametrize(
    "title",
    [
        "Charging Case for AirPods Pro 2",
        "Charging Case AirPods Pro 2 USB-C",
        "Зарядный кейс Charging Case для AirPods Pro 2",
        "Replacement MagSafe Charging Case AirPods Pro 2",
        "Сменный кейс AirPods Pro 2 Charging Case",
        "Silicone skin for AirPods Pro 2 Charging Case",
        "AirPods Pro 2 Charging Case Cover, силиконовый",
        "Чехол для AirPods Pro 2 Charging Case",
    ],
)
def test_a_charging_case_the_listing_sells_stays_an_accessory(title: str) -> None:
    assert decide._looks_like_an_accessory(title, QUERY)


def _offers(*prices: float) -> list[MarketOffer]:
    return [
        MarketOffer(source="citilink", product_id=str(index), title=QUERY, price_rub=price, currency="rub")
        for index, price in enumerate(prices)
    ]


@pytest.mark.req("R-COMPARE-COMPARABLE-OUTLIER")
def test_leading_offers_under_a_quarter_of_the_median_are_split_off() -> None:
    # The recorded AirPods prices: 4 227 is 18% of the 23 185 median.
    outliers, rest = decide._split_comparable_outliers(_offers(4227, 20452, 23185, 23269, 23444))

    assert [o.price_rub for o in outliers] == [4227]
    assert [o.price_rub for o in rest] == [20452, 23185, 23269, 23444]


@pytest.mark.req("R-COMPARE-COMPARABLE-OUTLIER")
@pytest.mark.parametrize(
    "prices",
    [
        # The lowest true match in the recordings: Dyson, 22 990 against a 65 499 median (0.35).
        (22990, 27500, 64000, 65499, 66000),
        # Fewer than four like-for-like offers say too little about the median.
        (4227, 20452, 23185),
    ],
)
def test_a_cheap_true_match_or_a_thin_set_is_kept(prices: tuple[float, ...]) -> None:
    outliers, rest = decide._split_comparable_outliers(_offers(*prices))

    assert outliers == [] and [o.price_rub for o in rest] == list(prices)


def _stub_citilink(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, str, int]]) -> None:
    async def search(**kwargs):
        items = [CitilinkSearchItemOut(product_id=pid, title=title, price_rub=price) for pid, title, price in rows]
        return CitilinkSearchResponse(items=items)

    monkeypatch.setitem(server.SOURCES, "citilink", SimpleNamespace(citilink_search=search))


@pytest.mark.req("R-COMPARE-COMPARABLE-OUTLIER")
async def test_compare_prices_names_the_skipped_outlier_which_stays_cheapest(monkeypatch: pytest.MonkeyPatch) -> None:
    title = "Наушники Apple AirPods Pro 2"
    _stub_citilink(monkeypatch, [("1", title, 4227), ("2", title, 20452), ("3", title, 23185), ("4", title, 23269)])

    result = await server.compare_prices(QUERY, sources=["citilink"])

    assert result.cheapest.product_id == "1"
    assert result.cheapest_comparable.product_id == "2"
    assert [o.product_id for o in result.offers] == ["1", "2", "3", "4"]
    assert [w for w in result.warnings if w.startswith("price_outlier: skipped")] == [
        "price_outlier: skipped citilink/1 at 4227 ₽ for cheapest_comparable: under a quarter of the median "
        "like-for-like price, so a replica or another configuration is likelier than a bargain; cheapest_comparable "
        "is the next one, 20452 ₽; verify before quoting it"
    ]
