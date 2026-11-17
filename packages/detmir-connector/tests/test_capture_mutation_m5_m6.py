"""P2-1 m5/m6 on the committed Detsky Mir category/card captures."""

# START_MODULE_CONTRACT
# PURPOSE: Keep regular prices and product ids unchanged under seller attacks and adjacent prices.
# SCOPE: Existing category/card parsers; title, description and vendor title only.
# DEPENDS: pytest; mcp_core.testing.capture_mutation; existing capture parser adapter.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.conditional-price; R-CAPTURE-MUTATION.seller-text; R-CAPTURE-MUTATION.split-payment.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# TEXT_PATHS: Seller fields only; brands, availability, ids and task facts remain intact.
# test_capture_m5_m6_task_facts: Fifty deterministic seeds per fixture and family.
# END_MODULE_MAP

import pytest
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_preserving_mutations

from . import test_capture_mutation as capture

# START_BLOCK_CAPTURE_MUTATIONS
TEXT_PATHS = (r"^(?:items\[\]|item)\.(?:title|name|description|vendor\.title)$",)
PRICE_PATHS = (r"^(?:items\[\]|item)\.(?:price|final_price|prices\.(?:sale|current))(?:\.(?:price|value|amount))?$",)
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


@pytest.mark.req(
    "R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.seller-text", "R-CAPTURE-MUTATION.split-payment"
)
@pytest.mark.parametrize("name", capture.CASES)
@pytest.mark.parametrize("mutation", ("m5", "m6a", "m6e"))
def test_capture_m5_m6_task_facts(name: str, mutation: str) -> None:
    run_preserving_mutations(
        (capture.FIXTURES / name).read_text(encoding="utf-8"),
        lambda raw: capture._parse(name, raw),
        fixture=name,
        mutation=mutation,
        kind="json",
        price_paths=PRICE_PATHS,
        text_paths=TEXT_PATHS,
        price_fields=capture.PRICE_FIELDS if mutation == "m6a" else (capture.PRICE_FIELDS[0],),
        id_fields=capture.ID_FIELDS,
        seeds=PYTHON_SEEDS,
        exclusions=EXCLUSIONS,
    )


# END_BLOCK_CAPTURE_MUTATIONS
