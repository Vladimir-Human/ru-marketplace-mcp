"""m1-m4 over Ozon's captured composer search and its embedded widget JSON."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent search tiles and reject invented current/original prices or sku ids.
# SCOPE: search_composer_live.json; pure _search_items_from_payload only, no composer fetch.
# DEPENDS: pytest; stdlib json,pathlib; ozon_connector.server; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: The composer fixture named by shape_reference.py.
# PRICE_PATHS: priceV2 text amounts inside JSON-encoded widgetStates values, not rating atoms.
# _parse: Decode only the outer envelope; the real parser decodes its widgets.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# test_removed_current_price_keeps_the_original_in_its_role: The m2 seeds that once promoted the original price.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, check_case, mutation_case, run_mutations
from ozon_connector import server

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = ("search_composer_live.json",)
PRICE_PATHS = (r"^widgetStates\.tileGridDesktop.*\.items\[\]\.mainState\[\]\.priceV2\.price\[\]\.text$",)
PRICE_FIELDS = ("price", "price_original")
ID_FIELDS = ("sku",)
# The m2 seeds that removed the tile's current price text: server._parse_search_tile used to
# drop the empty entry and promote the ORIGINAL_PRICE one to price (fixed 2026-11-17).
CURRENT_PRICE_SEEDS = (0, 2, 9, 11, 12, 14, 19, 24, 25, 27, 28, 31, 32, 38, 40, 41, 42, 43, 44, 46, 49)
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _parse(raw: str) -> list:
    return server._search_items_from_payload(json.loads(raw))


# END_BLOCK_CAPTURE_CASES
# START_BLOCK_SEEDED_TESTS


@pytest.mark.req("R-CAPTURE-MUTATION.equivalent")
@pytest.mark.parametrize("name", CASES)
def test_capture_equivalent(name: str) -> None:
    run_mutations(
        (FIXTURES / name).read_text(encoding="utf-8"),
        _parse,
        fixture=name,
        mutation="m1",
        kind="json",
        price_paths=PRICE_PATHS,
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        markers=DECOY_MARKERS,
        allowed_errors=(ConnectorError,),
        seeds=PYTHON_SEEDS,
        exclusions=EXCLUSIONS,
    )


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("mutation", ("m2", "m3", "m4"))
def test_capture_no_invented_price(name: str, mutation: str) -> None:
    run_mutations(
        (FIXTURES / name).read_text(encoding="utf-8"),
        _parse,
        fixture=name,
        mutation=mutation,
        kind="json",
        price_paths=PRICE_PATHS,
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        markers=DECOY_MARKERS,
        allowed_errors=(ConnectorError,),
        seeds=PYTHON_SEEDS,
        exclusions=EXCLUSIONS,
    )


# END_BLOCK_SEEDED_TESTS
# START_BLOCK_MINIMAL_VIOLATIONS


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price", "R-OZON-SEARCH-NUMERIC.roles")
@pytest.mark.parametrize("seed", CURRENT_PRICE_SEEDS)
def test_removed_current_price_keeps_the_original_in_its_role(seed: int) -> None:
    raw = (FIXTURES / "search_composer_live.json").read_text(encoding="utf-8")
    mutated = mutation_case(
        raw, "m2", seed, kind="json", price_paths=PRICE_PATHS, price_nodes=(), markers=DECOY_MARKERS
    )
    check_case(
        mutated,
        _parse(raw),
        _parse,
        clean_raw=raw,
        mutation="m2",
        kind="json",
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        allowed_errors=(ConnectorError,),
    )


# END_BLOCK_MINIMAL_VIOLATIONS


@pytest.mark.req("R-OZON-SEARCH-NUMERIC.roles")
@pytest.mark.parametrize(
    "entries",
    [
        [{"textStyle": "PRICE"}, {"text": "27 949 ₽", "textStyle": "ORIGINAL_PRICE"}],
        [{"text": "27 949 ₽", "textStyle": "ORIGINAL_PRICE"}],
        [{"text": "27 949 ₽", "textStyle": "ORIGINAL_PRICE"}, {"text": "17 324 ₽", "textStyle": "PRICE"}],
    ],
    ids=["current-text-gone", "current-entry-gone", "original-listed-first"],
)
def test_an_original_price_entry_never_becomes_the_tile_price(entries: list) -> None:
    tile = server._parse_search_tile(
        {
            "sku": 1,
            "mainState": [
                {"type": "priceV2", "priceV2": {"price": entries, "priceStyle": {"styleType": "CARD_PRICE"}}}
            ],
        }
    )
    current = next((e["text"] for e in entries if e.get("textStyle") == "PRICE" and e.get("text")), None)
    assert tile["price"] == (17324.0 if current else None)
    assert tile["price_original"] == 27949.0
    assert tile.get("price_kind") == ("card" if current else None)
