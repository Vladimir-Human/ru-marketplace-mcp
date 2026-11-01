"""Reference shape signatures for the Wildberries search/card parsers.

The goldens are the fingerprints of ``_card_item_dict`` — the one flattener
``wb_search``, ``wb_card`` and ``wb_category_products`` all share — over the
captured v9 search payload and the captured card/v4 payload
(``tests/fixtures/*.json``, provenance recorded alongside), measured with
``mcp_core.resilience.shape_signature``. Because both tools normalize through
the same function, the two references pin the shared item shape from both
entry points.

``SEARCH_REQUIRED_FAMILIES`` is the runtime tripwire: ``wb_search`` feeds the
RAW v9 products (wrapped as ``{"products": [...]}``) to
``mcp_core.resilience.shape_drift_warning``, and the response's
``meta.warnings`` carries ``shape_drift: wb_search: missing <family>`` when a
whole parser-critical key family vanishes. The normalized items cannot serve
that guard — ``_card_item_dict`` emits every key unconditionally, so a
key-presence check on items would never fire — while the raw product objects
bind through the families below (the price family spans the July-2026 v4
``sizes[].price`` nesting and the older top-level ``salePriceU``/``priceU``
kopecks, so a rollback cannot flip the guard). A rename WITHIN a family is
tolerated; the loss of every member is drift.

Never edit by hand: regenerate by re-running the flattener over a fresh
capture.
"""

from __future__ import annotations

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "products[].brand:str",
    "products[].color:str",
    "products[].feedbacks:int",
    "products[].in_stock:bool",
    "products[].name:str",
    "products[].nm_id:int",
    "products[].price_original_rub:float",
    "products[].price_rub:float",
    "products[].review_rating:float",
    "products[].review_rating:int",
    "products[].supplier:str",
    "products[].supplier_id:int",
    "products[].supplier_rating:float",
    "products[].total_quantity:int",
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
    "products[].brand:str",
    "products[].color:str",
    "products[].feedbacks:int",
    "products[].in_stock:bool",
    "products[].name:str",
    "products[].nm_id:int",
    "products[].price_original_rub:float",
    "products[].price_rub:float",
    "products[].review_rating:int",
    "products[].supplier:str",
    "products[].supplier_id:int",
    "products[].supplier_rating:float",
    "products[].total_quantity:int",
)

# Key families the flattener binds through on the RAW product objects, as
# alternatives (the price family covers both the current sizes[].price
# nesting and the pre-July-2026 top-level kopeck fields, and is
# presence-not-type: ``sizes[].price:null`` is a delisted item, not a lost
# price field): drift means the WHOLE family vanished, not a single alias.
# A delisted page carries ``"sizes": []`` on every product, which the
# signature writes as ``products[].sizes:empty_array``; an empty container
# has no element that could have lost its price key, so the shared matcher
# counts the family present there (warning on that documented state was the
# 2026-11-01 audit finding). Sizes entries that exist but carry no price key
# stay drift — that is a rename inside the array, not emptiness.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("products[].id",),
    ("products[].name",),
    (
        "products[].sizes[].price",
        "products[].salePriceU",
        "products[].priceU",
    ),
    ("products[].totalQuantity",),
)
