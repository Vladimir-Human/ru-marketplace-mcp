"""P2-1 m5/m6 over every committed Avito search-capture shape."""

# START_MODULE_CONTRACT
# PURPOSE: Keep regular prices and ids unchanged under conditional prices and seller-text attacks.
# SCOPE: Existing search captures; title, description and public seller-name fields only.
# DEPENDS: pytest; mcp_core.testing.capture_mutation; existing capture parser adapter.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.conditional-price; R-CAPTURE-MUTATION.seller-text; R-CAPTURE-MUTATION.split-payment.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# TEXT_PATHS: Seller fields, not platform price labels, category names or address facts.
# test_capture_m5_m6_task_facts: Fifty deterministic seeds per fixture and family.
# END_MODULE_MAP

import pytest
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, run_preserving_mutations

from . import test_capture_mutation as capture

# START_BLOCK_CAPTURE_MUTATIONS
TEXT_PATHS = (
    r"^(?:items|catalog\.items|data\.items)\[\]\.(?:title|name|description|sellerName|seller\.(?:name|title))$",
)
PRICE_PATHS = (
    r"^(?:items|catalog\.items|data\.items)\[\]\.(?:price|priceRub|price_rub|priceDetailed\.(?:value|price))$",
    r"^(?:items|catalog\.items|data\.items)\[\]\.iva\.PriceStep\[\]\.payload\.priceDetailed\.value$",
)
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


@pytest.mark.req(
    "R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.seller-text", "R-CAPTURE-MUTATION.split-payment"
)
@pytest.mark.parametrize("name", capture.CASES)
@pytest.mark.parametrize("mutation", ("m5", "m6a", "m6e"))
def test_capture_m5_m6_task_facts(name: str, mutation: str) -> None:
    run_preserving_mutations(
        (capture.FIXTURES / name).read_text(encoding="utf-8"),
        capture._parse,
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
