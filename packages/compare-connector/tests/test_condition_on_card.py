"""A source whose search rows never state the item's condition cannot name cheapest_comparable.

START_MODULE_CONTRACT
PURPOSE: Prove R-COMPARE-CONDITION-ON-CARD through the public compare_prices entry: an Avito row (classifieds;
         the condition lives on the card) stays in offers and may be cheapest, but is no like-for-like candidate
         unless the query itself asks for a used item.
SCOPE: compare_prices with the Avito and Citilink search tools replaced by native responses; the real adapters.
DEPENDS: pytest; compare_connector.server; avito/citilink output models; mcp_core.sources.
LINKS: M-COMPARE-DECIDE; M-SOURCES; R-COMPARE-CONDITION-ON-CARD; live run 2026-11-17 (an Avito "iPhone 16, 128 ГБ"
       row at 45 200 ₽ was cheapest_comparable; its card said «Б/у»).
END_MODULE_CONTRACT
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from avito_connector.models_output import AvitoSearchItemOut, AvitoSearchResponse
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from mcp_core.sources import BY_NAME, SOURCES


def _stub(monkeypatch: pytest.MonkeyPatch, avito_rows: list[tuple[int, str, float]]) -> None:
    async def avito_search(**kwargs):
        items = [
            AvitoSearchItemOut(item_id=i, title=t, price_rub=p, url=f"https://www.avito.ru/{i}")
            for i, t, p in avito_rows
        ]
        return AvitoSearchResponse(items=items, count=len(items))

    async def citilink_search(**kwargs):
        items = [
            CitilinkSearchItemOut(product_id="2001", title="Смартфон Apple iPhone 16 128 ГБ, черный", price_rub=68990)
        ]
        return CitilinkSearchResponse(items=items)

    monkeypatch.setitem(server.SOURCES, "avito", SimpleNamespace(avito_search=avito_search))
    monkeypatch.setitem(server.SOURCES, "citilink", SimpleNamespace(citilink_search=citilink_search))


@pytest.mark.req("R-COMPARE-CONDITION-ON-CARD")
async def test_an_avito_row_is_never_cheapest_comparable_for_an_unqualified_query(monkeypatch) -> None:
    _stub(monkeypatch, [(8609346018, "iPhone 16, 128 ГБ, SIM + eSIM", 45200)])

    result = await server.compare_prices("iphone 16 128gb", sources=["avito", "citilink"])

    assert (result.cheapest.source, result.cheapest.price_rub) == ("avito", 45200)
    assert (result.cheapest_comparable.source, result.cheapest_comparable.product_id) == ("citilink", "2001")
    assert any(w.startswith("comparable_price:") for w in result.warnings)


@pytest.mark.req("R-COMPARE-CONDITION-ON-CARD")
async def test_an_avito_row_may_be_cheapest_comparable_when_the_query_asks_for_used(monkeypatch) -> None:
    _stub(monkeypatch, [(8609346018, "iPhone 16, 128 ГБ, SIM + eSIM", 45200)])

    result = await server.compare_prices("iphone 16 128gb б/у", sources=["avito", "citilink"])

    assert (result.cheapest_comparable.source, result.cheapest_comparable.price_rub) == ("avito", 45200)


@pytest.mark.req("R-COMPARE-CONDITION-ON-CARD")
def test_only_sources_that_keep_the_condition_on_the_card_are_marked() -> None:
    assert {spec.name for spec in SOURCES if spec.condition_on_card} == {"avito"}
    assert BY_NAME["avito"].card_tool == "avito_card"
