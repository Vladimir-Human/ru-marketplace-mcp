"""Reference shape signatures for the Citilink extractors.

Generated from the captured fixtures (see ``tests/fixtures/*.provenance.json``)
by running the real extractor over the capture and fingerprinting the payload
with ``mcp_core.resilience.shape_signature``. Two consumers:

* ``citilink_selfcheck`` compares a live payload against
  ``SEARCH_SHAPE_REFERENCE`` and reports HOW the shape moved (missing/added
  paths) instead of only "zero tiles";
* ``tests/test_shape_reference.py`` asserts the references still agree with
  the fixtures, so the registry cannot go stale silently.

Never edit by hand: regenerate by re-running the extractor over a fresh
captured page and replacing the fingerprint.
"""

from __future__ import annotations

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "items[].old_price_text:null",
    "items[].old_price_text:str",
    "items[].price_meta:str",
    "items[].price_text:str",
    "items[].price_texts.attached[]:str",
    "items[].price_texts.other:empty_array",
    "items[].price_texts.other[]:str",
    "items[].product_id:str",
    "items[].title:str",
    "items[].url:str",
    "title:str",
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
    "is_available:bool",
    "old_price_text:str",
    "page_title:str",
    "price_meta:str",
    "price_text:str",
    "price_texts.attached[]:str",
    "price_texts.other[]:str",
    "title:str",
)

# Paths whose PRESENCE the parser depends on; the type may legitimately vary
# with the data (``title:str`` on one page, ``title:null`` on a tile without a
# name). A live payload missing one of these keys entirely is structural drift,
# not data variation — that distinction is the whole point of the selfcheck.
SEARCH_REQUIRED_KEYS: tuple[str, ...] = (
    "items[].product_id",
    "items[].title",
    "items[].price_text",
    "items[].url",
    "title",
)
