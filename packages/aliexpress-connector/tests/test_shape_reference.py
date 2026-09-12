"""Golden shapes for AliExpress's captured DOM extractors."""

from __future__ import annotations

from pathlib import Path

import pytest
from aliexpress_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor
from mcp_core.resilience import shape_signature

FIXTURES = Path(__file__).parent / "fixtures"


def _extract(source: str, fixture: Path, url: str) -> dict:
    try:
        return run_extractor(source, fixture, page_url=url)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def test_search_extractor_shape_matches_golden() -> None:
    payload = _extract(
        server._SEARCH_EXTRACT_JS,
        FIXTURES / "search_grid.html",
        "https://aliexpress.ru/wholesale?SearchText=x",
    )

    assert shape_signature(payload) == [
        "items[].item_id:str",
        "items[].orders:str",
        "items[].price_texts.attached[]:str",
        "items[].price_texts.other:empty_array",
        "items[].price_texts.other[]:str",
        "items[].rating:str",
        "items[].sku_id:str",
        "items[].title:str",
        "items[].url:str",
        "page_title:str",
        "punish:bool",
    ]


def test_card_extractor_shape_matches_golden() -> None:
    payload = _extract(
        server._CARD_EXTRACT_JS,
        FIXTURES / "card.html",
        "https://aliexpress.ru/item/x.html",
    )

    assert shape_signature(payload) == [
        "meta_description:str",
        "price_texts.attached:empty_array",
        "price_texts.other:empty_array",
        "punish:bool",
        "rating_text:null",
        "title:str",
        "url:str",
    ]
