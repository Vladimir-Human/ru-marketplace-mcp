"""The shared listing price gate, derived from R-SELFCHECK-GATES-PRICE.

START_MODULE_CONTRACT
PURPOSE: Prove the one count-independent decision for priceless listings.
SCOPE: Pure unit test; no transport or source-specific price readers.
DEPENDS: pytest; mcp_core.resilience.
LINKS: M-RESILIENCE; R-SELFCHECK-GATES-PRICE.
END_MODULE_CONTRACT
START_MODULE_MAP
test_price_gate: Empty listings are not price drift; any identified but unpriced listing is.
END_MODULE_MAP
"""

import pytest
from mcp_core import resilience

# START_BLOCK_PRICE_GATE


@pytest.mark.req("R-SELFCHECK-GATES-PRICE")
@pytest.mark.parametrize(
    ("identified", "priced", "expected"),
    [(0, 0, None), (2, 0, "no_prices"), (3, 0, "no_prices"), (3, 1, None)],
)
def test_price_gate(identified: int, priced: int, expected: str | None) -> None:
    """The decision depends on having items, not on a three-item minimum."""
    assert resilience.price_gate(identified, priced) == expected


# END_BLOCK_PRICE_GATE
