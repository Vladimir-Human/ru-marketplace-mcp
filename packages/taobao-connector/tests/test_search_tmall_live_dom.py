"""Real 2026-10-30 Taobao/Tmall tiles and an intentionally excluded ad tracker.

All markup is trimmed from the owner's saved rendered page, not invented.
Ground truth was read from that raw capture before trimming. Node/jsdom is
optional, as in the other captured DOM tests; no page resources are loaded.
"""
# START_MODULE_CONTRACT
# PURPOSE: Prove mixed Taobao/Tmall extraction and loading-marker transport on captured DOM.
# SCOPE: Offline jsdom plus public search mapping; skips only if Node/jsdom is unavailable.
# DEPENDS: pytest; mcp_core.domtest; taobao_connector.server; provenance-pinned captures.
# LINKS: M-TAOBAO; R-TAOBAO-TMALL-SEARCH; R-TAOBAO-GRID-LOADING; R-FIXTURES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_tmall_tiles_keep_real_host_and_product_fields: Return both real Tmall products through search.
# test_ad_click_tracker_is_excluded: Keep the id-bearing advertiser-billing tracker out.
# test_taobao_tiles_preserve_product_fields: Preserve the two ordinary Taobao products.
# test_loading_marker_is_transported_from_the_live_wall: Carry raw loading evidence without a JS verdict.
# test_loading_marker_ignores_scripts_and_styles: Exclude non-rendered script/style wording.
# END_MODULE_MAP

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp_core.cache import TTLCache
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "search_grid_iphone15_live.html"
PROVENANCE = json.loads(FIXTURE.with_suffix(".provenance.json").read_text(encoding="utf-8"))
PAGE_URL = PROVENANCE["url"]
GROUND_TRUTH = PROVENANCE["ground_truth"]["items"]


def _extract(fixture: Path) -> dict:
    try:
        return run_extractor(server._SEARCH_EXTRACT_JS, fixture, page_url=PAGE_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def capture_payload():
    return _extract(FIXTURE)


@pytest.fixture
async def search_result(monkeypatch, capture_payload):
    monkeypatch.setattr(server, "_cache", TTLCache(ttl_s=300))

    async def render(url, extract_js, wait_ms, ctx):
        return capture_payload

    monkeypatch.setattr(server, "_cdp_render", render)
    return await server.taobao_search("iphone 15")


def _assert_product_fields(got, expected):
    for field in ("item_id", "title", "price_cny", "shop_name", "sales", "location", "url"):
        assert getattr(got, field) == expected[field], field


# START_BLOCK_MIXED_GRID
async def test_tmall_tiles_keep_real_host_and_product_fields(search_result):
    """R-TAOBAO-TMALL-SEARCH: both captured Tmall products retain identity, price and host."""
    expected = [it for it in GROUND_TRUTH if it["kind"] == "tmall"]
    assert len(expected) == 2
    got = {it.item_id: it for it in search_result.items}
    for product in expected:
        assert product["item_id"] in got, "A real Tmall tile was skipped"
        _assert_product_fields(got[product["item_id"]], product)
    assert search_result.count == 4


def test_ad_click_tracker_is_excluded(capture_payload):
    """R-TAOBAO-TMALL-SEARCH: an id-bearing real ad is not an ordinary product URL."""
    expected_ads = [it for it in GROUND_TRUTH if it["kind"] == "simba_ad"]
    assert len(expected_ads) == 1
    ad = expected_ads[0]
    assert ad["item_id"]
    assert "click.simba.taobao.com/cc_im" in FIXTURE.read_text(encoding="utf-8")
    assert ad["item_id"] not in {it["item_id"] for it in capture_payload["items"]}
    assert ad["title"] not in {it["title"] for it in capture_payload["items"]}
    assert all("click.simba.taobao.com" not in it["url"] for it in capture_payload["items"])


async def test_taobao_tiles_preserve_product_fields(search_result):
    """R-TAOBAO-TMALL-SEARCH: adding Tmall must leave ordinary Taobao values unchanged."""
    expected = [it for it in GROUND_TRUTH if it["kind"] == "taobao"]
    assert len(expected) == 2
    got = {it.item_id: it for it in search_result.items}
    for product in expected:
        _assert_product_fields(got[product["item_id"]], product)


# END_BLOCK_MIXED_GRID


# START_BLOCK_LOADING_MARKER


def test_loading_marker_is_transported_from_the_live_wall():
    """R-TAOBAO-GRID-LOADING: the real login wall also says 加载中; Python must decide the wall."""
    payload = _extract(FIXTURES / "search_login_wall_live.html")
    assert payload["grid_loading"] is True
    assert payload["items"] == []
    assert server._login_wall_markers(payload)


def test_loading_marker_ignores_scripts_and_styles(tmp_path):
    """R-TAOBAO-GRID-LOADING: synthetic script/style wording on a real grid is not visible evidence."""
    html = FIXTURE.read_text(encoding="utf-8")
    page = tmp_path / "grid_script_loading.html"
    page.write_text(
        html.replace("</body>", '<script>const state = "加载中";</script><style>/* 加载中 */</style></body>'),
        encoding="utf-8",
    )
    payload = _extract(page)
    assert payload["grid_loading"] is False
    assert len(payload["items"]) == 4


# END_BLOCK_LOADING_MARKER
