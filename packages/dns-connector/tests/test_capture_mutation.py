"""m1-m4 drive DNS's real search/card extractors on their committed HTML."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent extraction and prohibit invented current/old prices or product ids.
# SCOPE: Both shape-reference captures; jsdom with pure price normalization, no browser/fake transport.
# DEPENDS: pytest; stdlib pathlib; dns_connector.server; mcp_core.domtest/resilience; capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Search and card captures/page URLs pinned by existing extractor tests.
# _parse: Run the shipped extractor; normalize its two monetary strings exactly as the source does.
# test_capture_equivalent/test_capture_no_invented_price: Shared JSDOM_SEEDS per fixture and mutation.
# END_MODULE_MAP

from pathlib import Path

import pytest
from dns_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS, price_from_texts
from mcp_core.testing.capture_mutation import JSDOM_SEEDS, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = {
    "search_grid.html": "https://www.dns-shop.ru/search/?q=%D0%BD%D0%BE%D1%83%D0%B1%D1%83%D0%BA",
    "card.html": "https://www.dns-shop.ru/product/b7a1667f9b19ed20/noutbuk-huawei/",
}
# Exact class token, not the price-wrap container or instalment sub-block.
PRICE_NODES = (r"""class=["'][^"']*\bproduct-buy__price(?=[\s"'])""",)
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
        current = price_from_texts(tile.get("price_text"), tile.get("price_rub"))
        old = price_from_texts(tile.get("old_price_text"), tile.get("old_price_rub"))
        if current is not None and old is not None and old <= current:
            old = None
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
