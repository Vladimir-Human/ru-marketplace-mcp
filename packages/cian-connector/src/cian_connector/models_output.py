"""Typed responses for the Cian connector.

Field descriptions are written for a reader who cannot see Cian's API: they say
what a value means and when it is absent, because that is what an agent uses to
decide whether the tool answers the question. Prices are rubles and ``None``
when Cian shows no price — never 0 (see ``mcp_core.resilience.coerce_price``).
"""

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """Validation metadata attached to every response."""


class CianMetroOut(BaseModel):
    name: str | None = Field(default=None, description="Metro station name.")
    minutes: int | None = Field(default=None, description="Travel time to the station in minutes, as Cian reports it.")
    mode: str | None = Field(
        default=None,
        description="How the travel time is measured: 'walk' (on foot) or 'transport' (by public transport / car).",
    )


class CianSearchItemOut(BaseModel):
    offer_id: int | None = Field(default=None, description="Cian offer id (the digits in the offer URL).")
    title: str | None = Field(
        default=None,
        description=(
            "Offer headline. Cian sets it only on some offers; otherwise the connector composes one from "
            "rooms, area and floor, e.g. '1-комн. квартира, 38.1 м², 12/22 эт.'."
        ),
    )
    deal_type: str | None = Field(default=None, description="'sale' or 'rent'.")
    category: str | None = Field(
        default=None,
        description=(
            "Cian offer category, e.g. flatSale, newBuildingFlatSale, flatRent, roomSale, houseSale, "
            "officeSale. newBuilding* means a developer's primary-market offer."
        ),
    )
    price_rub: float | None = Field(
        default=None,
        description=(
            "Price in rubles: total for sale, per payment period for rent. None when Cian shows no price "
            "('цена не указана' / 'договорная') — never 0."
        ),
    )
    price_unit: str | None = Field(
        default=None,
        description=(
            "What price_rub buys: 'total' (a sale), 'month' (long-term rent) or 'day' (daily rent, a nightly "
            "rate). Never compare prices across units — a 5 000 ₽ night is not cheaper than a 90 000 ₽ month."
        ),
    )
    price_period: str | None = Field(
        default=None,
        description="Long-term rent only: Cian's own payment period, usually 'monthly'. Null on daily offers.",
    )
    lease_term: str | None = Field(
        default=None,
        description=(
            "Long-term rent only: lease term as Cian codes it — 'longTerm' (от года) or 'fewMonths' "
            "(на несколько месяцев). Null for sale and for daily rent."
        ),
    )
    deposit_rub: float | None = Field(default=None, description="Rent only: security deposit in rubles, if stated.")
    rooms: int | None = Field(default=None, description="Number of rooms; None for studios/free layouts and non-flats.")
    flat_type: str | None = Field(default=None, description="Cian flat type: 'rooms', 'studio' or 'openPlan'.")
    is_apartments: bool | None = Field(
        default=None, description="True when the unit is legally 'апартаменты', not a flat."
    )
    total_area_m2: float | None = Field(default=None, description="Total area in square metres.")
    living_area_m2: float | None = Field(default=None, description="Living area in square metres, if stated.")
    kitchen_area_m2: float | None = Field(default=None, description="Kitchen area in square metres, if stated.")
    floor: int | None = Field(default=None, description="Floor the unit is on.")
    floors_total: int | None = Field(default=None, description="Number of floors in the building.")
    build_year: int | None = Field(default=None, description="Year the building was built, if Cian states it.")
    address: str | None = Field(default=None, description="Address as Cian displays it (region, city, street, house).")
    metro: CianMetroOut | None = Field(default=None, description="Nearest metro station by travel time, if any.")
    newbuilding_name: str | None = Field(default=None, description="Residential complex (ЖК) name for new buildings.")
    url: str | None = Field(default=None, description="Canonical cian.ru offer URL (tracking parameters stripped).")
    agency_name: str | None = Field(default=None, description="Agency or developer name that published the offer.")
    seller_type: str | None = Field(
        default=None,
        description="Publisher type as Cian codes it: 'developer', 'realtor_based', 'agency', 'homeowner' etc.",
    )
    is_by_homeowner: bool | None = Field(default=None, description="True when the owner publishes without an agent.")
    created_at: str | None = Field(
        default=None, description="Offer creation time as reported by Cian (local ISO-8601)."
    )
    photos: int = Field(default=0, description="Number of photos attached to the offer.")


class CianSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    deal: str = Field(default="", description="Deal type the search ran with: sale or rent.")
    offer_type: str = Field(
        default="", description="Property type the search ran with: flat, room, house or commercial."
    )
    region: str = Field(default="", description="Cian region id the search ran against.")
    page: int = Field(default=1, description="Result page number (1-based).")
    tier_used: str | None = Field(default=None, description="Fetch tier used: cdp or cache.")
    count: int = Field(default=0, description="Number of offers returned on this page.")
    total_count: int | None = Field(
        default=None, description="Total matching offers Cian reports for the query (after de-duplication)."
    )
    items: list[CianSearchItemOut] = Field(default_factory=list, description="Search result offers.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class CianAgentOut(BaseModel):
    name: str | None = Field(default=None, description="Agent, agency or developer display name.")
    agent_id: int | None = Field(default=None, description="Cian user id of the publisher.")
    account_type: str | None = Field(default=None, description="Cian account type, e.g. 'agency', 'specialist'.")
    user_type: str | None = Field(
        default=None, description="Publisher type as Cian codes it: 'developer', 'realtor_based', 'homeowner' etc."
    )
    is_developer: bool | None = Field(default=None, description="True when the publisher is a developer (застройщик).")
    offers_count: int | None = Field(default=None, description="Number of active offers this publisher has on Cian.")
    on_cian_since: str | None = Field(default=None, description="When the publisher account was created (ISO-8601).")


class CianPriceChangeOut(BaseModel):
    changed_at: str | None = Field(default=None, description="When the price was set (ISO-8601 UTC).")
    price_rub: float | None = Field(default=None, description="Price in rubles after that change.")


class CianCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    offer_id: int | None = Field(default=None, description="Cian offer id.")
    title: str | None = Field(default=None, description="Offer headline (Cian's own, or composed — see cian_search).")
    deal_type: str | None = Field(default=None, description="'sale' or 'rent'.")
    category: str | None = Field(default=None, description="Cian offer category (see cian_search).")
    price_rub: float | None = Field(
        default=None,
        description=(
            "Price in rubles: total for a sale, per month for long-term rent, per night for daily rent. "
            "Read price_unit before comparing. None when not stated — never 0."
        ),
    )
    price_unit: str | None = Field(
        default=None,
        description="What price_rub buys: 'total' (sale), 'month' (long-term rent) or 'day' (daily rent).",
    )
    price_period: str | None = Field(
        default=None,
        description="Long-term rent only: Cian's own payment period, usually 'monthly'. Null on daily offers.",
    )
    lease_term: str | None = Field(
        default=None,
        description=(
            "Long-term rent only: 'longTerm' (от года) or 'fewMonths' (на несколько месяцев). "
            "Null for sale and for daily rent."
        ),
    )
    deposit_rub: float | None = Field(default=None, description="Rent only: security deposit in rubles, if stated.")
    rooms: int | None = Field(default=None, description="Number of rooms; None for studios and non-flats.")
    flat_type: str | None = Field(default=None, description="Cian flat type: 'rooms', 'studio' or 'openPlan'.")
    is_apartments: bool | None = Field(default=None, description="True when the unit is 'апартаменты'.")
    total_area_m2: float | None = Field(default=None, description="Total area in square metres.")
    living_area_m2: float | None = Field(default=None, description="Living area in square metres, if stated.")
    kitchen_area_m2: float | None = Field(default=None, description="Kitchen area in square metres, if stated.")
    floor: int | None = Field(default=None, description="Floor the unit is on.")
    floors_total: int | None = Field(default=None, description="Floors in the building.")
    build_year: int | None = Field(default=None, description="Construction year, if stated.")
    building_material: str | None = Field(
        default=None, description="Building material as Cian codes it (monolith, panel, brick…)."
    )
    ceiling_height_m: float | None = Field(default=None, description="Ceiling height in metres, if stated.")
    address: str | None = Field(default=None, description="Address as Cian displays it.")
    metro: list[CianMetroOut] = Field(default_factory=list, description="Nearby metro stations with travel time.")
    newbuilding_name: str | None = Field(default=None, description="Residential complex (ЖК) name, if any.")
    description: str | None = Field(default=None, description="Full offer description text.")
    agency_name: str | None = Field(default=None, description="Agency or developer name that published the offer.")
    seller_type: str | None = Field(
        default=None,
        description="Publisher type as Cian codes it: 'developer', 'realtor_based', 'agency', 'homeowner' etc.",
    )
    is_by_homeowner: bool | None = Field(default=None, description="True when the owner publishes without an agent.")
    photos: int = Field(default=0, description="Number of photos attached.")
    created_at: str | None = Field(default=None, description="Offer creation time (local ISO-8601).")
    updated_at: str | None = Field(default=None, description="Last edit time (ISO-8601 UTC).")
    views: int | None = Field(default=None, description="Total views Cian reports for the offer.")
    price_history: list[CianPriceChangeOut] = Field(
        default_factory=list, description="Price changes, newest first, as Cian records them."
    )
    agent: CianAgentOut | None = Field(default=None, description="Publisher: agent, agency or developer.")
    url: str = Field(default="", description="Canonical cian.ru offer URL.")
    tier_used: str = Field(default="", description="Fetch tier used: cdp or cache.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class CianSelfcheckCheckOut(SelfCheckEntryBase):
    """Cian sub-check entry: adds the baseline-comparison fields."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class CianSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, CianSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
