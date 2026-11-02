"""R07: false and true comparability drops through the public MCP comparison."""
# START_MODULE_CONTRACT
# PURPOSE: Prove R-COMPARE-RELEVANCE-BOUNDARIES without changing raw price ranking.
# SCOPE: In-process tools/call with native Citilink response stubs; no network or browser.
# DEPENDS: compare_connector.server; citilink_connector.models_output; fastmcp.Client; pytest.
# LINKS: M-COMPARE; R-COMPARE-RELEVANCE-BOUNDARIES; R-OFFLINE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_compare_mcp_relevance_markers: Boundary, bundle and negation regressions plus genuine-drop controls.
# END_MODULE_MAP

from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from fastmcp import Client


# START_BLOCK_RELEVANCE_MARKER_TESTS
@pytest.mark.parametrize(
    "query,title,warning",
    [
        pytest.param("чемодан 70 см", "Suitcase Samsonite 70 cm", None, id="suitcase"),
        pytest.param("варочная панель", "Варочная панель СТЕКЛОКЕРАМИКА", None, id="cyrillic-word"),
        pytest.param("iPhone", "UNUSED iPhone", None, id="unused"),
        pytest.param("iPhone", "iPhone НЕ Б/У", None, id="negated-used"),
        pytest.param("iPhone", "iPhone not USED", None, id="english-negation"),
        pytest.param("iPhone", "iPhone WITH a CASE", None, id="with-case"),
        pytest.param("iPhone", "iPhone, чехол В КОМПЛЕКТЕ", None, id="bundle-suffix"),
        pytest.param("iPhone", "iPhone, В\tКОМПЛЕКТЕ: ЗАЩИТНОЕ\tСТЕКЛО", None, id="bundle-phrase"),
        pytest.param("iPhone", "чехол для iPhone", "relevance:", id="real-case-ru"),
        pytest.param("iPhone", "CASE for iPhone", "relevance:", id="real-case-en"),
        pytest.param("iPhone", "SCREEN\tPROTECTOR for iPhone", "relevance:", id="real-phrase"),
        pytest.param("iPhone", "iPhone б/у", "condition:", id="real-used-ru"),
        pytest.param("iPhone", "USED iPhone", "condition:", id="real-used-en"),
        pytest.param("unused iPhone", "used iPhone", "condition:", id="unused-query"),
        pytest.param("iPhone не б/у", "iPhone Б/У", "condition:", id="negated-query"),
        pytest.param("iPhone with case", "case for iPhone", "relevance:", id="bundle-query"),
        pytest.param("чехол для iPhone", "Чехол для iPhone", None, id="requested-accessory"),
        pytest.param("used iPhone", "USED iPhone", None, id="requested-condition"),
        pytest.param("iPhone", "Case for iPhone with cable", "relevance:", id="bundle-does-not-hide-case"),
        pytest.param("iPhone", "iPhone не б/у; used", "condition:", id="negation-does-not-hide-used"),
    ],
)
async def test_compare_mcp_relevance_markers(monkeypatch, query, title, warning):
    """R-COMPARE-RELEVANCE-BOUNDARIES: no false drops; real unrequested markers still drop.

    The probe's 900/950 prices isolate relevance from outlier and stock policies.
    Keep the real Citilink adapter and compare_prices MCP entry, not a helper mock.
    """

    async def search(*, query):
        return CitilinkSearchResponse(
            items=[
                CitilinkSearchItemOut(product_id="1", title=title, price_rub=900),
                CitilinkSearchItemOut(product_id="2", title=query, price_rub=950),
            ]
        )

    monkeypatch.setattr(server, "SOURCES", {"citilink": SimpleNamespace(citilink_search=search)})
    async with Client(server.mcp) as client:
        result = (
            await client.call_tool("compare_prices", {"query": query, "sources": ["citilink"]})
        ).structured_content

    assert result is not None
    assert result["complete"] is True
    assert result["total_offers"] == 2
    assert [row["product_id"] for row in result["offers"]] == ["1", "2"]
    assert result["cheapest"]["product_id"] == "1"
    assert result["cheapest_comparable"]["product_id"] == ("2" if warning else "1")
    relevance_warnings = [
        text for text in result["warnings"] if text.startswith(("relevance:", "condition:", "comparable_price:"))
    ]
    if warning:
        assert any(text.startswith(warning) for text in relevance_warnings)
        assert any(text.startswith("comparable_price:") for text in relevance_warnings)
    else:
        assert relevance_warnings == []


# END_BLOCK_RELEVANCE_MARKER_TESTS
