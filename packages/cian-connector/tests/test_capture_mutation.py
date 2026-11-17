"""m1-m4 over Cian sale, monthly rent and daily search/card captures."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent offers and prohibit invented headline, deposit or history prices.
# SCOPE: Six committed JSON inputs; pure _parse_offers/_parse_card only, no transport.
# DEPENDS: pytest; stdlib json,pathlib; cian_connector.server; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Sale/rent/daily fixtures for each parser named in shape_reference.py.
# _parse: Decode; search passes the envelope, card passes its offerData subtree.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# END_MODULE_MAP

import json
from pathlib import Path

import pytest
from cian_connector import server
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = (
    "search_sale_live.json",
    "search_rent_live.json",
    "search_daily_live.json",
    "card_sale_live.json",
    "card_rent_live.json",
    "card_daily_live.json",
)
PRICE_PATHS = (
    r"^(?:data\.offersSerialized\[\]|offerData\.offer)\.(?:bargainTerms\.(?:priceRur|price|deposit)|priceTotalRur|priceTotal)$",
    r"^offerData\.priceChanges\[\]\.priceData\.price$",
)
PRICE_FIELDS = ("price_rub", "deposit_rub")  # Recursive oracle includes price_history[].price_rub.
ID_FIELDS = ("offer_id",)


def _parse(name: str, raw: str) -> tuple | dict:
    value = json.loads(raw)
    return server._parse_offers(value) if name.startswith("search_") else server._parse_card(value["offerData"])


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
