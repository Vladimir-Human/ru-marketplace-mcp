"""m1-m4 over Detsky Mir's committed category and card parser inputs."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent products and reject invented current/old prices or product ids.
# SCOPE: Category/card JSON captures; pure _parse_product and _product_node only.
# DEPENDS: pytest; stdlib json,pathlib; detmir_connector.server; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Both inputs named by the shape-reference companion tests.
# _parse: Normalize category items or the unwrapped card through the real product parser.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from detmir_connector import server
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = ("category_live.json", "card_live.json")
PRICE_PATHS = (
    r"^(?:items\[\]|item)\.(?:price|old_price|final_price|prices\.(?:sale|current))(?:\.(?:price|value|amount))?$",
)
PRICE_FIELDS = ("price_rub", "old_price_rub")
ID_FIELDS = ("product_id",)


def _parse(name: str, raw: str) -> list | dict:
    value = json.loads(raw)
    if name.startswith("category_"):
        return [server._parse_product(item).model_dump() for item in value["items"]]
    return server._parse_product(server._product_node(value)).model_dump()


# END_BLOCK_CAPTURE_CASES
# START_BLOCK_SEEDED_TESTS


@pytest.mark.req("R-CAPTURE-MUTATION.equivalent")
@pytest.mark.parametrize("name", CASES)
def test_capture_equivalent(name: str) -> None:
    run_mutations(
        (FIXTURES / name).read_text(encoding="utf-8"),
        lambda raw: _parse(name, raw),
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
        lambda raw: _parse(name, raw),
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
