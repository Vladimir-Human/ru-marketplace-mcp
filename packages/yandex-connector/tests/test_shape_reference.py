"""Reference shape signatures for the Yandex Market SSR parsers, pinned to captures.

Companion to the value-pinning tests in ``test_ssr.py``: ``shape_signature`` of
``ssr.parse_search`` / ``ssr.parse_card`` output over each committed captured
page (captured live in Jul 2026, per that module). A field no value assertion
looks at cannot disappear or retype silently — the shape changes and this test
names the exact paths that drifted.

The populated-page goldens live in ``yandex_connector.shape_reference`` for
consistency with the citilink/dns registries; here the consumer is this test
alone — the yandex parser emits every item key unconditionally, so unlike
citilink/dns there is no runtime shape-diff of live payloads (see that module's
docstring for why; empty-value drift is covered by ``_guard_parse_status`` and
the selfcheck). The empty-search and no-rating variants stay here: they are
diagnostic shapes that complement the populated-page reference.

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
from yandex_connector import shape_reference, ssr

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_EMPTY_GOLDEN = [
    "has_next_page:bool",
    "items:empty_array",
    "page:null",
    "page_count:null",
    "query:str",
    "status:str",
    "total:null",
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
    assert shape_signature(ssr.parse_search(_load("search_washer.html"))) == list(
        shape_reference.SEARCH_SHAPE_REFERENCE
    )


def test_search_shape_matches_the_iphone_capture() -> None:
    assert shape_signature(ssr.parse_search(_load("search_iphone.html"))) == list(
        shape_reference.SEARCH_SHAPE_REFERENCE
    )


def test_empty_search_shape_is_its_own_reference() -> None:
    """Empty results are a shape of their own; an empty page must not be
    fingerprinted like a populated one, or drift hides inside 'no results'."""
    assert shape_signature(ssr.parse_search(_load("search_empty.html"))) == SEARCH_EMPTY_GOLDEN


def test_card_shape_matches_the_washer_capture() -> None:
    assert shape_signature(ssr.parse_card(_load("card_washer.html"))) == list(shape_reference.CARD_SHAPE_REFERENCE)


def test_card_shape_matches_the_no_rating_capture() -> None:
    assert shape_signature(ssr.parse_card(_load("card_no_rating.html"))) == CARD_NO_RATING_GOLDEN
