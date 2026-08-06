"""Reference shape signatures for the DNS extractors.

Generated from the captured fixtures (see ``tests/fixtures/*.provenance.json``)
by running the real extractor over the capture and fingerprinting the payload
with ``mcp_core.resilience.shape_signature``. Two consumers:

* ``dns_selfcheck`` compares a live payload against
  ``SEARCH_SHAPE_REFERENCE`` and reports HOW the shape moved (missing/added
  paths) instead of only "zero tiles";
* ``tests/test_shape_reference.py`` asserts the references still agree with
  the fixtures, so the registry cannot go stale silently.

Never edit by hand: regenerate by re-running the extractor over a fresh
captured page and replacing the fingerprint.
"""

from __future__ import annotations

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "items[].availability_text:str",
    "items[].old_price_text:null",
    "items[].old_price_text:str",
    "items[].price_text:str",
    "items[].product_id:str",
    "items[].title:str",
    "items[].url:str",
    "title:str",
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
    "availability_text:str",
    "is_available:bool",
    "old_price_text:null",
    "page_title:str",
    "price_text:str",
    "title:str",
)

# Paths whose PRESENCE the parser depends on; the type may legitimately vary
# with the data (``old_price_text:str`` on a discounted tile, ``:null`` on a
# tile without one). A live payload missing one of these keys entirely is
# structural drift, not data variation — that distinction is the whole point
# of the selfcheck. Only keys `_search_item_from_tile` actually reads belong
# here: everything else would cry drift while the parser still works.
SEARCH_REQUIRED_KEYS: tuple[str, ...] = (
    "items[].product_id",
    "items[].title",
    "items[].price_text",
    "items[].url",
    "title",
)
