"""Reference shape signatures for the Yandex Market SSR parsers, pinned to captures.

Companion to the value-pinning tests in ``test_ssr.py``: ``shape_signature`` of
``ssr.parse_search`` / ``ssr.parse_card`` output over each committed captured
page (captured live in Jul 2026, per that module). A field no value assertion
looks at cannot disappear or retype silently — the shape changes and this test
names the exact paths that drifted.

Goldens are measured (parsers run over the fixtures), never hand-written.
Pure Python: the SSR state is lifted out of the page without a browser, so
these run without Node and without network.

Two captures of the same endpoint pin the same thing twice on purpose: the
washer and iphone searches must fingerprint identically, so a future parser
change that starts depending on query-specific markup fails on exactly one of
them and the diff points at the divergence.
"""

from __future__ import annotations

from pathlib import Path

from mcp_core.resilience import shape_signature
from yandex_connector import ssr

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_GOLDEN = [
    "has_next_page:bool",
    "items[].brand:str",
    "items[].currency:str",
    "items[].image:str",
    "items[].in_stock:bool",
    "items[].is_express:bool",
    "items[].price_old_rub:null",
    "items[].price_rub:float",
    "items[].price_with_plus:float",
    "items[].product_id:str",
    "items[].rating:float",
    "items[].rating_count:int",
    "items[].seller:str",
    "items[].sku_id:str",
    "items[].source:str",
    "items[].title:str",
    "items[].url:str",
    "page:int",
    "page_count:int",
    "query:str",
    "status:str",
    "total:int",
]

SEARCH_EMPTY_GOLDEN = [
    "has_next_page:bool",
    "items:empty_array",
    "page:null",
    "page_count:null",
    "query:str",
    "status:str",
    "total:null",
]

CARD_GOLDEN = [
    "brand:str",
    "currency:str",
    "description:str",
    "discount_percent:int",
    "image:str",
    "offers_count:null",
    "price_before_discount_rub:float",
    "price_rub:float",
    "price_with_plus:float",
    "product_id:str",
    "rating:float",
    "rating_count:int",
    "rating_stars.1:int",
    "rating_stars.2:int",
    "rating_stars.3:int",
    "rating_stars.4:int",
    "rating_stars.5:int",
    "review_count:int",
    "reviews[].author:str",
    "reviews[].comment:str",
    "reviews[].cons:str",
    "reviews[].date:str",
    "reviews[].photos:empty_array",
    "reviews[].photos[]:str",
    "reviews[].pros:str",
    "reviews[].rating:int",
    "reviews[].votes_down:int",
    "reviews[].votes_up:int",
    "seller:str",
    "sku_id:str",
    "status:str",
    "title:str",
]

CARD_NO_RATING_GOLDEN = [
    "brand:str",
    "currency:str",
    "description:str",
    "discount_percent:int",
    "image:str",
    "offers_count:int",
    "price_before_discount_rub:float",
    "price_rub:float",
    "price_with_plus:float",
    "product_id:str",
    "rating:null",
    "rating_count:null",
    "rating_stars:empty_object",
    "review_count:null",
    "reviews:empty_array",
    "seller:str",
    "sku_id:str",
    "status:str",
    "title:str",
]


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_search_shape_matches_the_washer_capture() -> None:
    assert shape_signature(ssr.parse_search(_load("search_washer.html"))) == SEARCH_GOLDEN


def test_search_shape_matches_the_iphone_capture() -> None:
    assert shape_signature(ssr.parse_search(_load("search_iphone.html"))) == SEARCH_GOLDEN


def test_empty_search_shape_is_its_own_reference() -> None:
    """Empty results are a shape of their own; an empty page must not be
    fingerprinted like a populated one, or drift hides inside 'no results'."""
    assert shape_signature(ssr.parse_search(_load("search_empty.html"))) == SEARCH_EMPTY_GOLDEN


def test_card_shape_matches_the_washer_capture() -> None:
    assert shape_signature(ssr.parse_card(_load("card_washer.html"))) == CARD_GOLDEN


def test_card_shape_matches_the_no_rating_capture() -> None:
    assert shape_signature(ssr.parse_card(_load("card_no_rating.html"))) == CARD_NO_RATING_GOLDEN
