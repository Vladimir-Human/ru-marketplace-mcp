"""The REAL extractors run against the captured fixtures — not mocks.

The offline suite above monkeypatches the render step and asserts the tool
contract around parsed tiles; what it deliberately cannot prove is that the
extractor JavaScript still understands the site's markup. That half runs here
against the trimmed real pages captured 2026-08-20 (see fixtures/*.provenance.json
for what those pages displayed), in jsdom when available. Mocking the render
call and feeding pre-parsed dicts is exactly how the DNS and Citilink bug
classes lived while 707 tests stayed green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aliexpress_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
SEARCH_FIXTURE = FIXTURES / "search_grid.html"
CARD_FIXTURE = FIXTURES / "card.html"
SEARCH_PAGE_URL = "https://aliexpress.ru/wholesale?SearchText=realme+watch"


def _run(js_source, fixture, page_url):
    try:
        payload = run_extractor(js_source, fixture, page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    assert isinstance(payload, dict), f"extractor returned non-object: {payload!r}"
    return payload


def test_search_extractor_reads_the_captured_grid():
    payload = _run(server._SEARCH_EXTRACT_JS, SEARCH_FIXTURE, SEARCH_PAGE_URL)

    assert payload.get("punish") is False
    items = [t for t in payload.get("items") or [] if isinstance(t, dict)]
    assert items, "the real extractor found no tiles in the captured grid"

    # The two tiles the provenance records: current price is the smaller number.
    by_id = {t.get("item_id"): t for t in items}
    realme = by_id.get("1005010003103368")
    assert realme is not None, "the recorded Realme tile is missing"
    attached = (realme.get("price_texts") or {}).get("attached") or []
    assert "2 939 ₽" in attached
    assert "6 967 ₽" in attached
    assert realme.get("orders") == "2334"
    assert realme.get("title")

    s5 = by_id.get("1005012078314868")
    if s5 is not None:
        attached_s5 = (s5.get("price_texts") or {}).get("attached") or []
        assert "5 579 ₽" in attached_s5, "the coupon/base pair of the S5 tile changed shape"


def test_search_extractor_pairing_agrees_with_python():
    """Parser-to-wire: the DOM order (base, current) must yield current=min."""
    payload = _run(server._SEARCH_EXTRACT_JS, SEARCH_FIXTURE, SEARCH_PAGE_URL)
    items = [t for t in payload.get("items") or [] if isinstance(t, dict)]
    assert items
    parsed = [server._item_from_payload(t) for t in items]
    realme = next((p for p in parsed if p.item_id == "1005010003103368"), None)
    assert realme is not None
    assert realme.price_rub == 2939.0, "the extractor+pairing must ship the current price, not the base"
    assert realme.old_price_rub == 6967.0


def test_card_extractor_reads_the_captured_modules():
    payload = _run(server._CARD_EXTRACT_JS, CARD_FIXTURE, "https://aliexpress.ru/item/1005010003103368.html")

    assert payload.get("punish") is False
    assert "Realme Watch 5" in (payload.get("title") or "")
    assert "Рейтинг- 4.7" in (payload.get("meta_description") or "")
    # The SSR shell the fixture was captured from carries no price module: the
    # price pair renders client-side and is asserted by the cdp-marked test and
    # by the offline tool tests (CARD_EXTRACTED payload). Here the extractor
    # must return an empty glyph-scored structure rather than invent one.
    assert payload.get("price_texts") is not None


@pytest.mark.cdp
def test_live_cdp_reads_a_real_grid_and_card():
    """The whole two-hop flow against the operator's Chrome.

    Excluded from CI (no Chrome there). Run locally with CDP on port 9222:
        uv run pytest packages/aliexpress-connector/tests/test_parser_live.py -m cdp -s

    The card assertion is deliberately softer than the search one: measured on
    this very session, x5sec quietly strips the client-rendered price module
    from card pages when the session is probed hard — the title renders, the
    price does not (the connector reports that as `price_missing`, never as a
    fake number). A total render failure — no title either — stays a failure.
    """
    import asyncio
    import os

    async def _cdp_alive() -> bool:
        port = int(os.environ.get("CHROME_CDP_PORT", "9222"))
        _reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=3)
        writer.close()
        await writer.wait_closed()
        return True

    try:
        alive = asyncio.run(_cdp_alive())
    except Exception:
        alive = False
    if not alive:
        pytest.skip("CDP is not listening — start the scraping Chrome (scripts/start_chrome_cdp) first")

    async def probe():
        search = await server._cdp_render_search(f"{server.SEARCH_URL}?SearchText={'умные часы'}", ctx=None)
        items = [t for t in (search.get("items") or []) if isinstance(t, dict)]
        assert items, "no tiles on the live grid"
        parsed = server._item_from_payload(items[0])
        assert parsed.title, "live tile title did not parse"
        card = await server._cdp_card(f"{server.SITE_BASE}/item/{parsed.item_id}.html", ctx=None)
        title = card.get("title")
        price, _old = server._card_prices(card)
        assert title, "live card rendered no title at all — hard shape break"
        assert price is None or price > 0
        # price_missing is a known, honest state under x5sec load-shedding; the
        # tool-level contract for it is covered by test_card_warns_when_price_missing.

    asyncio.run(probe())
