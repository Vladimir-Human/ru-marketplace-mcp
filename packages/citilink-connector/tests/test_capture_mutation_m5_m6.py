"""P2-1 m5/m6 on Citilink's shipped jsdom extractors and captured HTML."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve regular prices and product ids under conditional-price and seller-text mutations.
# SCOPE: Existing search/card captures, including recommendations on an out-of-stock card.
# DEPENDS: pytest; stdlib pathlib; mcp_core.testing.capture_mutation; existing DOM parser adapter.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.conditional-price; R-CAPTURE-MUTATION.seller-text; R-CAPTURE-MUTATION.split-payment.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# TEXT_NODES: Only the product's Snippet__title and h1, not UI headings or spec fields.
# test_capture_m5_m6_task_facts: Shared JSDOM_SEEDS per capture and mutation family.
# END_MODULE_MAP

from pathlib import Path

import pytest
from mcp_core.testing.capture_mutation import (
    JSDOM_SEEDS,
    assert_facts_unchanged,
    preserving_case,
    run_preserving_mutations,
)

from . import test_capture_mutation as capture

# START_BLOCK_CAPTURE_MUTATIONS
TEXT_NODES = (r'data-meta-name=["\']Snippet__title["\']', r"^<h1(?:\s|>)")
# Reviewer red cases; one first-seed regression per fixture/family.
VIOLATIONS = (("search_grid.html", "m5", 0), ("card.html", "m5", 1))
# A failing fixture/family is represented by its minimal red case, not seed-by-seed xfails.
VIOLATION_FAMILIES = {(name, mutation) for name, mutation, _seed in VIOLATIONS}


@pytest.mark.req(
    "R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.seller-text", "R-CAPTURE-MUTATION.split-payment"
)
@pytest.mark.parametrize(
    "name,mutation",
    [
        (name, mutation)
        for name in capture.CASES
        for mutation in ("m5", "m6a", "m6e")
        if (name, mutation) not in VIOLATION_FAMILIES
    ],
)
def test_capture_m5_m6_task_facts(name: str, mutation: str, tmp_path: Path) -> None:
    run_preserving_mutations(
        (capture.FIXTURES / name).read_text(encoding="utf-8"),
        lambda raw: capture._parse(name, raw, tmp_path / "mutated.html"),
        fixture=name,
        mutation=mutation,
        kind="html",
        price_nodes=capture.PRICE_NODES,
        text_nodes=TEXT_NODES,
        price_fields=capture.PRICE_FIELDS if mutation == "m6a" else (capture.PRICE_FIELDS[0],),
        id_fields=capture.ID_FIELDS,
        seeds=JSDOM_SEEDS,
    )


# END_BLOCK_CAPTURE_MUTATIONS

# START_BLOCK_MINIMAL_VIOLATIONS


@pytest.mark.req("R-CAPTURE-MUTATION.conditional-price", "R-CAPTURE-MUTATION.split-payment")
@pytest.mark.parametrize("name,mutation,seed", VIOLATIONS)
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Reviewed adjacent conditional/split display changes the regular price (known gap G-CAPTURE-MUTATION-ADJACENT-PRICE)",
)
def test_adjacent_price_minimal_violation(name: str, mutation: str, seed: int, tmp_path: Path) -> None:
    raw = (capture.FIXTURES / name).read_text(encoding="utf-8")
    changed = preserving_case(raw, mutation, seed, kind="html", price_nodes=capture.PRICE_NODES)
    assert changed != raw, "minimal violation must mutate the priced capture"
    assert_facts_unchanged(
        capture._parse(name, raw, tmp_path / "clean.html"),
        capture._parse(name, changed, tmp_path / "mutated.html"),
        fields=(capture.PRICE_FIELDS[0], *capture.ID_FIELDS),
    )


# END_BLOCK_MINIMAL_VIOLATIONS
