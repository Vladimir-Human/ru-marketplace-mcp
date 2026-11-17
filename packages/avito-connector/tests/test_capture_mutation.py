"""m1-m4 over the committed Avito js/items search captures."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent listings and reject invented prices or changed item identities.
# SCOPE: Three js/items JSON captures; pure _parse_search_items only, no fetch/tool shell.
# DEPENDS: pytest; stdlib json,pathlib; avito_connector.server; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Live, reserved and sale captures used by existing parser tests.
# _parse: Decode the fixture and call server._parse_search_items.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from avito_connector import server
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = ("js_items_live.json", "js_items_reserved.json", "js_items_sale.json")
PRICE_PATHS = (
    r"^(?:items|catalog\.items|data\.items)\[\]\.(?:price|priceRub|price_rub|priceDetailed\.(?:value|price))$",
    r"^(?:items|catalog\.items|data\.items)\[\]\.iva\.PriceStep\[\]\.payload\.(?:priceDetailed\.value|old_string)$",
)
PRICE_FIELDS = ("price_rub", "old_price_rub")
ID_FIELDS = ("item_id",)


def _parse(raw: str) -> tuple:
    return server._parse_search_items(json.loads(raw))


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
