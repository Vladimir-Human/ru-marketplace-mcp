"""Detsky Mir page links stay on Detsky Mir's own site (audit cycle 1, finding F-A1, 2026-11-17).

START_MODULE_CONTRACT
PURPOSE: Prove category and product page links are full https links on the registry's Detsky Mir page hosts;
         the sister shop's zoozavr.ru links the live category list carried are dropped.
SCOPE: detmir_categories through a stubbed _fetch_json (URL routing only) and the pure _parse_product.
DEPENDS: detmir_connector.server; pytest.
LINKS: R-OUTPUT-PAGE-URLS.detmir.
END_MODULE_CONTRACT
"""

from __future__ import annotations

from typing import Any

import pytest
from detmir_connector import server


@pytest.fixture(autouse=True)
def clear_cache() -> Any:
    server._cache.clear()
    yield
    server._cache.clear()


def _categories(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": rows, "meta": {"total": len(rows), "offset": 0, "limit": 30}}


@pytest.mark.req("R-OUTPUT-PAGE-URLS.detmir")
async def test_a_sister_shop_category_link_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": 1, "alias": "igry", "title": "Игры", "web_url": "https://www.detmir.ru/catalog/index/name/igry/"},
        {
            "id": 2,
            "alias": "zoozavr",
            "title": "Зоотовары",
            "web_url": "https://zoozavr.ru/catalog/index/name/zoozavr/",
        },
        {"id": 3, "alias": "pups", "title": "Пупсы", "web_url": "/catalog/index/name/pups/"},
        {"id": 4, "alias": "http", "title": "Http", "web_url": "http://www.detmir.ru/catalog/index/name/http/"},
    ]

    async def fake_fetch(url: str, label: str, ctx: Any = None) -> dict[str, Any]:
        assert "/v2/categories" in url
        return _categories(rows)

    monkeypatch.setattr(server._limiter, "min_gap_s", 0.0)
    monkeypatch.setattr(server, "_fetch_json", fake_fetch)

    result = await server.detmir_categories(parent="top", limit=30)

    assert [item.url for item in result.items] == [
        "https://www.detmir.ru/catalog/index/name/igry/",
        "",
        "https://www.detmir.ru/catalog/index/name/pups/",
        "",
    ]


@pytest.mark.req("R-OUTPUT-PAGE-URLS.detmir")
def test_a_product_link_on_another_host_is_dropped_and_a_missing_one_is_built() -> None:
    foreign = server._parse_product({"id": 5, "title": "x", "link": {"web_url": "https://zoozavr.ru/product/5/"}})
    missing = server._parse_product({"id": 6, "title": "y"})
    own = server._parse_product(
        {"id": 7, "title": "z", "link": {"web_url": "https://www.detmir.ru/product/index/id/7/"}}
    )

    assert foreign.url == ""
    assert missing.url == "https://www.detmir.ru/product/index/id/6/"
    assert own.url == "https://www.detmir.ru/product/index/id/7/"
