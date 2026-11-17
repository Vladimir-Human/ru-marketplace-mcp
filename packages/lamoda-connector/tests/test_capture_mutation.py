"""m1-m4 drive Lamoda's real search extractor on all committed search HTML."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent extraction and prohibit invented current/old prices or sku identities.
# SCOPE: Model/live grids and captured refusal page; real jsdom, no browser/fake transport.
# DEPENDS: pytest; stdlib pathlib; lamoda_connector.server; mcp_core.dom/domtest; capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price; R-OLD-PRICE-BY-MARKER.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: All committed inputs existing search-extractor tests use.
# _parse: Retain real extractor output and interpret price text with the source's shared pure mapper.
# test_capture_equivalent/test_capture_no_invented_price: Shared JSDOM_SEEDS per fixture and mutation.
# ROLE_CASES/EXCLUSIONS: Former m2/m4 role failures now run without exclusions.
# test_missing_highest_old_price_stays_unknown: Missing/refused marked old prices never promote a lower role.
# END_MODULE_MAP

from pathlib import Path

import pytest
from lamoda_connector import server
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import JSDOM_SEEDS, check_case, mutation_case, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = ("search_grid.html", "search_grid_live.html", "refusal_page_live.html")
PAGE_URL = "https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8"
PRICE_NODES = (
    r"""class=["'][^"']*\b(?:d-card__current-price|d-card__old-price|x-product-card-description__price-new|x-product-card-description__price-old|x-product-card-description__price-second-old)(?=[\s"'])""",
)
PRICE_FIELDS = ("price_rub", "old_price_rub")
ID_FIELDS = ("sku",)
# The captured 12 999/8 999/6 569 pair loses or refuses its marked 12 999 price:
# prices_from_tile used to promote 8 999 into that role (fixed 2026-11-17).
ROLE_CASES = (("search_grid_live.html", "m2", 2), ("search_grid_live.html", "m4", 2))
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _parse(name: str, raw: str, path: Path) -> dict:
    path.write_text(raw, encoding="utf-8", newline="\n")
    try:
        payload = run_extractor(server._SEARCH_EXTRACT_JS, path, page_url=PAGE_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    monetary = []
    for tile in payload.get("items", []):
        current, old = prices_from_tile(tile)
        monetary.append({"sku": tile.get("sku"), "price_rub": current, "old_price_rub": old})
    return {"extracted": payload, "monetary": monetary}


# END_BLOCK_CAPTURE_CASES
# START_BLOCK_SEEDED_TESTS


@pytest.mark.req("R-CAPTURE-MUTATION.equivalent")
@pytest.mark.parametrize("name", CASES)
def test_capture_equivalent(name: str, tmp_path: Path) -> None:
    run_mutations(
        (FIXTURES / name).read_text(encoding="utf-8"),
        lambda raw: _parse(name, raw, tmp_path / "mutated.html"),
        fixture=name,
        mutation="m1",
        kind="html",
        price_paths=(),
        price_nodes=PRICE_NODES,
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        markers=DECOY_MARKERS,
        allowed_errors=(ConnectorError,),
        seeds=JSDOM_SEEDS,
        exclusions=EXCLUSIONS,
    )


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price")
@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("mutation", ("m2", "m3", "m4"))
def test_capture_no_invented_price(name: str, mutation: str, tmp_path: Path) -> None:
    run_mutations(
        (FIXTURES / name).read_text(encoding="utf-8"),
        lambda raw: _parse(name, raw, tmp_path / "mutated.html"),
        fixture=name,
        mutation=mutation,
        kind="html",
        price_paths=(),
        price_nodes=PRICE_NODES,
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        markers=DECOY_MARKERS,
        allowed_errors=(ConnectorError,),
        seeds=JSDOM_SEEDS,
        exclusions=EXCLUSIONS,
    )


# END_BLOCK_SEEDED_TESTS
# START_BLOCK_MINIMAL_VIOLATIONS


@pytest.mark.req("R-CAPTURE-MUTATION.no-invented-price", "R-OLD-PRICE-BY-MARKER")
@pytest.mark.parametrize("name,mutation,seed", ROLE_CASES)
def test_missing_highest_old_price_stays_unknown(name: str, mutation: str, seed: int, tmp_path: Path) -> None:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    mutated = mutation_case(
        raw, mutation, seed, kind="html", price_paths=(), price_nodes=PRICE_NODES, markers=DECOY_MARKERS
    )
    check_case(
        mutated,
        _parse(name, raw, tmp_path / "clean.html"),
        lambda text: _parse(name, text, tmp_path / "mutated.html"),
        clean_raw=raw,
        mutation=mutation,
        kind="html",
        price_fields=PRICE_FIELDS,
        id_fields=ID_FIELDS,
        allowed_errors=(ConnectorError,),
    )


# END_BLOCK_MINIMAL_VIOLATIONS
