"""m1-m4 over every committed input used by the Yandex SSR search/card parsers."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent SSR output and prohibit invented prices or product identities.
# SCOPE: All eight committed HTML captures; pure ssr.parse_search/parse_card, no fetch/tool shell.
# DEPENDS: pytest; stdlib pathlib; yandex_connector.ssr; mcp_core.testing.capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Fixture-to-parser table; embedded noframes/ld+json/data-zone-data amounts are mutated.
# _parse: Invoke the same pure parser as the shape reference.
# test_capture_equivalent/test_capture_no_invented_price: Shared PYTHON_SEEDS per fixture and mutation.
# test_removed_discount_price_keeps_the_old_price_in_its_role: The m2 seeds that once promoted the base price.
# END_MODULE_MAP

from pathlib import Path

import pytest
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import PYTHON_SEEDS, check_case, mutation_case, run_mutations
from yandex_connector import ssr

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = (
    "search_washer.html",
    "search_iphone.html",
    "search_empty.html",
    "search_kettle.html",
    "search_telefon_zone.html",
    "card_washer.html",
    "card_no_rating.html",
    "card_empty_shell.html",
)
PRICE_PATHS = (
    r"(?:^|\.)price$",
    r"(?:^|\.)price\.(?:value|valueFmt)$",
    r"(?:^|\.)priceValue$",
    r"(?:actualPrice|oldPrice|initialPrice)\.amount\.intPart$",
)
PRICE_FIELDS = ("price_rub", "price_with_plus", "price_old_rub", "price_before_discount_rub")
ID_FIELDS = ("product_id", "sku_id")
# The m2 seeds that removed a discounted row's withDiscount priceValue: ssr.parse_zone_items used to
# fall back to the base (struck) price as the everyday price (fixed 2026-11-17, issue #303).
DISCOUNT_PRICE_SEEDS = (1, 3, 9, 11, 20, 37, 40, 44)
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _parse(name: str, raw: str) -> dict:
    return ssr.parse_search(raw) if name.startswith("search_") else ssr.parse_card(raw)


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
        kind="html",
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
        lambda raw: _parse(name, raw),
        fixture=name,
        mutation=mutation,
        kind="html",
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


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price", "R-YANDEX-DISCOUNT-BADGE")
@pytest.mark.parametrize("seed", DISCOUNT_PRICE_SEEDS)
def test_removed_discount_price_keeps_the_old_price_in_its_role(seed: int) -> None:
    name = "search_telefon_zone.html"
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    mutated = mutation_case(
        raw, "m2", seed, kind="html", price_paths=PRICE_PATHS, price_nodes=(), markers=DECOY_MARKERS
    )
    check_case(
        mutated,
        _parse(name, raw),
        lambda text: _parse(name, text),
        clean_raw=raw,
        mutation="m2",
        kind="html",
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        allowed_errors=(ConnectorError,),
    )


# END_BLOCK_MINIMAL_VIOLATIONS
