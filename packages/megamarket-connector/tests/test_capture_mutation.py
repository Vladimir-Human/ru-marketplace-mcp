"""m1-m4 over Megamarket's committed search API capture."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent listings and reject invented current/old prices or item ids.
# SCOPE: search_live.json; pure _parse_items only, no browser/post/tool shell.
# DEPENDS: pytest; stdlib json,pathlib; megamarket_connector.server; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: The captured input named in shape_reference.py and existing parser tests.
# _parse: Decode and invoke the shipped nested goods/favoriteOffer parser.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_mutations
from megamarket_connector import server

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = ("search_live.json",)
PRICE_PATHS = (
    r"^(?:items|goods|products|data\.items)\[\]\.(?:(?:goods|favoriteOffer)\.)?(?:finalPrice|price|currentPrice|oldPrice|basePrice|strikePrice)$",
)
PRICE_FIELDS = ("price_rub", "old_price_rub")
ID_FIELDS = ("item_id",)


def _parse(raw: str) -> tuple:
    return server._parse_items(json.loads(raw))


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
    )


# END_BLOCK_SEEDED_TESTS
