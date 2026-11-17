"""m1-m4 over Wildberries' committed v9 search and v4 card JSON captures."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent product rows and reject invented kopeck-derived prices or nm ids.
# SCOPE: Both shape-reference inputs; pure _card_item_dict only, no HTTP/DOM/tool shell.
# DEPENDS: pytest; stdlib json,pathlib; wb_connector.server; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Captured search_v9_live/card_v4_live inputs.
# _parse: Decode and flatten products through the shared real parser.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# test_missing_original_price_stays_unknown: The m2/m4 seeds that once copied the current price into the original.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, check_case, mutation_case, run_mutations
from wb_connector import server

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = ("search_v9_live.json", "card_v4_live.json")
PRICE_PATHS = (r"^products\[\]\.(?:sizes\[\]\.price\.(?:product|total|basic)|salePriceU|priceU)$",)
PRICE_FIELDS = ("price_rub", "price_original_rub")
ID_FIELDS = ("nm_id",)
# The (name, mutation, seed) cases that removed or refused the basic amount: _extract_price_rub used to
# substitute product for it, changing the original field (fixed 2026-11-17).
ORIGINAL_PRICE_SEEDS = {
    "search_v9_live.json": (
        2,
        5,
        6,
        7,
        10,
        13,
        14,
        16,
        17,
        23,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        43,
        45,
        46,
        47,
        48,
        49,
    ),
    "card_v4_live.json": (
        1,
        2,
        3,
        4,
        6,
        8,
        10,
        14,
        15,
        18,
        19,
        20,
        21,
        22,
        26,
        28,
        29,
        31,
        32,
        33,
        37,
        39,
        42,
        43,
        46,
        49,
    ),
}
ROLE_CASES = tuple(
    (name, mutation, seed)
    for name, seeds in ORIGINAL_PRICE_SEEDS.items()
    for mutation in ("m2", "m4")
    for seed in seeds
)
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _parse(raw: str) -> list:
    return [server._card_item_dict(product) for product in json.loads(raw)["products"]]


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


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price", "R-WB-ORIGINAL-PRICE-ROLE")
@pytest.mark.parametrize("name,mutation,seed", ROLE_CASES)
def test_missing_original_price_stays_unknown(name: str, mutation: str, seed: int) -> None:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    mutated = mutation_case(
        raw, mutation, seed, kind="json", price_paths=PRICE_PATHS, price_nodes=(), markers=DECOY_MARKERS
    )
    check_case(
        mutated,
        _parse(raw),
        _parse,
        clean_raw=raw,
        mutation=mutation,
        kind="json",
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        allowed_errors=(ConnectorError,),
    )


# END_BLOCK_MINIMAL_VIOLATIONS
