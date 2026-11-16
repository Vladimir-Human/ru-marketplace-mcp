"""An unfilled search page is not parser drift (R-CITILINK-UNFILLED-PAGE).

On 2026-11-16 Citilink served a search page with 36 product tiles that held no
link, picture or price: placeholders the page's own scripts fill in, and those
scripts hung. The tool called that parser_drift ("the DOM shape moved").
The HTML below is synthetic, shaped like that page's tile wrappers.
"""

from __future__ import annotations

import json

import pytest
from citilink_connector import server
from fastmcp.exceptions import ToolError
from mcp_core.domtest import JsdomUnavailable, run_extractor

URL = "https://www.citilink.ru/search/?text=ssd"
EMPTY_TILE = '<div data-meta-name="SnippetProductVerticalLayout"><div class="skeleton"><span></span></div></div>'
FILLED_TILE = (
    '<div data-meta-name="SnippetProductVerticalLayout">'
    '<a data-meta-name="Snippet__title" href="https://www.citilink.ru/product/ssd-samsung-990-pro-1tb-1874263/">'
    "SSD Samsung 990 PRO 1TB</a>"
    '<span data-meta-name="Snippet__price" data-meta-price="12990">12 990 <span>₽</span></span></div>'
)
UNFILLED = {"items": [], "title": "ssd - купить", "qrator_wall": False, "placeholder_tiles": 36}


def _extract(tmp_path, tiles: str) -> dict:
    page = tmp_path / "page.html"
    page.write_text(f'<html><body><div data-meta-name="ProductListLayout">{tiles}</div></body></html>', "utf-8")
    try:
        payload = run_extractor(server._SEARCH_EXTRACT_JS, page, page_url=URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    return json.loads(payload) if isinstance(payload, str) else payload


@pytest.mark.req("R-CITILINK-UNFILLED-PAGE")
def test_extractor_counts_tiles_without_a_product_link(tmp_path) -> None:
    payload = _extract(tmp_path, EMPTY_TILE * 3)
    assert payload["items"] == []
    assert payload["placeholder_tiles"] == 3


@pytest.mark.req("R-CITILINK-UNFILLED-PAGE")
def test_filled_tiles_are_not_placeholders(tmp_path) -> None:
    payload = _extract(tmp_path, FILLED_TILE + EMPTY_TILE * 2)
    assert len(payload["items"]) == 1
    assert payload["placeholder_tiles"] == 2


def _patch_render(monkeypatch, payload, calls):
    async def fake_render(url, extract_js, wait_ms, ctx):
        calls.append(url)
        return payload

    monkeypatch.setattr(server, "_cdp_render", fake_render)


@pytest.mark.req("R-CITILINK-UNFILLED-PAGE")
async def test_search_on_an_unfilled_page_is_transport_down_and_not_cached(monkeypatch) -> None:
    server._cache._data.clear()
    calls: list[str] = []
    _patch_render(monkeypatch, UNFILLED, calls)
    for _ in range(2):
        with pytest.raises(ToolError) as excinfo:
            await server.citilink_search("ssd samsung 990 pro 1tb")
        error = json.loads(str(excinfo.value))
        assert error["error"] == "transport_down"
        assert "never filled in (36 empty placeholder tiles)" in error["message"]
    assert len(calls) == 2


@pytest.mark.req("R-CITILINK-UNFILLED-PAGE")
async def test_zero_tiles_without_placeholders_stays_parser_drift(monkeypatch) -> None:
    server._cache._data.clear()
    _patch_render(monkeypatch, {**UNFILLED, "placeholder_tiles": 0}, [])
    with pytest.raises(ToolError) as excinfo:
        await server.citilink_search("ssd samsung 990 pro 1tb")
    assert json.loads(str(excinfo.value))["error"] == "parser_drift"


@pytest.mark.req("R-CITILINK-UNFILLED-PAGE")
async def test_selfcheck_on_an_unfilled_page_is_inconclusive_not_drift(monkeypatch) -> None:
    server._cache._data.clear()
    _patch_render(monkeypatch, UNFILLED, [])
    result = await server.citilink_selfcheck()
    assert result.status != "drift_detected"
    search = result.model_dump()["checks"]["search"]
    assert search["state"] == "inconclusive"
    assert search["reason"] == "transport_down"
