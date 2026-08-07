"""Reference shape signature for the Avito js/items search payload.

Measured on the captured live payload (``tests/fixtures/js_items_live.json``,
captured 2026-07-28 from a residential session; provenance recorded alongside
the fixture's contract test). Two consumers:

* ``avito_selfcheck`` compares the live payload against
  ``SEARCH_REQUIRED_FAMILIES`` and reports drift with paths when a
  parser-critical family of keys vanishes. The parser binds through alias
  families (``id``/``itemId``/``item_id`` ...), so the check is per family,
  not per key: a rename WITHIN a family is tolerated, the loss of a whole
  family is drift.
* ``tests/test_shape_reference.py`` asserts the reference still agrees with
  the fixture, so the registry cannot go stale silently.

Never edit by hand: regenerate by re-fingerprinting a fresh capture.
"""

from __future__ import annotations

from collections.abc import Iterable

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "count:int",
    "itemsOnPage:int",
    "items[].addressDetailed.locationName:str",
    "items[].allowTimeStamp:int",
    "items[].category.compare:bool",
    "items[].category.id:int",
    "items[].category.name:str",
    "items[].category.pageRootId:int",
    "items[].category.rootId:int",
    "items[].category.slug:str",
    "items[].description:str",
    "items[].geo.geoReferences:empty_array",
    "items[].geo.geoReferences[].content:<truncated>",
    "items[].id:int",
    "items[].imagesCount:int",
    "items[].images[].208x208:str",
    "items[].images[].236x236:str",
    "items[].images[].416x416:str",
    "items[].images[].472x472:str",
    "items[].isMarketplace:bool",
    "items[].location.id:int",
    "items[].location.isCurrent:bool",
    "items[].location.isRegion:bool",
    "items[].location.name:str",
    "items[].location.namePrepositional:str",
    "items[].location:null",
    "items[].locationId:int",
    "items[].priceDetailed.enabled:bool",
    "items[].priceDetailed.exponent:str",
    "items[].priceDetailed.fullString:str",
    "items[].priceDetailed.hasValue:bool",
    "items[].priceDetailed.postfix:str",
    "items[].priceDetailed.string:str",
    "items[].priceDetailed.stringWithoutDiscount:null",
    "items[].priceDetailed.title.full:str",
    "items[].priceDetailed.title.short:str",
    "items[].priceDetailed.titleDative:str",
    "items[].priceDetailed.value:int",
    "items[].priceDetailed.wasLowered:bool",
    "items[].rating.score:float",
    "items[].rating.score:int",
    "items[].rating.showChevronEnd:bool",
    "items[].rating.summary:str",
    "items[].sortTimeStamp:int",
    "items[].title:str",
    "items[].urlPath:str",
    "totalCount:int",
    "totalElements:int",
)

# Key families the parser reads, as alternatives: the parser binds through
# every alias, so drift means the WHOLE family vanished, not a single key.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("items[].id", "items[].itemId", "items[].item_id"),
    ("items[].title", "items[].name"),
    (
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
