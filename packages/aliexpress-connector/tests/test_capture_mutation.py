"""m1-m4 drive the real AliExpress extractors against their committed captures."""

# START_MODULE_CONTRACT
# PURPOSE: Preserve equivalent extracted output and prohibit invented regular/old/coupon prices or ids.
# SCOPE: search_grid.html and the unpriced SSR card.html; real jsdom, no browser/tool/transport fake.
# DEPENDS: pytest; stdlib pathlib; aliexpress_connector.server; mcp_core.domtest; capture_mutation.
# LINKS: M-CAPTURE-MUTATION; R-CAPTURE-MUTATION.equivalent; R-CAPTURE-MUTATION.no-invented-price.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# CASES: Committed search/card inputs and the page URLs used by their extractor tests.
# _parse: Real extractor plus the source's pure price interpretation, retaining raw output for m1.
# test_capture_equivalent/test_capture_no_invented_price: Shared JSDOM_SEEDS per fixture and mutation.
# END_MODULE_MAP

from pathlib import Path

import pytest
from aliexpress_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.errors import ConnectorError
from mcp_core.resilience import DECOY_MARKERS
from mcp_core.testing.capture_mutation import JSDOM_SEEDS, run_mutations

# START_BLOCK_CAPTURE_CASES
FIXTURES = Path(__file__).parent / "fixtures"
CASES = {
    "search_grid.html": "https://aliexpress.ru/wholesale?SearchText=realme+watch",
    "card.html": "https://aliexpress.ru/item/1005010003103368.html",
}
# The committed grid has unnamed currency-bearing div leaves, not a named price class.
PRICE_NODES = (r"@text:\d[\d\s.,]*\s*₽",)
PRICE_FIELDS = ("price_rub", "old_price_rub", "coupon_price_rub")
ID_FIELDS = ("item_id", "sku_id")
EXCLUSIONS: tuple[tuple[str, str, int], ...] = ()


def _parse(name: str, raw: str, path: Path) -> dict:
    path.write_text(raw, encoding="utf-8", newline="\n")
    source = server._SEARCH_EXTRACT_JS if name.startswith("search_") else server._CARD_EXTRACT_JS
    try:
        payload = run_extractor(source, path, page_url=CASES[name])
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    prices = []
    if name.startswith("search_"):
        for tile in payload.get("items", []):
            current, old, coupon = server._tile_prices(tile)
            prices.append(
                {
                    "item_id": tile.get("item_id"),
                    "sku_id": tile.get("sku_id"),
                    "price_rub": current,
                    "old_price_rub": old,
                    "coupon_price_rub": coupon,
                }
            )
    else:
        current, old = server._card_prices(payload)
        prices.append(
            {
                "item_id": payload.get("item_id"),
                "sku_id": payload.get("sku_id"),
                "price_rub": current,
                "old_price_rub": old,
            }
        )
    return {"extracted": payload, "monetary": prices}


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
