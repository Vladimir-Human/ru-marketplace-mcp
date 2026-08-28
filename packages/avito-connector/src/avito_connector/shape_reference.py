"""Reference shape signature for the Avito js/items search payload.

Measured on the captured live payload (``tests/fixtures/js_items_live.json``,
captured 2026-08-29 from a residential session; provenance recorded alongside
the fixture's contract test). Two consumers:

* ``avito_selfcheck`` compares the live payload against
  ``SEARCH_REQUIRED_FAMILIES`` and reports drift with paths when a
  parser-critical family of keys vanishes. The parser binds through alias
  families (``id``/``itemId``/``item_id`` ...), so the check is per family,
  not per key: a rename WITHIN a family is tolerated, the loss of a whole
  family is drift.
* ``tests/test_shape_reference.py`` asserts the reference still agrees with
  the fixture, so the registry cannot go stale silently.

Envelope note, 2026-08-29: Avito moved the listings array from the payload's
top level into ``payload.catalog.items[]`` — the reference below was
re-fingerprinted from a fresh capture that carries the new envelope, and the
required families accept BOTH shapes so an A/B rollout cannot flip the canary.

Never edit by hand: regenerate by re-fingerprinting a fresh capture.
"""

from __future__ import annotations

from collections.abc import Iterable

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "catalog.items[].addressDetailed.locationName:str",
    "catalog.items[].allowTimeStamp:int",
    "catalog.items[].category.compare:bool",
    "catalog.items[].category.id:int",
    "catalog.items[].category.name:str",
    "catalog.items[].category.pageRootId:int",
    "catalog.items[].category.rootId:int",
    "catalog.items[].category.slug:str",
    "catalog.items[].id:int",
    "catalog.items[].imagesCount:int",
    "catalog.items[].isMarketplace:bool",
    "catalog.items[].location.id:int",
    "catalog.items[].location.isCurrent:bool",
    "catalog.items[].location.isRegion:bool",
    "catalog.items[].location.name:str",
    "catalog.items[].location.namePrepositional:str",
    "catalog.items[].location:null",
    "catalog.items[].locationId:int",
    "catalog.items[].priceDetailed.enabled:bool",
    "catalog.items[].priceDetailed.exponent:str",
    "catalog.items[].priceDetailed.fullString:str",
    "catalog.items[].priceDetailed.hasValue:bool",
    "catalog.items[].priceDetailed.postfix:str",
    "catalog.items[].priceDetailed.string:str",
    "catalog.items[].priceDetailed.stringWithoutDiscount:null",
    "catalog.items[].priceDetailed.title.full:<truncated>",
    "catalog.items[].priceDetailed.title.short:<truncated>",
    "catalog.items[].priceDetailed.titleDative:str",
    "catalog.items[].priceDetailed.value:int",
    "catalog.items[].priceDetailed.wasLowered:bool",
    "catalog.items[].rating.score:float",
    "catalog.items[].rating.score:int",
    "catalog.items[].rating.showChevronEnd:bool",
    "catalog.items[].rating.summary:str",
    "catalog.items[].sortTimeStamp:int",
    "catalog.items[].title:str",
    "catalog.items[].urlPath:str",
    "count:int",
    "itemsOnPage:int",
    "itemsOnPageMainSection:int",
    "mainCount:int",
    "totalCount:int",
    "totalElements:int",
)

# Key families the parser reads, as alternatives: the parser binds through
# every alias, so drift means the WHOLE family vanished, not a single key.
# Each family accepts both envelopes — the pre-2026-08 top-level items[] and
# the current catalog.items[] — because the parser already binds either way,
# and the canary must not cry drift when a client is served the old shape.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    (
        "catalog.items[].id",
        "catalog.items[].itemId",
        "catalog.items[].item_id",
        "items[].id",
        "items[].itemId",
        "items[].item_id",
    ),
    (
        "catalog.items[].title",
        "catalog.items[].name",
        "items[].title",
        "items[].name",
    ),
    (
        "catalog.items[].price",
        "catalog.items[].priceRub",
        "catalog.items[].price_rub",
        "catalog.items[].priceDetailed.value",
        "catalog.items[].priceDetailed.price",
        "items[].price",
        "items[].priceRub",
        "items[].price_rub",
        "items[].priceDetailed.value",
        "items[].priceDetailed.price",
    ),
)


def missing_required_families(signature: Iterable[str]) -> list[tuple[str, ...]]:
    """The required families that have no member present in the signature."""
    entries = set(signature)
    return [
        family
        for family in SEARCH_REQUIRED_FAMILIES
        if not any(entry.startswith(f"{path}:") for entry in entries for path in family)
    ]
