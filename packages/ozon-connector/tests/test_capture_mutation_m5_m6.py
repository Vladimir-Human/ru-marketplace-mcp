"""P2-1 m5/m6 on the committed Ozon composer, including JSON-encoded widgets."""

# START_MODULE_CONTRACT
# PURPOSE: Keep regular prices and ids unchanged under adjacent prices and seller instructions.
# SCOPE: The shape-reference composer; only title atoms identified by tile-name are seller-editable.
# DEPENDS: pytest; mcp_core.testing.capture_mutation; existing capture parser adapter.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.conditional-price; R-CAPTURE-MUTATION.seller-text; R-CAPTURE-MUTATION.split-payment.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _title_atom: Exclude platform stock/rating atoms despite their shared textDS shape.
# test_capture_m5_m6_task_facts: Fifty deterministic seeds per fixture and family.
# END_MODULE_MAP

import pytest
from mcp_core.testing.capture_mutation import (
    PYTHON_SEEDS,
    assert_facts_unchanged,
    preserving_case,
    run_preserving_mutations,
)

from . import test_capture_mutation as capture

# START_BLOCK_CAPTURE_MUTATIONS
TEXT_PATHS = (r"^widgetStates\.tileGridDesktop.*\.items\[\]\.mainState\[\]\.textDS\.text$",)
# Only regular current entries are mutation targets, not originals or already card-only tiles.
# Red run, regular entries only: these insert-before seeds lose or replace the regular amount.
VIOLATION_SEEDS = (
    0,
    1,
    2,
    3,
    4,
    5,
    13,
    14,
    15,
    19,
    20,
    22,
    23,
    24,
    25,
    28,
    30,
    31,
    32,
    34,
    36,
    37,
    38,
    40,
    41,
    43,
    44,
    46,
)
EXCLUSIONS = tuple(
    ("search_composer_live.json", mutation, seed) for mutation in ("m5", "m6e") for seed in VIOLATION_SEEDS
)


def _regular_price_entry(value, parent: dict, key: str) -> bool:
    if key != "text" or parent.get("textStyle") != "PRICE":
        return False
    for encoded in value.get("widgetStates", {}).values():
        widget = getattr(encoded, "value", encoded)
        for item in widget.get("items", []):
            for atom in item.get("mainState", []):
                prices = atom.get("priceV2", {})
                if any(entry is parent for entry in prices.get("price", [])):
                    return prices.get("priceStyle", {}).get("styleType") != "CARD_PRICE"
    return False


def _title_atom(parent: dict, key: str) -> bool:
    return key == "text" and parent.get("testInfo", {}).get("automatizationId") == "tile-name"


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
        price_paths=capture.PRICE_PATHS,
        price_guard=_regular_price_entry,
        text_paths=TEXT_PATHS,
        text_guard=_title_atom,
        price_fields=capture.PRICE_FIELDS if mutation == "m6a" else (capture.PRICE_FIELDS[0],),
        id_fields=capture.ID_FIELDS,
        seeds=PYTHON_SEEDS,
        exclusions=EXCLUSIONS,
    )


# END_BLOCK_CAPTURE_MUTATIONS
# START_BLOCK_MINIMAL_VIOLATIONS


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
@pytest.mark.parametrize("mutation", ("m5", "m6e"))
@pytest.mark.parametrize("seed", (VIOLATION_SEEDS[0],))
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Ozon selects a preceding conditional/split display instead of the unchanged regular price; P2-1 forbids connector fixes",
)
def test_adjacent_price_keeps_ozon_regular_price(mutation: str, seed: int) -> None:
    raw = (capture.FIXTURES / "search_composer_live.json").read_text(encoding="utf-8")
    mutated = preserving_case(
        raw, mutation, seed, kind="json", price_paths=capture.PRICE_PATHS, price_guard=_regular_price_entry
    )
    assert_facts_unchanged(
        capture._parse(raw), capture._parse(mutated), fields=("price", "sku", "price_kind", "currency")
    )


# END_BLOCK_MINIMAL_VIOLATIONS
