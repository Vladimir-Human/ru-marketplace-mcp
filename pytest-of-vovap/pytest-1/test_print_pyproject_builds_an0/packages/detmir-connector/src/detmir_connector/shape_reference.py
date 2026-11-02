"""Reference shape signatures for the Detsky Mir parsers.

The search golden is the fingerprint of ``_parse_product`` over the captured
category page (``tests/fixtures/category_live.json``, captured 2026-08-07;
provenance recorded alongside); the card golden is the same parser over the
captured card payload. Both measured with
``mcp_core.resilience.shape_signature`` — the sorted paths and types of the
parser's own output, so a renamed or retyped upstream field fails offline.

``SEARCH_REQUIRED_FAMILIES`` is the runtime tripwire: ``detmir_category`` feeds
the RAW items (wrapped as ``{"items": [...]}`` so both envelope shapes — the
object with ``meta`` and the bare array — fingerprint identically) to
``mcp_core.resilience.shape_drift_warning``, and the response's ``meta.warnings``
carries ``shape_drift: detmir_category: missing <family>`` when a whole
parser-critical alias family vanishes. The parsed products cannot serve that
guard — ``_parse_product`` emits every key unconditionally, so a key-presence
check on products would never fire — while the raw items bind through the
alias families below. A rename WITHIN a family is tolerated; the loss of every
member is drift.

Never edit by hand: regenerate by re-running the parser over a fresh capture.
"""

from __future__ import annotations

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "items[].article:str",
    "items[].availability:str",
    "items[].available_offline:bool",
    "items[].available_online:bool",
    "items[].brand:str",
    "items[].discount_percent:int",
    "items[].is_marketplace:bool",
    "items[].old_price_rub:float",
    "items[].old_price_rub:null",
    "items[].picture:str",
    "items[].price_rub:float",
    "items[].product_id:int",
    "items[].questions_count:int",
    "items[].rating:float",
    "items[].review_count:int",
    "items[].store_count:int",
    "items[].title:str",
    "items[].url:str",
    "items[].vendor:str",
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
    "article:str",
    "availability:str",
    "available_offline:bool",
    "available_online:bool",
    "brand:str",
    "discount_percent:int",
    "is_marketplace:bool",
    "old_price_rub:null",
    "picture:str",
    "price_rub:float",
    "product_id:int",
    "questions_count:int",
    "rating:float",
    "review_count:int",
    "store_count:int",
    "title:str",
    "url:str",
    "vendor:str",
)

# Key families the parser binds through on the RAW items, as alternatives
# (``_parse_product`` reads id/product_id, title/name, then price with a
# prices.sale/current and final_price fallback): drift means the WHOLE family
# vanished, not a single alias. ``mcp_core.resilience.shape_drift_warning``
# does the presence matching, including the object-valued ``items[].price``
# bind, whose entries extend the path with ``.`` rather than ``:``.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("items[].id", "items[].product_id"),
    ("items[].title", "items[].name"),
    ("items[].price", "items[].prices.sale", "items[].prices.current", "items[].final_price"),
)
