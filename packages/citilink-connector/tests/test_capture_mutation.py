"""m1-m4 drive Citilink's real search/card extractors on committed HTML."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent output and prohibit invented current/old prices or product ids.
# SCOPE: Three search/card captures, including out-of-stock recommendations; jsdom, no browser/fakes.
# DEPENDS: pytest; stdlib pathlib; citilink_connector.server; mcp_core.dom/domtest; capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Every captured HTML input existing extractor tests use.
# _parse: Retain extractor output; interpret monetary text through the shared pure price mapper.
# test_capture_equivalent/test_capture_no_invented_price: Shared JSDOM_SEEDS per fixture and mutation.
# END_MODULE_MAP

from pathlib import Path

import pytest
from citilink_connector import server
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import JSDOM_SEEDS, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = {
    "search_grid.html": "https://www.citilink.ru/catalog/noutbuki/",
    "card.html": "https://www.citilink.ru/product/noutbuk-lenovo-loq-15irx9-i7-13645hx-16gb-ssd512gb-rtx4050-6gb-15-6-ip-2166619/",
    "card_out_of_stock.html": "https://www.citilink.ru/product/noutbuk-acer-gadget-erbook-air-ryzen-7-8745hs-16gb-ssd512gb-780m-14-ip-2147976/",
}
PRICE_NODES = (
    r"""data-meta-name=["'](?:Snippet__price|Snippet__old-price|PriceBlock__price|PriceBlock__additional-price)["']""",
    r"""\bdata-meta-price=["']""",
)
PRICE_FIELDS = ("price_rub", "old_price_rub")
ID_FIELDS = ("product_id",)
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _parse(name: str, raw: str, path: Path) -> dict:
    path.write_text(raw, encoding="utf-8", newline="\n")
    source = server._SEARCH_EXTRACT_JS if name.startswith("search_") else server._CARD_EXTRACT_JS
    try:
        payload = run_extractor(source, path, page_url=CASES[name])
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    tiles = payload.get("items", []) if name.startswith("search_") else [payload]
    monetary = []
    for tile in tiles:
        current, old = prices_from_tile(tile)
        monetary.append({"product_id": tile.get("product_id"), "price_rub": current, "old_price_rub": old})
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
