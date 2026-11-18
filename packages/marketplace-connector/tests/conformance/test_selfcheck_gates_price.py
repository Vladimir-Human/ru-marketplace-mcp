"""Every selfcheck must gate its verdict on the prices its own search tool reads.

START_MODULE_CONTRACT
PURPOSE: Prove R-SELFCHECK-GATES-PRICE for every registry source that ships a selfcheck.
SCOPE: One captured listing per source; only the transport that selfcheck calls is faked.
DEPENDS: pytest; stdlib importlib,json,pathlib,re; mcp_core.sources,mcp_core.domtest.
LINKS: M-SOURCES; M-MARKETPLACE; R-SELFCHECK-GATES-PRICE.
END_MODULE_CONTRACT
START_MODULE_MAP
Adapter: One source's selfcheck, its captured listing and how to feed it.
EXEMPTIONS: Sources with a selfcheck and no listing fixture to strip.
strip_*: Make every price in one captured listing unreadable, keys and types intact.
priced_*: Count the priced items through the tool's own price extraction.
install_*: Fake only the transport that source's selfcheck calls.
ADAPTERS: The per-source adapters, in registry order.
test_a_priceless_listing_never_reads_healthy: The price-stripped fixture is drift(no_prices).
test_the_captured_listing_reads_healthy: The untouched fixture is the healthy baseline.
test_every_selfcheck_source_is_covered: Every registry source with a selfcheck is covered or exempt.
END_MODULE_MAP
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.sources import BY_NAME, SOURCES

# START_BLOCK_ADAPTERS
ROOT = Path(__file__).resolve().parents[4]

# The value that replaces a price: no price parser reads it, and the key stays
# where it was, so a stripped listing is a priceless page, not a changed shape.
UNREADABLE = "no-price"


@dataclass(frozen=True)
class Adapter:
    """One source's selfcheck, its captured listing, and how to feed it.

    ``load`` returns the listing as the selfcheck's transport hands it over,
    ``strip`` returns the same listing with every price unreadable, ``priced``
    counts the priced items through the tool's OWN price extraction (the rule
    the requirement names), and ``install`` fakes the transport the selfcheck
    calls — nothing else.
    """

    source: str
    check: str
    fixture: str
    load: Callable[[Any], Any]
    strip: Callable[[Any], Any]
    install: Callable[[Any, pytest.MonkeyPatch, Any], None]
    priced: Callable[[Any, Any], int]
    baseline: str = "healthy"


def fixture_path(source: str, name: str) -> Path:
    return ROOT / "packages" / BY_NAME[source].package / "tests" / "fixtures" / name


def _json_fixture(source: str, name: str) -> Any:
    return json.loads(fixture_path(source, name).read_text(encoding="utf-8"))


def _dom_listing(module: Any, source: str, name: str, page_url: str) -> Any:
    """The captured page as the connector's own extractor reads it (jsdom).

    The DOM connectors' selfchecks consume the extractor's output, not the
    markup, so the capture is put through the shipped extractor — the same
    harness the connector's own shape tests use. Without jsdom the check
    cannot run and the case skips (the repo's convention for DOM checks).
    """
    try:
        return run_extractor(module._SEARCH_EXTRACT_JS, fixture_path(source, name), page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


async def _no_wait() -> None:
    return None


# ----------------------------------------------------------------- strips ----


def _blank_text(value: Any) -> Any:
    """Unreadable for a price parser, same JSON type as the value it replaces."""
    if isinstance(value, str):
        return UNREADABLE
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return 0
    return value


def _strip_tiles(payload: Any) -> Any:
    """Blank the price of every extracted tile; the keys stay where they were."""
    for tile in payload.get("items") or []:
        if not isinstance(tile, dict):
            continue
        for key in ("price_text", "old_price_text", "price_meta"):
            if isinstance(tile.get(key), str):
                tile[key] = UNREADABLE
        if isinstance(tile.get("price_cny"), (int, float)) and not isinstance(tile.get("price_cny"), bool):
            tile["price_cny"] = 0
        texts = tile.get("price_texts")
        if isinstance(texts, dict):
            for bucket in ("attached", "other"):
                if isinstance(texts.get(bucket), list):
                    texts[bucket] = [_blank_text(value) for value in texts[bucket]]
    return payload


def _strip_ozon(payload: Any) -> Any:
    """Blank the price text of every Ozon tile (the tile parser reads it)."""
    for name, body in list((payload.get("widgetStates") or {}).items()):
        data = json.loads(body) if isinstance(body, str) else body
        for tile in data.get("items") or []:
            for atom in (tile.get("mainState") or []) if isinstance(tile, dict) else []:
                if not isinstance(atom, dict) or atom.get("type") != "priceV2":
                    continue
                for entry in (atom.get("priceV2") or {}).get("price") or []:
                    if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                        entry["text"] = UNREADABLE
        payload["widgetStates"][name] = json.dumps(data) if isinstance(body, str) else data
    return payload


def _strip_avito(payload: Any) -> Any:
    for item in (payload.get("catalog") or {}).get("items") or []:
        detailed = item.get("priceDetailed")
        if not isinstance(detailed, dict):
            continue
        if isinstance(detailed.get("value"), (int, float)):
            detailed["value"] = 0
        for key in ("string", "fullString"):
            if isinstance(detailed.get(key), str):
                detailed[key] = UNREADABLE
    return payload


def _strip_megamarket(payload: Any) -> Any:
    for item in payload.get("items") or []:
        offer = item.get("favoriteOffer")
        if isinstance(offer, dict):
            for key in ("finalPrice", "price", "currentPrice"):
                if isinstance(offer.get(key), (int, float)):
                    offer[key] = 0
        for key in ("price", "currentPrice", "finalPrice"):
            if isinstance(item.get(key), (int, float)):
                item[key] = 0
    return payload


def _strip_detmir(payload: Any) -> Any:
    for item in payload.get("items") or []:
        price = item.get("price")
        if isinstance(price, dict):
            for key in ("price", "value", "amount"):
                if isinstance(price.get(key), (int, float)):
                    price[key] = 0
        elif isinstance(price, (int, float)):
            item["price"] = 0
        prices = item.get("prices")
        if isinstance(prices, dict):
            for key in ("sale", "current"):
                if isinstance(prices.get(key), (int, float)):
                    prices[key] = 0
        if isinstance(item.get("final_price"), (int, float)):
            item["final_price"] = 0
    return payload


def _strip_cian(payload: Any) -> Any:
    for offer in (payload.get("data") or {}).get("offersSerialized") or []:
        terms = offer.get("bargainTerms")
        if isinstance(terms, dict):
            for key in ("priceRur", "price"):
                if isinstance(terms.get(key), (int, float)):
                    terms[key] = 0
        for key in ("priceTotalRur", "priceTotal"):
            if isinstance(offer.get(key), (int, float)):
                offer[key] = 0
    return payload


# The Yandex SERP state keeps its money inside JSON blobs; the everyday price
# rides on the cart button / the discounted additional price, the subscriber
# price on the display node, and the struck-through base price on the offer.
_YANDEX_MONEY_KEYS = frozenset(
    {"valueFmt", "priceValue", "intPart", "price", "currentPrice", "oldPrice", "initialPrice"}
)


def _strip_yandex(html: str) -> str:
    """Blank every money leaf of the SSR state, keys and types intact."""

    def blank(node: Any, parent: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    blank(value, key)
                elif key in _YANDEX_MONEY_KEYS or (key == "value" and parent == "price"):
                    node[key] = _blank_text(value)
        elif isinstance(node, list):
            for item in node:
                blank(item, parent)

    def rewrite(match: re.Match[str]) -> str:
        body = match.group(1)
        try:
            state = json.loads(body)
        except json.JSONDecodeError:
            return match.group(0)
        blank(state)
        return f'<noframes data-apiary="patch">{json.dumps(state, ensure_ascii=False)}</noframes>'

    return re.sub(r'<noframes data-apiary="patch">(.*?)</noframes>', rewrite, html, flags=re.S)


def _strip_wb(payload: Any) -> Any:
    for product in payload.get("products") or []:
        for size in product.get("sizes") or []:
            price = size.get("price")
            if isinstance(price, dict):
                size["price"] = dict.fromkeys(price, 0)
    return payload


# ----------------------------------------------------------------- priced ----


def _priced_tiles(module: Any, payload: Any, field: str = "price_rub") -> int:
    return sum(
        1 for tile in payload.get("items") or [] if getattr(module._search_item_from_tile(tile), field) is not None
    )


def _priced_aliexpress(module: Any, payload: Any) -> int:
    return sum(1 for tile in payload.get("items") or [] if module._tile_prices(tile)[0] is not None)


def _priced_ozon(module: Any, payload: Any) -> int:
    return sum(1 for item in module._search_items_from_payload(payload) if item.get("price") is not None)


def _priced_avito(module: Any, payload: Any) -> int:
    items, _total = module._parse_search_items(payload)
    return sum(1 for item in items if item.get("price_rub") is not None)


def _priced_megamarket(module: Any, payload: Any) -> int:
    items, _total, _found = module._parse_items(payload)
    return sum(1 for item in items if item.get("price_rub") is not None)


def _priced_detmir(module: Any, payload: Any) -> int:
    return sum(1 for raw in payload.get("items") or [] if module._parse_product(raw).price_rub is not None)


def _priced_cian(module: Any, payload: Any) -> int:
    rows, _total = module._parse_offers(payload)
    return sum(1 for row in rows if row.get("price_rub") is not None)


def _priced_yandex(module: Any, html: str) -> int:
    parsed = module.ssr.parse_search(html)
    return sum(1 for item in parsed.get("items") or [] if item.get("price_rub") is not None)


def _priced_wb(module: Any, payload: Any) -> int:
    return sum(1 for product in payload.get("products") or [] if module._extract_price_rub(product)[0] is not None)


# ---------------------------------------------------------------- installs ----


async def _offline_page(*args: Any, **kwargs: Any) -> Any:
    raise ConnectionError("offline test: no browser page")


def _install_wb(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def responder(client: Any, url: str) -> tuple[int, str | None, str | None]:
        if "/v9/search" in url:
            return 200, json.dumps(payload), None
        return 200, "{}", None

    monkeypatch.setattr(module, "_safe_get_text", responder)
    monkeypatch.setattr(module, "_polite_wait", _no_wait)


def _install_ozon(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_fetch(path: str, ctx: Any = None) -> tuple[int, str, str]:
        return 200, json.dumps(payload), "fake"

    monkeypatch.setattr(module, "_fetch_composer", fake_fetch)


def _install_yandex(module: Any, monkeypatch: pytest.MonkeyPatch, html: str) -> None:
    async def fake_fetch(url: str, label: str, ctx: Any = None) -> str:
        return html

    monkeypatch.setattr(module, "_fetch_html", fake_fetch)


def _install_detmir(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_fetch(url: str, label: str, ctx: Any = None) -> Any:
        return payload

    monkeypatch.setattr(module, "_fetch_json", fake_fetch)


def _install_avito(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_fetch(url: str, ctx: Any = None) -> tuple[int, str, str]:
        return 200, json.dumps(payload), "fake"

    monkeypatch.setattr(module, "_fetch", fake_fetch)
    # The chained card probe opens the item page in the browser; offline it answers
    # as unreachable, which leaves the listing check this test reads untouched.
    monkeypatch.setattr(module, "_fetch_item_page", _offline_page)


def _install_taobao(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_render(url: str, extract_js: str, wait_ms: int, ctx: Any = None) -> Any:
        return payload

    monkeypatch.setattr(module, "_cdp_render", fake_render)


def _install_megamarket(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_post(api_path: str, body: Any, ctx: Any, what: str) -> Any:
        return payload

    monkeypatch.setattr(module, "_post", fake_post)
    monkeypatch.setattr(module, "_min_gap", 0.0)

    async def same_url(catalog_url: str, ctx: Any = None) -> str:
        return catalog_url

    # A cold category cache resolves the catalog URL in the browser; offline the
    # URL is taken as final, so the search goes straight to the faked _post.
    monkeypatch.setattr(module, "_final_catalog_url", same_url)


def _install_lamoda(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_render(query: str, ctx: Any = None) -> Any:
        return payload

    async def fake_graphql(sku: str, ctx: Any = None) -> Any:
        return {"sku": sku, "name": "Кроссовки", "price_amount": 100.0, "is_available": True, "sizes": []}

    monkeypatch.setattr(module, "_cdp_render_search", fake_render)
    monkeypatch.setattr(module, "_graphql_card", fake_graphql)


def _install_dns(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_render(url: str, extract_js: str, wait_ms: int, ctx: Any = None) -> Any:
        return payload

    monkeypatch.setattr(module, "_cdp_render", fake_render)


def _install_citilink(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_render(url: str, extract_js: str, wait_ms: int, ctx: Any = None) -> Any:
        return payload

    monkeypatch.setattr(module, "_cdp_render", fake_render)


def _install_aliexpress(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_search(url: str, ctx: Any = None) -> Any:
        return payload

    async def fake_card(url: str, ctx: Any = None) -> Any:
        return {"punish": False, "title": "card", "price_texts": {"attached": [], "other": []}}

    monkeypatch.setattr(module, "_cdp_render_search", fake_search)
    monkeypatch.setattr(module, "_cdp_card", fake_card)


def _install_cian(module: Any, monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake_search(json_query: Any, ctx: Any = None) -> tuple[int, str, str]:
        return 200, json.dumps(payload), "fake"

    async def fake_card(offer_id: int, ctx: Any = None) -> tuple[int, str, str]:
        return 200, json.dumps({"kind": "ok", "offerData": {}}), "fake"

    monkeypatch.setattr(module, "_fetch_search", fake_search)
    monkeypatch.setattr(module, "_fetch_card", fake_card)


# ---------------------------------------------------------------- adapters ----

ADAPTERS: tuple[Adapter, ...] = (
    Adapter(
        source="wildberries",
        check="search_v9",
        fixture="search_v9_live.json",
        load=lambda module: _json_fixture("wildberries", "search_v9_live.json"),
        strip=_strip_wb,
        install=_install_wb,
        priced=_priced_wb,
    ),
    Adapter(
        source="ozon",
        check="search",
        fixture="search_composer_live.json",
        load=lambda module: _json_fixture("ozon", "search_composer_live.json"),
        strip=_strip_ozon,
        install=_install_ozon,
        priced=_priced_ozon,
    ),
    Adapter(
        source="yandex_market",
        check="search",
        fixture="search_washer.html",
        load=lambda module: fixture_path("yandex_market", "search_washer.html").read_text(encoding="utf-8"),
        strip=_strip_yandex,
        install=_install_yandex,
        priced=_priced_yandex,
    ),
    Adapter(
        source="detsky_mir",
        check="category",
        fixture="category_live.json",
        load=lambda module: _json_fixture("detsky_mir", "category_live.json"),
        strip=_strip_detmir,
        install=_install_detmir,
        priced=_priced_detmir,
    ),
    Adapter(
        source="avito",
        check="search",
        fixture="js_items_live.json",
        load=lambda module: _json_fixture("avito", "js_items_live.json"),
        strip=_strip_avito,
        install=_install_avito,
        priced=_priced_avito,
    ),
    Adapter(
        source="taobao",
        check="search",
        fixture="search_grid_live.html",
        load=lambda module: _dom_listing(
            module, "taobao", "search_grid_live.html", "https://s.taobao.com/search?q=%E8%BF%9E%E8%A1%A3%E8%A3%99"
        ),
        strip=_strip_tiles,
        install=_install_taobao,
        priced=lambda module, payload: _priced_tiles(module, payload, "price_cny"),
    ),
    Adapter(
        source="megamarket",
        check="search",
        fixture="search_live.json",
        load=lambda module: _json_fixture("megamarket", "search_live.json"),
        strip=_strip_megamarket,
        install=_install_megamarket,
        priced=_priced_megamarket,
    ),
    Adapter(
        source="lamoda",
        check="search",
        fixture="search_grid_live.html",
        load=lambda module: _dom_listing(
            module,
            "lamoda",
            "search_grid_live.html",
            "https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8",
        ),
        strip=_strip_tiles,
        install=_install_lamoda,
        priced=_priced_tiles,
    ),
    Adapter(
        source="dns",
        check="search",
        fixture="search_grid.html",
        load=lambda module: _dom_listing(
            module,
            "dns",
            "search_grid.html",
            "https://www.dns-shop.ru/search/?q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA",
        ),
        strip=_strip_tiles,
        install=_install_dns,
        priced=_priced_tiles,
    ),
    Adapter(
        source="citilink",
        check="search",
        fixture="search_grid.html",
        load=lambda module: _dom_listing(
            module, "citilink", "search_grid.html", "https://www.citilink.ru/catalog/noutbuki/"
        ),
        strip=_strip_tiles,
        install=_install_citilink,
        priced=_priced_tiles,
    ),
    Adapter(
        source="aliexpress",
        check="search",
        fixture="search_grid.html",
        load=lambda module: _dom_listing(
            module, "aliexpress", "search_grid.html", "https://aliexpress.ru/wholesale?SearchText=x"
        ),
        strip=_strip_tiles,
        install=_install_aliexpress,
        priced=_priced_aliexpress,
    ),
    Adapter(
        source="cian",
        check="search",
        fixture="search_sale_live.json",
        load=lambda module: _json_fixture("cian", "search_sale_live.json"),
        strip=_strip_cian,
        install=_install_cian,
        priced=_priced_cian,
    ),
)

# Every remaining source with a selfcheck has a captured listing.
EXEMPTIONS: dict[str, str] = {}


# END_BLOCK_ADAPTERS
# START_BLOCK_CONFORMANCE


def _entry(check: Any) -> dict[str, Any]:
    """One selfcheck sub-check as a plain dict (models and dicts alike)."""
    return check if isinstance(check, dict) else check.model_dump()


async def _run(
    source: str, monkeypatch: pytest.MonkeyPatch, install: Callable[..., None], payload: Any
) -> dict[str, Any]:
    module = importlib.import_module(BY_NAME[source].module)
    install(module, monkeypatch, payload)
    result = await getattr(module, BY_NAME[source].selfcheck)()
    return {name: _entry(check) for name, check in result.checks.items()}


@pytest.mark.req("R-SELFCHECK-GATES-PRICE")
@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda adapter: adapter.source)
async def test_a_priceless_listing_never_reads_healthy(adapter: Adapter, monkeypatch: pytest.MonkeyPatch) -> None:
    """The captured listing with every price unreadable must not read healthy.

    The price count comes from the tool's OWN extraction, so a selfcheck that
    reads prices its own way (or not at all) is caught here: the stripped
    listing carries no price the tool can read, and a canary that says
    healthy anyway is wrong (R-SELFCHECK-GATES-PRICE).
    """
    module = importlib.import_module(BY_NAME[adapter.source].module)
    listing = adapter.load(module)
    assert adapter.priced(module, listing) > 0, f"{adapter.source}: the capture must carry a price to begin with"
    stripped = adapter.strip(listing)
    assert adapter.priced(module, stripped) == 0, f"{adapter.source}: the strip must leave no readable price"
    checks = await _run(adapter.source, monkeypatch, adapter.install, stripped)
    check = checks[adapter.check]
    assert check["state"] != "healthy", f"{adapter.source}: a priceless listing read healthy — {check}"
    assert check.get("reason") == "no_prices", f"{adapter.source}: the drift must name the missing prices — {check}"


@pytest.mark.req("R-SELFCHECK-GATES-PRICE")
@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda adapter: adapter.source)
async def test_the_captured_listing_reads_healthy(adapter: Adapter, monkeypatch: pytest.MonkeyPatch) -> None:
    """The untouched capture is the healthy baseline: the gate must not cry drift."""
    module = importlib.import_module(BY_NAME[adapter.source].module)
    listing = adapter.load(module)
    assert adapter.priced(module, listing) > 0, f"{adapter.source}: the capture must carry a price to begin with"
    checks = await _run(adapter.source, monkeypatch, adapter.install, listing)
    check = checks[adapter.check]
    assert check["state"] == adapter.baseline, f"{adapter.source}: the captured listing must read healthy — {check}"


@pytest.mark.req("R-SELFCHECK-GATES-PRICE")
def test_every_selfcheck_source_is_covered() -> None:
    """The registry is the list: a source with a selfcheck needs a case or a stated gap."""
    with_selfcheck = {spec.name for spec in SOURCES if spec.selfcheck}
    covered = {adapter.source for adapter in ADAPTERS}
    assert with_selfcheck == covered | set(EXEMPTIONS), (
        f"missing cases: {with_selfcheck - covered - set(EXEMPTIONS)}; "
        f"cases without a selfcheck: {covered - with_selfcheck}"
    )
    assert all(reason.strip() for reason in EXEMPTIONS.values())
    assert not covered & EXEMPTIONS.keys(), "a covered source needs no exemption"


# END_BLOCK_CONFORMANCE
