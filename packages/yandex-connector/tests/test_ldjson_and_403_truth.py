"""Regression tests for Yandex JSON-LD arrays/objects, URL host hygiene, HTTP 403 refusals, and error wrapping."""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError
from yandex_connector import server, ssr


@pytest.fixture(autouse=True)
def _clear_yandex_cache() -> None:
    server._cache.clear()


@pytest.mark.req("R-YANDEX-ANONYMOUS-PRICES")
def test_ldjson_array_and_non_dict_patch_do_not_raise_and_unwrap_brand_image() -> None:
    html = (
        "<html><body>"
        '<noframes data-apiary="patch">null</noframes>'
        '<noframes data-apiary="patch">[1, 2]</noframes>'
        '<script type="application/ld+json">'
        + json.dumps(
            [
                {
                    "@type": "Product",
                    "name": "Смартфон Samsung Galaxy S25",
                    "sku": "1029384756",
                    "brand": {"@type": "Brand", "name": "Samsung"},
                    "image": ["https://avatars.mds.yandex.net/get-mpic/12345/img_id1/orig"],
                    "url": "https://market.yandex.ru/card/smartfon-samsung/1029384756",
                    "offers": {"price": "69990", "priceCurrency": "RUB"},
                }
            ]
        )
        + "</script></body></html>"
    )
    parsed = ssr.parse_card(html)
    assert parsed["status"] == ssr.ParseStatus.OK
    assert parsed["title"] == "Смартфон Samsung Galaxy S25"
    assert parsed["brand"] == "Samsung"
    assert parsed["image"] == "https://avatars.mds.yandex.net/get-mpic/12345/img_id1/orig"
    assert parsed["price_with_plus"] == 69990.0


@pytest.mark.req("R-OUTPUT-PAGE-URLS")
def test_ssr_normalizes_urls_to_allowed_hosts() -> None:
    assert ssr._absolute_url("https://evil.example/card/x/12345", "12345") == "https://market.yandex.ru/card/x/12345"
    assert ssr._absolute_url("http://market.yandex.ru/card/x/12345", "12345") == "https://market.yandex.ru/card/x/12345"
    assert ssr._absolute_url("/card/phone/12345", "12345") == "https://market.yandex.ru/card/phone/12345"

    ld_html = (
        '<html><body><script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "ItemList",
                "itemListElement": [
                    {
                        "item": {
                            "name": "Phone",
                            "url": "https://evil.example/card/phone/98765",
                            "image": ["https://avatars.mds.yandex.net/get-mpic/1/img/orig"],
                            "offers": {"price": "19990"},
                        }
                    }
                ],
            }
        )
        + "</script></body></html>"
    )
    items = ssr.ldjson_item_list(ld_html)
    assert len(items) == 1
    assert items[0]["url"] == ""
    assert items[0]["image"] == "https://avatars.mds.yandex.net/get-mpic/1/img/orig"


@pytest.mark.req("R-YANDEX-CACHE-CONTENT")
async def test_http_403_refusal_without_captcha_raises_transport_down_not_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_text(*_args: object, **_kwargs: object) -> tuple[int, str]:
        return 403, "<html><head><title>403 Forbidden</title></head><body>Access denied</body></html>"

    monkeypatch.setattr(server, "get_text_with_retries", fake_get_text)
    with pytest.raises(ToolError) as excinfo:
        await server.yandex_search("смартфон")

    err = json.loads(str(excinfo.value))
    assert err["error"] == "transport_down"
    assert err["status_code"] == 403


@pytest.mark.req("R-ERROR-PASSTHROUGH")
async def test_unexpected_internal_error_wraps_into_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("unexpected state failure")

    monkeypatch.setattr(server, "_fetch_parsed", boom)

    with pytest.raises(ToolError) as excinfo_search:
        await server.yandex_search("смартфон")
    assert json.loads(str(excinfo_search.value))["error"] == "transport_down"

    with pytest.raises(ToolError) as excinfo_card:
        await server.yandex_card("12345678")
    assert json.loads(str(excinfo_card.value))["error"] == "transport_down"
