"""m1-m4 drive Taobao's real search/card extractors on every committed HTML input."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent extraction and prohibit invented CNY prices or item identities.
# SCOPE: Six model/live/mixed/wall/search/card captures; real jsdom, no browser/fake transport.
# DEPENDS: pytest; stdlib pathlib; taobao_connector.server; mcp_core.dom/domtest; capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Every committed same-extractor HTML input existing tests use.
# _parse: Retain the shipped extractor's output; the shared pure mapper decides its current/old CNY prices.
# test_capture_equivalent/test_capture_no_invented_price: Half the shared JSDOM_SEEDS (stop rule: >120 s).
# END_MODULE_MAP

from pathlib import Path

import pytest
from mcp_core.dom import prices_from_tile
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import JSDOM_SEEDS, run_mutations
from taobao_connector import server

# Halved per the plan's stop rule: six fixtures at JSDOM_SEEDS took 211 s locally (2026-11-17).
TAOBAO_SEEDS = (JSDOM_SEEDS + 1) // 2

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = {
    "search_grid.html": "https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA",
    "search_grid_live.html": "https://s.taobao.com/search?q=%E8%BF%9E%E8%A1%A3%E8%A3%99",
    "search_grid_iphone15_live.html": "https://s.taobao.com/search?q=iphone%2015&page=1",
    "search_login_wall_live.html": "https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA&page=1",
    "item_card.html": "https://item.taobao.com/item.htm?id=123456789012",
    "item_card_live.html": "https://item.taobao.com/item.htm?id=1048008062319",
}
# Whole price containers only: removing an integer/fraction/unit sub-node is not m2.
PRICE_NODES = (
    r"""class=["'][^"']*\b(?:priceWrapper[^\s"']*|PriceWrapper[^\s"']*|Price--priceText[^\s"']*|Price--current|Price--original|Price--coupon|highlightPrice--LlVWiXXs|subPrice--KfQ0yn4v)(?=[\s"'])""",
)
PRICE_FIELDS = ("price_cny", "old_price_cny")  # Both monetary outputs of the pure extractor price mapper.
ID_FIELDS = ("item_id",)


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
        monetary.append({"item_id": tile.get("item_id"), "price_cny": current, "old_price_cny": old})
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
        seeds=TAOBAO_SEEDS,
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
        seeds=TAOBAO_SEEDS,
    )


# END_BLOCK_SEEDED_TESTS
