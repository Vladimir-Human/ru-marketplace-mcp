"""Reference shape signatures for the Yandex Market SSR parsers.

Measured on the captured pages (see ``tests/fixtures/``, captured live in
Jul 2026) by running ``ssr.parse_search`` / ``ssr.parse_card`` and
fingerprinting the payload with ``mcp_core.resilience.shape_signature``.

Consumer: ``tests/test_shape_reference.py`` asserts the references still agree
with the fixtures, so a silent parser change that reshapes the payload fails
offline. Unlike citilink/dns, the yandex parser emits every item key
unconditionally, so a key-presence runtime guard would never fire — live drift
here surfaces as empty values and is covered by ``_guard_parse_status`` plus
the selfcheck's priced-items/title checks instead.

Never edit by hand: regenerate by re-running the parsers over a fresh capture.
"""

from __future__ import annotations

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
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
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
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
)
