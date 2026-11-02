"""R07: bundled accessory noun phrases must not disqualify the main product."""
# START_MODULE_CONTRACT
# PURPOSE: Prove R-COMPARE-BUNDLE-NOUN-PHRASES through the public compare_prices MCP tool.
# SCOPE: Native Citilink 900/950 RUB stubs; real MCP client, adapter and classifiers; no network or browser.
# DEPENDS: compare_connector.server; citilink_connector.models_output; fastmcp.Client; pytest.
# LINKS: M-COMPARE; R-COMPARE-BUNDLE-NOUN-PHRASES; R-COMPARE-RELEVANCE-BOUNDARIES; R-OFFLINE.
# ORACLE: Owner requirement, 2026-11-02; expectations fixed without inspecting the candidate implementation.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_compare_mcp_bundle_noun_phrases: Bundle noun phrases, held-out modifiers and independent negative/query controls.
# END_MODULE_MAP

from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server
from fastmcp import Client


# START_BLOCK_PUBLIC_BUNDLE_COMPARISON_TESTS
@pytest.mark.parametrize(
    "query,title,expected_winner,expected_warning_prefixes",
    [
        # Reported false drops: the main product includes the complete accessory noun phrase.
        pytest.param(
            "ноутбук",
            "Ноутбук, зарядное устройство в комплекте",
            "1",
            (),
            id="known-charging-device-suffix",
        ),
        pytest.param(
            "камера",
            "Камера, кабель USB-C в комплекте",
            "1",
            (),
            id="known-usb-c-cable-suffix",
        ),
        pytest.param(
            "телефон",
            "Телефон, чехол силиконовый в комплекте",
            "1",
            (),
            id="known-silicone-case-suffix",
        ),
        # Held-out generic modifiers: not just the three reported strings.
        pytest.param(
            "камера",
            "Камера, кабель HDMI в комплекте",
            "1",
            (),
            id="heldout-hdmi-cable-suffix",
        ),
        pytest.param(
            "телефон",
            "Телефон, чехол прозрачный в комплекте",
            "1",
            (),
            id="heldout-transparent-case-suffix",
        ),
        pytest.param("phone", "Phone with a transparent case", "1", (), id="modifier-before-case-prefix-en"),
        pytest.param("camera", "Camera with an HDMI cable", "1", (), id="modifier-before-cable-prefix-en"),
        pytest.param(
            "телефон",
            "Телефон, в комплекте: силиконовый чехол",
            "1",
            (),
            id="modifier-before-case-prefix-ru",
        ),
        pytest.param(
            "камера",
            "Камера, КАБЕЛЬ\tUSB-C В\tКОМПЛЕКТЕ",
            "1",
            (),
            id="noun-phrase-case-and-whitespace",
        ),
        # A standalone accessory, used condition or another unbundled occurrence still excludes the row.
        pytest.param(
            "ноутбук",
            "Зарядное устройство для ноутбука",
            "2",
            ("relevance", "comparable_price"),
            id="standalone-charging-device",
        ),
        pytest.param(
            "телефон",
            "Чехол силиконовый для телефона",
            "2",
            ("relevance", "comparable_price"),
            id="standalone-silicone-case",
        ),
        pytest.param("телефон", "Телефон б/у", "2", ("condition", "comparable_price"), id="used-main-product"),
        pytest.param(
            "телефон",
            "Телефон б/у, чехол силиконовый в комплекте",
            "2",
            ("condition", "comparable_price"),
            id="bundle-does-not-hide-used-condition",
        ),
        pytest.param(
            "телефон",
            "Телефон, кабель USB-C в комплекте; чехол силиконовый",
            "2",
            ("relevance", "comparable_price"),
            id="another-unbundled-accessory",
        ),
        pytest.param(
            "телефон",
            "Телефон, чехол силиконовый в комплекте; чехол для телефона",
            "2",
            ("relevance", "comparable_price"),
            id="another-unbundled-occurrence-of-same-marker",
        ),
        pytest.param(
            "iPhone",
            "Case for iPhone with cable",
            "2",
            ("relevance", "comparable_price"),
            id="standalone-case-with-cable-en",
        ),
        pytest.param(
            "телефон",
            "Чехол для телефона с кабелем в комплекте",
            "2",
            ("relevance", "comparable_price"),
            id="standalone-case-with-cable-ru",
        ),
        pytest.param(
            "камера",
            "Кабель HDMI для камеры",
            "2",
            ("relevance", "comparable_price"),
            id="standalone-heldout-hdmi-cable",
        ),
        # Preserve already-required direct bundles, word boundaries and occurrence-local negation.
        pytest.param("телефон", "Телефон, чехол в комплекте", "1", (), id="direct-bundle-suffix"),
        pytest.param("phone", "Phone WITH a CASE", "1", (), id="direct-bundle-prefix-en"),
        pytest.param(
            "телефон",
            "Телефон, В\tКОМПЛЕКТЕ: ЗАЩИТНОЕ\tСТЕКЛО",
            "1",
            (),
            id="direct-bundle-nested-phrase",
        ),
        pytest.param("чемодан", "Suitcase Samsonite", "1", (), id="word-boundary-suitcase"),
        pytest.param("варочная панель", "Варочная панель СТЕКЛОКЕРАМИКА", "1", (), id="word-boundary-cyrillic"),
        pytest.param("iPhone", "UNUSED iPhone", "1", (), id="word-boundary-unused"),
        pytest.param("телефон", "Телефон НЕ Б/У", "1", (), id="negated-condition-ru"),
        pytest.param("phone", "Phone not USED", "1", (), id="negated-condition-en"),
        pytest.param("телефон", "Телефон, не чехол", "1", (), id="negated-accessory"),
        pytest.param(
            "телефон",
            "Телефон не б/у; used",
            "2",
            ("condition", "comparable_price"),
            id="another-positive-condition-after-negation",
        ),
        # Query exclusions stay symmetric: a bundle is not a request for a standalone accessory.
        pytest.param("чехол для телефона", "Чехол силиконовый для телефона", "1", (), id="requested-accessory"),
        pytest.param("used phone", "USED phone", "1", (), id="requested-condition"),
        pytest.param("unused phone", "Used phone", "2", ("condition", "comparable_price"), id="unused-query"),
        pytest.param(
            "телефон не б/у", "Телефон Б/У", "2", ("condition", "comparable_price"), id="negated-condition-query"
        ),
        pytest.param(
            "phone with case", "Case for phone", "2", ("relevance", "comparable_price"), id="direct-bundle-query"
        ),
        pytest.param(
            "Телефон, чехол силиконовый в комплекте",
            "Чехол силиконовый для телефона",
            "2",
            ("relevance", "comparable_price"),
            id="noun-phrase-case-bundle-query",
        ),
        pytest.param(
            "Ноутбук, зарядное устройство в комплекте",
            "Зарядное устройство для ноутбука",
            "2",
            ("relevance", "comparable_price"),
            id="noun-phrase-charger-bundle-query",
        ),
        pytest.param(
            "Камера, кабель USB-C в комплекте",
            "Кабель USB-C для камеры",
            "2",
            ("relevance", "comparable_price"),
            id="noun-phrase-cable-bundle-query",
        ),
        pytest.param(
            "phone with a transparent case",
            "Case for phone",
            "2",
            ("relevance", "comparable_price"),
            id="modified-bundle-query",
        ),
        pytest.param(
            "телефон не чехол",
            "Чехол силиконовый для телефона",
            "2",
            ("relevance", "comparable_price"),
            id="negated-accessory-query",
        ),
        pytest.param(
            "Телефон, чехол силиконовый в комплекте; чехол для телефона",
            "Чехол для телефона",
            "1",
            (),
            id="another-positive-accessory-occurrence-in-query",
        ),
    ],
)
async def test_compare_mcp_bundle_noun_phrases(monkeypatch, query, title, expected_winner, expected_warning_prefixes):
    """R-COMPARE-BUNDLE-NOUN-PHRASES; R-COMPARE-RELEVANCE-BOUNDARIES.

    The table is the requirement-derived oracle, not a classifier prediction.
    The 950 RUB row uses the query itself as a suitable reference listing.
    Only source I/O is replaced; the public MCP call and Citilink adapter stay real.
    """
    source_queries = []

    async def search(*, query: str) -> CitilinkSearchResponse:
        source_queries.append(query)
        return CitilinkSearchResponse(
            query=query,
            count=2,
            items=[
                CitilinkSearchItemOut(product_id="1", title=title, price_rub=900),
                CitilinkSearchItemOut(product_id="2", title=query, price_rub=950),
            ],
        )

    monkeypatch.setenv("MARKETPLACE_SOURCES", "citilink")
    monkeypatch.setattr(server, "SOURCES", {"citilink": SimpleNamespace(citilink_search=search)})
    async with Client(server.mcp) as client:
        result = (
            await client.call_tool("compare_prices", {"query": query, "sources": ["citilink"], "per_source_limit": 2})
        ).structured_content

    assert result is not None
    assert source_queries == [query]
    assert result["query"] == query
    assert result["complete"] is True
    assert result["sources_queried"] == ["citilink"]
    assert result["sources_ok"] == ["citilink"]
    assert result["total_offers"] == 2
    assert [
        (row["source"], row["product_id"], row["title"], row["price_rub"], row["currency"]) for row in result["offers"]
    ] == [
        ("citilink", "1", title, 900.0, "rub"),
        ("citilink", "2", query, 950.0, "rub"),
    ]
    assert result["price_spread_rub"] == 50.0
    assert result["cheapest"] == result["offers"][0]
    assert result["cheapest"]["product_id"] == "1"
    assert result["cheapest"]["price_rub"] == 900.0
    assert len(result["source_outcomes"]) == 1
    outcome = result["source_outcomes"][0]
    assert outcome["source"] == "citilink"
    assert outcome["status"] == "ok"
    assert outcome["offers_returned"] == 2
    assert outcome["warnings"] == []
    assert outcome["error_code"] is None

    assert result["cheapest_comparable"] is not None
    assert result["cheapest_comparable"]["product_id"] == expected_winner
    expected_offer = next(row for row in result["offers"] if row["product_id"] == expected_winner)
    assert result["cheapest_comparable"] == expected_offer
    assert sorted(text.partition(":")[0] for text in result["warnings"]) == sorted(expected_warning_prefixes)


# END_BLOCK_PUBLIC_BUNDLE_COMPARISON_TESTS
