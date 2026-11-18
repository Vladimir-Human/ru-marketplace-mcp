"""R-ALIEXPRESS-SEARCH-RENDERED: the grid is read once its item links appear.

Audit cycle 2 (2026-11-17): eight parallel ``compare_prices`` calls made
AliExpress answer parser_drift «rendered search page yielded zero product
tiles» in four of them (after the full fixed 10 s wait, 14-28 s elapsed),
while AliExpress alone returned priced tiles. The grid renders late when
several tabs load at once.

Everything here is offline. The fakes replace the operator's Chrome only: the
page answers the real extractor with the payload a real grid produces — no
tiles while the grid has not rendered, then tiles with prices — and the
``open_page`` seam records how many navigations the connector made.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aliexpress_connector import server
from fastmcp.exceptions import ToolError

PRICED_TILE = {
    "item_id": "1005010003103368",
    "title": "Realme Watch 5 смарт-часы",
    "price_texts": {"attached": ["6 967 ₽", "2 939 ₽"], "other": ["2 334 купили"]},
    "rating": "4,7",
    "orders": "2 334",
    "sku_id": "12000050856992499",
    "url": "https://aliexpress.ru/item/1005010003103368.html",
}


def _payload(items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """An extractor payload: the fields the real search extractor always emits."""
    payload = {
        "punish": False,
        "page_title": "realme watch с бесплатной доставкой на AliExpress",
        "items": items,
        "item_links": len(items),
        "other_item_links": 0,
        "links": 120,
        "text_len": 0,
        "empty_notice": False,
    }
    payload.update(extra)
    return payload


class _FakePage:
    """A rendered grid: the extractor answers follow a script."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.reads = 0
        self.url = "https://aliexpress.ru/wholesale?SearchText=realme+watch"

    async def evaluate(self, expression: str, arg: object = None) -> Any:
        self.reads += 1
        payload = self._payloads.pop(0) if len(self._payloads) > 1 else self._payloads[0]
        return json.dumps(payload)


def _patch_open_page(monkeypatch: pytest.MonkeyPatch, page: _FakePage) -> list[str]:
    """Replace the browser seam and record every navigation."""
    opened: list[str] = []

    @asynccontextmanager
    async def fake_open_page(url: str, wait_ms: int = 0, **kwargs: Any):
        opened.append(url)
        yield page

    async def no_wait() -> None:
        return None

    monkeypatch.setattr(server, "open_page", fake_open_page)
    monkeypatch.setattr(server, "_polite_wait", no_wait)
    # raising=False: the wait bounds are this change's own constants, and a run
    # against the old connector must fail on the behaviour, not on a missing name.
    monkeypatch.setattr(server, "_SEARCH_RENDER_POLL_S", 0.05, raising=False)
    monkeypatch.setattr(server, "_SEARCH_POLL_INTERVAL_S", 0.01, raising=False)
    server._cache._data.clear()
    return opened


@pytest.mark.req("R-ALIEXPRESS-SEARCH-RENDERED")
async def test_the_grid_is_read_once_the_item_links_appear(monkeypatch: pytest.MonkeyPatch) -> None:
    """The grid rendered after the fixed wait: the tool answers with its tiles."""
    page = _FakePage([_payload([]), _payload([PRICED_TILE])])
    opened = _patch_open_page(monkeypatch, page)

    result = await server.aliexpress_search("realme watch")

    assert result.count == 1
    assert result.items[0].price_rub == 2939.0
    assert result.items[0].item_id == "1005010003103368"
    assert page.reads > 1, "the page was read once and given up on"
    assert len(opened) == 1, "a rendered grid needs no second navigation"


@pytest.mark.req("R-ALIEXPRESS-SEARCH-RENDERED")
async def test_an_empty_grid_is_opened_once_more_and_then_drifted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A grid that stays empty is read from one more page, then answers drift."""
    page = _FakePage([_payload([], other_item_links=7, links=340, text_len=12_345, empty_notice=False)])
    opened = _patch_open_page(monkeypatch, page)

    with pytest.raises(ToolError) as exc:
        await server.aliexpress_search("realme watch")

    error = json.loads(str(exc.value))
    assert error["error"] == "parser_drift"
    assert len(opened) == 2, "the empty page must be opened exactly once more"
    message = error["message"]
    assert "7 item link(s) of another shape" in message
    assert "340 link(s) in total" in message
    assert "12345 characters of text" in message
    assert "empty-result notice: no" in message


@pytest.mark.req("R-ALIEXPRESS-SEARCH-RENDERED")
async def test_the_drift_message_names_an_empty_result_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that says «nothing found» is a different case than an empty grid."""
    page = _FakePage([_payload([], empty_notice=True)])
    _patch_open_page(monkeypatch, page)

    with pytest.raises(ToolError) as exc:
        await server.aliexpress_search("realme watch")

    assert "empty-result notice: yes" in json.loads(str(exc.value))["message"]


@pytest.mark.req("R-ALIEXPRESS-SEARCH-RENDERED")
async def test_a_challenge_is_neither_polled_nor_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    """x5sec is the operator's to pass: the page is read once and left alone."""
    page = _FakePage([_payload([], punish=True, page_title="Пройдите проверку")])
    opened = _patch_open_page(monkeypatch, page)

    with pytest.raises(ToolError) as exc:
        await server.aliexpress_search("realme watch")

    assert "challenge" in str(exc.value) or "Пройдите" in str(exc.value)
    assert page.reads == 1, "a challenge page must not be polled"
    assert len(opened) == 1, "a challenge page must not be reopened"
