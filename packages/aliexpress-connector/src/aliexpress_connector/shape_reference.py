"""Reference shape signatures for the AliExpress extractors.

Generated from the captured fixtures (see ``tests/fixtures/*.provenance.json``)
by running the real extractor over the capture and fingerprinting the payload
with ``mcp_core.resilience.shape_signature``. Two consumers:

* ``aliexpress_selfcheck`` compares a live payload against
  ``SEARCH_SHAPE_REFERENCE`` and reports HOW the shape moved (missing/added
  paths) instead of only "zero tiles";
* ``tests/test_shape_reference.py`` asserts the references still agree with
  the fixtures, so the registry cannot go stale silently.

The runtime companion is ``SEARCH_REQUIRED_KEYS``: ``aliexpress_search`` warns
``shape_drift: aliexpress_search: missing <path>`` through the shared
``mcp_core.resilience.shape_drift_warning`` when a payload that still yields
tiles lost a parser-critical key outright. That payload is the EXTRACTOR's own
output, and the extractor emits every key on every tile (2026-11-01 audit), so
a vanished key means the extractor stopped emitting it; a marketplace page
that changed shape reaches the agent as empty values instead, which
``validate_items`` reports as ``no_prices_on_page`` / ``title_missing``.
``price_texts.attached`` counts as present when it is an empty array: a tile
without a price is a data fact, a tile without the price module is drift.

Never edit by hand: regenerate by re-running the extractor over a fresh
captured page and replacing the fingerprint.
"""

from __future__ import annotations

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
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
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
    "meta_description:str",
    "price_texts.attached:empty_array",
    "price_texts.other:empty_array",
    "punish:bool",
    "rating_text:null",
    "title:str",
    "url:str",
)

# Paths whose PRESENCE the python-side parser depends on; the type may
# legitimately vary with the data (``sku_id:str`` on a tile with one,
# ``sku_id:null`` without — the extractor omits nothing the parser must have).
# Only keys ``_tile_prices`` / ``_item_from_payload`` actually read belong
# here: everything else would cry drift while the parser still works.
SEARCH_REQUIRED_KEYS: tuple[str, ...] = (
    "items[].item_id",
    "items[].title",
    "items[].price_texts.attached",
    "items[].url",
)
