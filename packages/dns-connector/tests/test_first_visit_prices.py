"""A new profile's first DNS search renders tiles before prices (first-run probe, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove R-DNS-FIRST-VISIT-PRICES through dns_search and dns_selfcheck: tiles with no price at all get one
         more render; a priced page, a wall or a page that stays unpriced never gets a third.
SCOPE: dns_search, dns_selfcheck with _cdp_render replaced by a scripted sequence of payloads.
DEPENDS: pytest; dns_connector.server.
LINKS: M-DNS; R-DNS-FIRST-VISIT-PRICES; R-SELFCHECK-GATES-PRICE.
END_MODULE_CONTRACT
"""

from __future__ import annotations

import contextlib

import pytest
from dns_connector import server
from fastmcp.exceptions import ToolError


def _tile(price_text: str | None) -> dict:
    return {
        "product_id": "5f4dcc3b5aa764d61d8327de",
        "title": "Ноутбук Lenovo IdeaPad 3",
        "price_text": price_text,
        "old_price_text": None,
        "availability_text": "В наличии",
        "url": "https://www.dns-shop.ru/product/5f4dcc3b5aa764d61d8327de/noutbuk-lenovo/",
    }


UNPRICED = {"title": "ноутбук — DNS", "items": [_tile(None), _tile("")]}
PRICED = {"title": "ноутбук — DNS", "items": [_tile("52 999 ₽"), _tile(None)]}


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()


def _script(monkeypatch: pytest.MonkeyPatch, *payloads: dict) -> list[str]:
    calls: list[str] = []

    async def fake_render(url, extract_js, wait_ms, ctx):
        calls.append(url)
        return payloads[min(len(calls), len(payloads)) - 1]

    monkeypatch.setattr(server, "_cdp_render", fake_render)
    return calls


@pytest.mark.req("R-DNS-FIRST-VISIT-PRICES")
async def test_a_first_render_without_any_price_is_rendered_once_more(monkeypatch) -> None:
    calls = _script(monkeypatch, UNPRICED, PRICED)

    result = await server.dns_search("ноутбук")

    assert len(calls) == 2
    assert result.items[0].price_rub == 52999.0
    assert not any("no_prices" in warning for warning in result.meta.warnings)


@pytest.mark.req("R-DNS-FIRST-VISIT-PRICES")
async def test_the_doctor_check_reads_the_second_render(monkeypatch) -> None:
    calls = _script(monkeypatch, UNPRICED, PRICED)

    result = await server.dns_selfcheck()

    assert len(calls) == 2
    assert result.checks["search"].state == "healthy", result.checks["search"]


@pytest.mark.req("R-DNS-FIRST-VISIT-PRICES")
@pytest.mark.parametrize(
    "payloads,renders",
    [
        ((PRICED,), 1),  # a priced page is never rendered again
        ((UNPRICED, UNPRICED, PRICED), 2),  # one more render only, never a third
        (({"title": "Доступ ограничен", "items": [_tile(None)], "qrator_wall": True},), 1),  # a wall is no page
    ],
    ids=["priced", "still-unpriced", "wall"],
)
async def test_one_more_render_at_most_and_only_for_an_unpriced_page(monkeypatch, payloads, renders) -> None:
    calls = _script(monkeypatch, *payloads)

    # The wall case raises its own challenge error; only the render count matters here.
    with contextlib.suppress(ToolError):
        await server.dns_search("ноутбук")

    assert len(calls) == renders
