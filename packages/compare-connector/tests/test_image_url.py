"""Captured search data reaches compare offers without fetching an image."""
# START_MODULE_CONTRACT
# PURPOSE: Prove fixture-backed images, empty unsupported sources and URL restrictions.
# SCOPE: Real search parsers and compare_prices; only native search I/O is replaced.
# DEPENDS: Native connector parsers/models; compare_connector; mcp_core.domtest; pytest.
# LINKS: M-COMPARE; R-COMPARE-IMAGE-URL; R-OFFLINE; R-FIXTURES.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _fixture: Locate a committed source capture.
# _ozon_tile: Decode the first captured composer tile.
# _native_response: Run captured payloads through native search parsers and models.
# _compare: Replace only native search I/O and call the real public compare tool.
# test_fixture_image_reaches_compare_offer: Pin each supported source's captured image.
# test_unsupported_source_image_stays_empty: Never derive an image without usable evidence.
# test_image_url_is_additive_and_empty_by_default: Pin the new field's default and type.
# test_url_policy_on_a_captured_tile: Mutate only the image URL to exercise safety policy.
# test_malformed_image_payload_stays_empty: Malformed optional image data cannot lose an offer.
# test_a_lazy_load_placeholder_is_not_an_image_url: A data: or relative placeholder never reaches a connector item.
# END_MODULE_MAP

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from compare_connector import server
from compare_connector.models_output import MarketOffer
from mcp_core.domtest import JsdomUnavailable, run_extractor

PACKAGES = Path(__file__).resolve().parents[2]

# Capture-derived primary image URLs, not independently generated URL schemes.
IMAGES = {
    "yandex_market": "https://avatars.mds.yandex.net/get-mpic/20177992/2a0000019f4b6ab4996bbc6ba7aba562f292/orig",
    "ozon": "https://ir.ozone.ru/s3/multimedia-1-w/11637109436.jpg",
    "megamarket": "https://main-cdn.sbermegamarket.ru/mid9/hlr-system/52/85/64/40/14/29/100026555148b0.jpg",
    "dns": "https://c.dns-shop.ru/thumb/st1/fit/200/200/x/y.jpg",
    "aliexpress": "https://ae-pic-a1.aliexpress-media.com/kf/S61b215d5d8a64b93875260ec37039dee2.jpg_480x480.jpg",
}


# START_BLOCK_IMAGE_TESTS
def _fixture(source: str, name: str) -> Path:
    return PACKAGES / f"{source}-connector" / "tests" / "fixtures" / name


def _ozon_tile() -> dict:
    payload = json.loads(_fixture("ozon", "search_composer_live.json").read_text(encoding="utf-8"))
    grid = next(value for key, value in payload["widgetStates"].items() if key.startswith("tileGridDesktop-"))
    return json.loads(grid)["items"][0]


def _native_response(source: str):
    package = {"wildberries": "wb", "yandex_market": "yandex"}.get(source, source)
    native = importlib.import_module(f"{package}_connector.server")
    models = importlib.import_module(f"{package}_connector.models_output")
    if source == "yandex_market":
        html = _fixture(package, "search_washer.html").read_text(encoding="utf-8")
        items = native.ssr.parse_search(html)["items"]
        return "yandex_search", models.YandexSearchResponse(items=[native._to_product(items[0])])
    if source == "ozon":
        return "ozon_search", models.OzonSearchResponse(items=[native._parse_search_tile(_ozon_tile())])
    if source == "megamarket":
        payload = json.loads(_fixture(package, "search_live.json").read_text(encoding="utf-8"))
        items, _, _ = native._parse_items(payload)
        return "megamarket_search", models.MegamarketSearchResponse(items=items[:1])
    if source == "avito":
        payload = json.loads(_fixture(package, "js_items_live.json").read_text(encoding="utf-8"))
        items, _ = native._parse_search_items(payload)
        return "avito_search", models.AvitoSearchResponse(items=items[:1])
    if source == "wildberries":
        payload = json.loads(_fixture(package, "search_v9_live.json").read_text(encoding="utf-8"))
        return "wb_search", models.WbSearchResponse(items=[native._card_item_dict(payload["products"][0])])
    filename = "search_grid_live.html" if source in {"lamoda", "taobao"} else "search_grid.html"
    page_url = {
        "dns": "https://www.dns-shop.ru/search/?q=ноутбук",
        "citilink": "https://www.citilink.ru/catalog/noutbuki/",
        "aliexpress": "https://aliexpress.ru/wholesale?SearchText=realme%20watch",
        "lamoda": "https://www.lamoda.ru/catalogsearch/result/?q=кроссовки",
        "taobao": "https://s.taobao.com/search?q=连衣裙",
    }[source]
    try:
        payload = run_extractor(native._SEARCH_EXTRACT_JS, _fixture(package, filename), page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    parser = native._item_from_payload if source == "aliexpress" else native._search_item_from_tile
    class_name = {
        "dns": "DnsSearchResponse",
        "citilink": "CitilinkSearchResponse",
        "aliexpress": "AliSearchResponse",
        "lamoda": "LamodaSearchResponse",
        "taobao": "TaobaoSearchResponse",
    }[source]
    response_type = getattr(models, class_name)
    return f"{source}_search", response_type(items=[parser(payload["items"][0])])


async def _compare(monkeypatch, source, tool, response):
    async def search(**kwargs):
        return response

    # The sole I/O fake: preserve the real parser, typed response, adapter and ranking.
    monkeypatch.setitem(server.SOURCES, source, SimpleNamespace(**{tool: search}))
    monkeypatch.delenv("MARKETPLACE_SOURCES", raising=False)
    return await server.compare_prices("product", sources=[source])


@pytest.mark.req("R-COMPARE-IMAGE-URL")
@pytest.mark.parametrize("source", IMAGES)
async def test_fixture_image_reaches_compare_offer(monkeypatch, source):
    tool, response = _native_response(source)
    result = await _compare(monkeypatch, source, tool, response)

    assert len(result.offers) == 1
    assert result.offers[0].model_dump().get("image_url") == IMAGES[source]


@pytest.mark.req("R-COMPARE-IMAGE-URL")
@pytest.mark.parametrize("source", ["wildberries", "avito", "taobao", "lamoda", "citilink"])
async def test_unsupported_source_image_stays_empty(monkeypatch, source):
    tool, response = _native_response(source)
    result = await _compare(monkeypatch, source, tool, response)

    assert len(result.offers) == 1
    assert result.offers[0].model_dump().get("image_url") == ""


@pytest.mark.req("R-COMPARE-IMAGE-URL")
def test_image_url_is_additive_and_empty_by_default():
    offer = MarketOffer()
    assert offer.model_dump().get("image_url") == ""
    assert MarketOffer.model_json_schema()["properties"]["image_url"]["type"] == "string"


@pytest.mark.req("R-COMPARE-IMAGE-URL")
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (IMAGES["ozon"].removeprefix("https:"), IMAGES["ozon"]),
        (IMAGES["ozon"] + "?width=200", IMAGES["ozon"] + "?width=200"),
        (IMAGES["ozon"] + "?signature=abc", ""),
        (IMAGES["ozon"] + "?X-Amz-Signature=abc", ""),
        (IMAGES["ozon"] + "?token=abc", ""),
        (IMAGES["ozon"] + "?expires=1790000000", ""),
        ("data:image/jpeg;base64,AA==", ""),
        ("/photo.jpg", ""),
        ("https:///photo.jpg", ""),
    ],
)
async def test_url_policy_on_a_captured_tile(monkeypatch, url, expected):
    # A focused mutation of the captured URL, not invented source markup.
    native = importlib.import_module("ozon_connector.server")
    models = importlib.import_module("ozon_connector.models_output")
    tile = _ozon_tile()
    tile["tileImage"]["items"][0]["image"]["link"] = url
    response = models.OzonSearchResponse(items=[native._parse_search_tile(tile)])
    result = await _compare(monkeypatch, "ozon", "ozon_search", response)

    assert len(result.offers) == 1
    assert result.offers[0].model_dump().get("image_url") == expected


@pytest.mark.req("R-COMPARE-IMAGE-URL")
@pytest.mark.parametrize("image", [None, [], {"items": None}, {"items": [None]}, {"items": [{"image": []}]}])
async def test_malformed_image_payload_stays_empty(monkeypatch, image):
    native = importlib.import_module("ozon_connector.server")
    models = importlib.import_module("ozon_connector.models_output")
    tile = _ozon_tile()
    tile["tileImage"] = image
    response = models.OzonSearchResponse(items=[native._parse_search_tile(tile)])
    result = await _compare(monkeypatch, "ozon", "ozon_search", response)

    assert len(result.offers) == 1
    assert result.offers[0].model_dump().get("image_url") == ""


# END_BLOCK_IMAGE_TESTS


@pytest.mark.req("R-COMPARE-IMAGE-URL")
@pytest.mark.parametrize("source", ["dns", "aliexpress"])
@pytest.mark.parametrize("placeholder", ["data:image/gif;base64,R0lGODlhAQABAAAAACw=", "/img/blank.gif", ""])
def test_a_lazy_load_placeholder_is_not_an_image_url(source, placeholder):
    """R-COMPARE-IMAGE-URL: the connector's own search item keeps only an http(s) or scheme-relative URL."""
    native = importlib.import_module(f"{source}_connector.server")
    page_url = {
        "dns": "https://www.dns-shop.ru/search/?q=ноутбук",
        "aliexpress": "https://aliexpress.ru/wholesale?SearchText=realme%20watch",
    }[source]
    try:
        payload = run_extractor(native._SEARCH_EXTRACT_JS, _fixture(source, "search_grid.html"), page_url=page_url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))
    tile = dict(payload["items"][0], image_url=placeholder)
    parser = native._item_from_payload if source == "aliexpress" else native._search_item_from_tile
    assert parser(tile).image_url == ""
