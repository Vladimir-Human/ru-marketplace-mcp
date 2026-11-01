"""Reference shape signatures for the Cian search/card parsers.

The search golden is the fingerprint of ``_parse_offers`` over the captured
sale payload (``tests/fixtures/search_sale_live.json``, captured 2026-09-09;
provenance recorded alongside); the card golden is ``_parse_card`` over the
captured offer-card state. Both measured with
``mcp_core.resilience.shape_signature`` — the sorted paths and types of the
parser's own output, so a renamed or retyped upstream field fails offline
before it ships a silently-wrong page of offers.

``SEARCH_REQUIRED_FAMILIES`` is the runtime tripwire: ``cian_search`` feeds the
RAW payload to ``mcp_core.resilience.shape_drift_warning`` and the response's
``meta.warnings`` carries ``shape_drift: cian_search: missing <family>`` when a
whole parser-critical alias family vanishes. The parsed rows cannot serve that
guard — ``_offer_row`` emits every key unconditionally, so a key-presence check
on rows would never fire — while the raw envelope binds through the alias
families below, and the loss of one whole family is exactly the silent drift
the runtime guard exists to catch. A rename WITHIN a family is tolerated.

Never edit by hand: regenerate by re-running the parsers over a fresh capture.
"""

from __future__ import annotations

from collections.abc import Iterable

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "items[].address:str",
    "items[].agency_name:str",
    "items[].build_year:int",
    "items[].build_year:null",
    "items[].category:str",
    "items[].created_at:str",
    "items[].deal_type:str",
    "items[].deposit_rub:null",
    "items[].flat_type:str",
    "items[].floor:int",
    "items[].floors_total:int",
    "items[].is_apartments:bool",
    "items[].is_by_homeowner:bool",
    "items[].is_by_homeowner:null",
    "items[].kitchen_area_m2:float",
    "items[].lease_term:null",
    "items[].living_area_m2:float",
    "items[].living_area_m2:null",
    "items[].metro[].minutes:int",
    "items[].metro[].mode:str",
    "items[].metro[].name:str",
    "items[].newbuilding_name:null",
    "items[].newbuilding_name:str",
    "items[].offer_id:int",
    "items[].photos:int",
    "items[].price_period:null",
    "items[].price_rub:float",
    "items[].price_unit:str",
    "items[].rooms:int",
    "items[].seller_type:str",
    "items[].title:str",
    "items[].total_area_m2:float",
    "items[].url:str",
    "total_count:int",
)

CARD_SHAPE_REFERENCE: tuple[str, ...] = (
    "address:str",
    "agency_name:str",
    "agent.account_type:str",
    "agent.agent_id:int",
    "agent.is_developer:bool",
    "agent.name:str",
    "agent.offers_count:int",
    "agent.on_cian_since:str",
    "agent.user_type:str",
    "build_year:null",
    "building_material:str",
    "category:str",
    "ceiling_height_m:float",
    "created_at:str",
    "deal_type:str",
    "deposit_rub:null",
    "description:str",
    "flat_type:str",
    "floor:int",
    "floors_total:int",
    "is_apartments:bool",
    "is_by_homeowner:null",
    "kitchen_area_m2:float",
    "lease_term:null",
    "living_area_m2:null",
    "metro[].minutes:null",
    "metro[].mode:null",
    "metro[].name:str",
    "newbuilding_name:str",
    "offer_id:int",
    "photo_urls[].is_layout:bool",
    "photo_urls[].url:str",
    "photos:int",
    "price_history[].changed_at:str",
    "price_history[].price_rub:float",
    "price_period:null",
    "price_rub:float",
    "price_unit:str",
    "rooms:int",
    "seller_type:str",
    "title:str",
    "total_area_m2:float",
    "updated_at:str",
    "url:null",
    "views:int",
)

# Key families the parser binds through on the RAW envelope, as alternatives
# (``_price_of`` reads bargainTerms.priceRur / bargainTerms.price, then
# priceTotalRur / priceTotal): drift means the WHOLE family vanished, not a
# single alias. The title family lists the two direct title sources
# ``_compose_title`` reads before it falls back to composing one from
# rooms/area — losing both is a structural change worth a warning.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("data.offersSerialized[].id",),
    (
        "data.offersSerialized[].title",
        "data.offersSerialized[].formattedShortInfo",
    ),
    ("data.offersSerialized[].fullUrl",),
    (
        "data.offersSerialized[].bargainTerms.priceRur",
        "data.offersSerialized[].bargainTerms.price",
        "data.offersSerialized[].priceTotalRur",
        "data.offersSerialized[].priceTotal",
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
