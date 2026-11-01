"""Offline regressions for R-COMPARE-VARIANT-IDENTITY through compare_prices."""
# START_MODULE_CONTRACT
# PURPOSE: Prove a title naming sizes that contradict the query cannot become cheapest_comparable.
# SCOPE: Native Citilink response stubs through the public entry; no network, browser or live source.
# DEPENDS: compare_connector.server; citilink_connector.models_output; pytest.
# LINKS: M-COMPARE; R-COMPARE-VARIANT-IDENTITY; R-OFFLINE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _stub_citilink: Replace the native source tool while keeping the real comparison adapter.
# test_query_asking_128gb_never_crowns_the_64gb_variant: The probe: junior stays cheapest, not comparable.
# test_offer_without_a_size_in_the_title_stays_comparable: Unknown size is not a contradiction.
# test_ru_and_latin_units_are_one_size: "128 ГБ" and "128GB" are the same size.
# test_several_size_mismatches_cost_one_warning: One warning per call, not one per offer.
# test_ram_and_storage_pair_is_not_excluded_by_its_ram_size: "8/128GB" against a 128GB query is comparable.
# test_query_without_a_size_excludes_nothing: No stated size means nothing to contradict.
# test_terabyte_query_accepts_a_title_in_gigabytes: "1 ТБ" and "1024 ГБ" are one size; "1000 ГБ" is not.
# END_MODULE_MAP

from __future__ import annotations

from types import SimpleNamespace

import pytest
from citilink_connector.models_output import CitilinkSearchItemOut, CitilinkSearchResponse
from compare_connector import server


# START_BLOCK_VARIANT_SIZE_TESTS
def _stub_citilink(monkeypatch, rows):
    """Replace ``citilink_search`` with native response rows of (id, title, price)."""

    async def search(**kwargs):
        return CitilinkSearchResponse(
            items=[
                CitilinkSearchItemOut(product_id=product_id, title=title, price_rub=price)
                for product_id, title, price in rows
            ]
        )

    monkeypatch.setitem(server.SOURCES, "citilink", SimpleNamespace(citilink_search=search))


async def test_query_asking_128gb_never_crowns_the_64gb_variant(monkeypatch):
    """R-COMPARE-VARIANT-IDENTITY: old code makes the 64GB offer cheapest_comparable.

    The senior variant stays in offers and may be plain cheapest; the agent is
    never handed the junior model as the like-for-like answer.
    """
    _stub_citilink(monkeypatch, [("1", "ACME X100 64GB", 20000), ("2", "ACME X100 128GB", 25000)])

    result = await server.compare_prices("ACME X100 128GB", sources=["citilink"])

    assert result.cheapest.title == "ACME X100 64GB"
    assert result.cheapest_comparable.title == "ACME X100 128GB"
    assert [offer.title for offer in result.offers] == ["ACME X100 64GB", "ACME X100 128GB"]
    assert "identity_mismatch: 1 offer states 64GB, query asks 128GB; not comparable" in result.warnings


async def test_offer_without_a_size_in_the_title_stays_comparable(monkeypatch):
    """R-COMPARE-VARIANT-IDENTITY: unknown is not a contradiction.

    The missing warning fails on the old code; the winner assertion guards the
    new code against excluding a title that simply does not state a size.
    """
    _stub_citilink(monkeypatch, [("1", "ACME X100", 20000), ("2", "ACME X100 256GB", 26000)])

    result = await server.compare_prices("ACME X100 128GB", sources=["citilink"])

    assert result.cheapest_comparable.title == "ACME X100"
    assert "identity_mismatch: 1 offer states 256GB, query asks 128GB; not comparable" in result.warnings


@pytest.mark.parametrize(
    "query,other,matching,expected_warning",
    [
        (
            "ACME X100 128 ГБ",
            "ACME X100 64 ГБ",
            "ACME X100 128GB",
            "identity_mismatch: 1 offer states 64GB, query asks 128GB; not comparable",
        ),
        (
            "ACME X100 1 ТБ",
            "ACME X100 512 ГБ",
            "ACME X100 1TB",
            "identity_mismatch: 1 offer states 512GB, query asks 1TB; not comparable",
        ),
    ],
)
async def test_ru_and_latin_units_are_one_size(monkeypatch, query, other, matching, expected_warning):
    """R-COMPARE-VARIANT-IDENTITY: "128 ГБ" states the size "128GB", not a different one.

    Old code crowns the contradicting title, so the winner and the warning both
    fail there; the warning text also proves unit and case normalisation on the
    query side and the title side.
    """
    _stub_citilink(monkeypatch, [("1", other, 20000), ("2", matching, 25000)])

    result = await server.compare_prices(query, sources=["citilink"])

    assert result.cheapest_comparable.title == matching
    assert [w for w in result.warnings if w.startswith("identity_mismatch:")] == [expected_warning]


async def test_several_size_mismatches_cost_one_warning(monkeypatch):
    """R-COMPARE-VARIANT-IDENTITY: warnings cost tokens, so one per call, not per offer."""
    _stub_citilink(
        monkeypatch,
        [("1", "ACME X100 64GB", 20000), ("2", "ACME X100 64GB", 21000), ("3", "ACME X100 128GB", 25000)],
    )

    result = await server.compare_prices("ACME X100 128GB", sources=["citilink"])

    assert [w for w in result.warnings if w.startswith("identity_mismatch:")] == [
        "identity_mismatch: 2 offers state 64GB, query asks 128GB; not comparable"
    ]
    assert result.cheapest.title == "ACME X100 64GB"
    assert result.cheapest_comparable.title == "ACME X100 128GB"


async def test_ram_and_storage_pair_is_not_excluded_by_its_ram_size(monkeypatch):
    """R-COMPARE-VARIANT-IDENTITY: "8/128GB" names RAM and storage; only the asked size decides.

    Old code crowns the 8/64GB row and emits no warning, so both assertions
    fail there.
    """
    _stub_citilink(monkeypatch, [("1", "Ноутбук ACME X100 8/64GB", 20000), ("2", "Ноутбук ACME X100 8/128GB", 25000)])

    result = await server.compare_prices("ACME X100 128GB", sources=["citilink"])

    assert result.cheapest_comparable.title == "Ноутбук ACME X100 8/128GB"
    assert "identity_mismatch: 1 offer states 64GB, query asks 128GB; not comparable" in result.warnings


async def test_query_without_a_size_excludes_nothing(monkeypatch):
    """R-COMPARE-VARIANT-IDENTITY: a query that states no size has nothing to contradict.

    Passes on the old code by design: it guards the new comparison against
    excluding offers on a size the caller never asked for.
    """
    _stub_citilink(monkeypatch, [("1", "ACME X100 64GB", 20000), ("2", "ACME X100 128GB", 25000)])

    result = await server.compare_prices("ACME X100", sources=["citilink"])

    assert result.cheapest_comparable.title == "ACME X100 64GB"
    assert not [w for w in result.warnings if w.startswith("identity_mismatch:")]


async def test_terabyte_query_accepts_a_title_in_gigabytes(monkeypatch):
    """R-COMPARE-VARIANT-IDENTITY: 1 TB is sold as 1024 GB, so the units must not split one size.

    Old code compares "1TB" with "1024GB" as text, excludes the cheaper title
    and warns about it, so both assertions fail there; the 1000 GB row keeps
    the comparison from turning into "any size close to a terabyte".
    """
    _stub_citilink(
        monkeypatch,
        [("1", "ACME X100 1000 ГБ", 19000), ("2", "ACME X100 1024 ГБ", 20000), ("3", "ACME X100 1TB", 25000)],
    )

    result = await server.compare_prices("ACME X100 1 ТБ", sources=["citilink"])

    assert result.cheapest_comparable.title == "ACME X100 1024 ГБ"
    assert [w for w in result.warnings if w.startswith("identity_mismatch:")] == [
        "identity_mismatch: 1 offer states 1000GB, query asks 1TB; not comparable"
    ]


# END_BLOCK_VARIANT_SIZE_TESTS
