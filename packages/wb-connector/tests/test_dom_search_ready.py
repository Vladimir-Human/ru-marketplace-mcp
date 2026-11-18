"""R-WB-DOM-SEARCH-PRICED: a WB search page is read once its tiles show prices.

Audit cycle 2 (2026-11-17): eight parallel ``compare_prices`` calls read WB
through the Chrome DOM fallback and five of them came back with rows and no
price («5 results, 0 priced»), while WB alone answered 40 of 40 priced. The
client-side render of the search grid is slower when several tabs load at
once, so the page was read before it had prices.

Everything here is offline. The fakes replace the operator's Chrome only: the
page answers the real readiness probe and the real extractor with what a
rendered WB grid holds — tiles with prices once the render finished, tiles
without prices for a page of sold-out items, no tiles at all for a page that
had not rendered yet, and WB's own «ничего не нашлось» notice.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from wb_connector import dom_transport as D


class _FakePage:
    """A WB page whose readiness answers follow a script.

    The extractor answer follows the last readiness answer: a grid that has
    shown prices yields priced tiles, a grid that has not yields bare tiles.
    That is what a real page does — the prices arrive with the tiles, not
    after the extractor ran.
    """

    def __init__(self, probes: list[dict[str, Any]]) -> None:
        self._probes = list(probes)
        self.url = "https://www.wildberries.ru/"
        self.probe_calls = 0
        self.extract_calls = 0
        self._last: dict[str, Any] = {}

    def _next_probe(self) -> dict[str, Any]:
        if len(self._probes) > 1:
            return self._probes.pop(0)
        return self._probes[0]

    async def evaluate(self, expression: str, arg: object = None) -> Any:
        if expression == D.PROBE_JS:
            self.probe_calls += 1
            self._last = self._next_probe()
            return json.dumps(self._last)
        self.extract_calls += 1
        tiles = int(self._last.get("tiles") or 0)
        priced = int(self._last.get("priced") or 0)
        return json.dumps(
            {
                "title": "Wildberries",
                "url": self.url,
                "tiles": [
                    {
                        "nm": str(1000 + index),
                        "lines": (["1 234 ₽"] if priced else []) + ["Артикул", str(1000 + index)],
                        "label": "",
                        "del": [],
                        "wallet": [],
                        "wallet_icon": False,
                    }
                    for index in range(tiles)
                ],
            }
        )


def _patch_open_page(monkeypatch: pytest.MonkeyPatch, pages: list[Any]) -> tuple[list[str], list[int]]:
    """Replace the browser seam and record every navigation and pacing gap."""
    import mcp_core.transport.chrome_cdp as cdp

    opened: list[str] = []
    gaps: list[int] = []

    @asynccontextmanager
    async def fake_open_page(url: str, wait_ms: int = 0, *, allowed_hosts: Any = None):
        assert allowed_hosts == D.ALLOWED_HOSTS
        page = pages[len(opened)]
        opened.append(url)
        yield page

    async def record_gap() -> None:
        gaps.append(1)

    monkeypatch.setattr(cdp, "open_page", fake_open_page)
    monkeypatch.setattr(D, "_gap", record_gap)
    monkeypatch.setattr(D._settings, "dom_min_gap", 0.0)
    monkeypatch.setattr(D._settings, "dom_wait_s", 5.0)
    D._dom_cache.clear()
    return opened, gaps


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_search_is_read_after_the_tiles_show_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prices arrive with the tiles: the extract must not run before them."""
    page = _FakePage(
        [
            {"state": "ok", "tiles": 5, "priced": 0},
            {"state": "ok", "tiles": 5, "priced": 0},
            {"state": "ok", "tiles": 5, "priced": 5},
            {"state": "ok", "tiles": 5, "priced": 5},
        ]
    )
    opened, _ = _patch_open_page(monkeypatch, [page])

    data = _run(D.render(D.search_url("prices-late"), D.SEARCH_EXTRACT_JS, "search"))

    assert page.extract_calls == 1
    assert data["tiles"], "the search page carried no tiles"
    assert any("₽" in line for tile in data["tiles"] for line in tile["lines"]), "the tiles were read without prices"
    assert len(opened) == 1, "a priced page needs no second navigation"


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_tiles_that_stay_unpriced_are_taken_after_the_unpriced_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page of sold-out items shows no price: it is read after a bounded wait."""
    page = _FakePage([{"state": "ok", "tiles": 5, "priced": 0}])
    _patch_open_page(monkeypatch, [page])
    monkeypatch.setattr(D, "SEARCH_UNPRICED_WAIT_S", 1.5)

    started = time.monotonic()
    data = _run(D.render(D.search_url("sold-out"), D.SEARCH_EXTRACT_JS, "search"))
    elapsed = time.monotonic() - started

    assert elapsed >= 1.0, "the read did not wait for the prices to appear"
    assert page.extract_calls == 1
    assert data["tiles"], "the unpriced page still carries its tiles"


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_a_search_page_without_tiles_is_opened_once_more(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that had not rendered yet is opened again, paced like any navigation."""
    first = _FakePage([{"state": "ok", "tiles": 0}])
    second = _FakePage([{"state": "ok", "tiles": 0}])
    opened, gaps = _patch_open_page(monkeypatch, [first, second])
    monkeypatch.setattr(D._settings, "dom_wait_s", 0.5)

    url = D.search_url("not-rendered")
    data = _run(D.render(url, D.SEARCH_EXTRACT_JS, "search"))

    assert len(opened) == 2, "the empty page must be opened exactly once more"
    assert len(gaps) == 2, "the second navigation must be paced like the first"
    assert opened == [url, url]
    assert data["tiles"] == []
    assert data["page_says_empty"] is False
    assert D._dom_cache.get(url) is None, "an empty page must not enter the cache"


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_the_empty_result_notice_stops_the_read_without_a_second_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WB's own «ничего не нашлось» is an answer, not a page that has to render."""
    page = _FakePage([{"state": "ok", "tiles": 0, "empty": True}])
    opened, _ = _patch_open_page(monkeypatch, [page])

    data = _run(D.render(D.search_url("nothing"), D.SEARCH_EXTRACT_JS, "search"))

    assert len(opened) == 1, "a page with the empty-result notice needs no second navigation"
    assert data["page_says_empty"] is True


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_empty_notice_text_beside_tiles_still_waits_for_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """The notice regex reads the whole page; with tiles on it the read still waits for their prices."""
    page = _FakePage(
        [
            {"state": "ok", "tiles": 5, "priced": 0, "empty": True},
            {"state": "ok", "tiles": 5, "priced": 5, "empty": True},
            {"state": "ok", "tiles": 5, "priced": 5, "empty": True},
        ]
    )
    opened, _ = _patch_open_page(monkeypatch, [page])

    data = _run(D.render(D.search_url("notice-and-tiles"), D.SEARCH_EXTRACT_JS, "search"))

    assert page.extract_calls == 1
    assert all(any("₽" in line for line in tile["lines"]) for tile in data["tiles"])
    assert len(opened) == 1


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_a_captcha_is_not_polled_and_not_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    """A captcha is the operator's to clear: the connector waits for neither."""
    page = _FakePage([{"state": "blocked", "captcha": True}])
    opened, _ = _patch_open_page(monkeypatch, [page])

    with pytest.raises(ToolError) as exc:
        _run(D.render(D.search_url("captcha"), D.SEARCH_EXTRACT_JS, "search"))

    body = json.loads(str(exc.value))
    assert body["error"] == "challenge_required" and body["requires_user_action"] is True
    assert len(opened) == 1


@pytest.mark.req("R-WB-DOM-SEARCH-PRICED")
def test_a_browser_check_is_not_polled_and_not_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    """WB's automatic check is waited out once, never reopened."""
    page = _FakePage([{"state": "challenge"}])
    opened, _ = _patch_open_page(monkeypatch, [page])
    monkeypatch.setattr(D._settings, "dom_wait_s", 0.5)

    with pytest.raises(ToolError) as exc:
        _run(D.render(D.search_url("browser-check"), D.SEARCH_EXTRACT_JS, "search"))

    assert "did not clear" in json.loads(str(exc.value))["message"]
    assert len(opened) == 1
