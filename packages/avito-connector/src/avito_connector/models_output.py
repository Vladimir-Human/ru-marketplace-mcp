"""Pydantic output models for the Avito MCP connector.

Every tool returns a typed Pydantic model instead of a raw dict. The ``meta``
field is populated from the ``_meta`` key that ``resilience.attach_meta``
writes, and serializes back to ``meta`` on the wire.
"""

from __future__ import annotations

from typing import Literal

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """Avito carries the shared envelope unchanged."""


class AvitoSearchItemOut(BaseModel):
    item_id: int | None = Field(default=None, description="Avito item id (the digits in the listing URL).")
    title: str | None = Field(default=None, description="Listing title.")
    price_rub: float | None = Field(
        default=None,
        description="Price a buyer pays now, after an Avito sale discount; None when absent or the discount does not add up.",
    )
    old_price_rub: float | None = Field(
        default=None, description="Struck-through price during an Avito sale, else None."
    )
    discount_percent: int | None = Field(
        default=None, description="Avito's sale percent (computed from the two prices only when Avito shows none)."
    )
    url: str | None = Field(default=None, description="Canonical avito.ru listing URL.")
    location: str | None = Field(default=None, description="Seller location string.")
    seller_name: str | None = Field(default=None, description="Seller display name.")
    seller_id: str | None = Field(default=None, description="Seller/user id when present.")
    is_company: bool | None = Field(default=None, description="Whether the seller is a company profile.")
    posted_at: str | None = Field(default=None, description="Publication time as reported by Avito.")
    images: int = Field(default=0, description="Number of images attached to the listing.")
    availability: Literal["active", "reserved", "unknown"] = Field(
        default="unknown",
        description=(
            "From the hit's own isReserved flag: reserved = another buyer holds it, not purchasable now; "
            "unknown when the flag is absent. Never guessed."
        ),
    )


class AvitoSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    page: int = Field(default=1, description="Result page number.")
    location_id: str = Field(default="", description="Avito location id the search ran against.")
    tier_used: str | None = Field(default=None, description="Fetch tier used: curl_cffi, cdp, cache.")
    count: int = Field(default=0, description="Number of items returned on this page.")
    total_count: int | None = Field(default=None, description="Total matches Avito reports for the query.")
    items: list[AvitoSearchItemOut] = Field(default_factory=list, description="Search result items.")
    excluded_inactive: int = Field(
        default=0,
        description=(
            "Reserved hits dropped from items because include_inactive was false (0 by default, when they are kept); "
            "total_count still counts them."
        ),
    )
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AvitoSellerOut(BaseModel):
    name: str | None = Field(default=None, description="Seller display name.")
    seller_id: str | None = Field(default=None, description="Seller/user id when present.")
    is_company: bool | None = Field(default=None, description="Whether this is a company profile.")
    rating_score: float | None = Field(default=None, description="Seller rating score.")
    rating_count: int | None = Field(default=None, description="Seller review count.")
    profile_url: str | None = Field(default=None, description="Seller profile URL.")
    checks: list[str] = Field(
        default_factory=list,
        description=(
            "Seller checks the item page states (e.g. «Данные подтверждены»): verification flags that are true, "
            "and check badges; promotional badges excluded; at most 10."
        ),
    )
    tenure: str | None = Field(
        default=None, description="Avito tenure medal as the page names it (e.g. gold); not a date."
    )
    seller_since: str | None = Field(default=None, description="«На Авито с …» when the page gives a year; else None.")
    reply_time: str | None = Field(default=None, description="Seller reply-time text shown on the item page.")


class AvitoParamOut(BaseModel):
    name: str = Field(description="Parameter title as the item page shows it (Характеристики).")
    value: str = Field(description="Parameter value as the item page shows it.")


class AvitoMetroOut(BaseModel):
    name: str = Field(description="Nearby metro/railway station or landmark from the item's location block.")
    distance: str | None = Field(default=None, description="Distance text, e.g. 600 м.")
    walk: str | None = Field(default=None, description="Walking time text, e.g. 6–10 мин.")


class AvitoPhotoOut(BaseModel):
    url: str = Field(description="Photo URL on *.img.avito.st (640x480 variant when available).")
    delivered: bool = Field(default=False, description="True when this photo is attached as MCP image content.")
    bytes: int | None = Field(default=None, description="Size of the attached image in bytes.")


PhotosStatus = Literal[
    "ok",
    "fewer_on_page",
    "skipped_inactive",
    "none_on_page",
    "refused_403",
    "rate_limited",
    "refused_http",
    "too_large",
    "bad_type",
    "fetch_failed",
]


class AvitoCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    item_id: int | None = Field(default=None, description="Avito item id.")
    title: str | None = Field(default=None, description="Listing title.")
    price_rub: float | None = Field(
        default=None,
        description="Price a buyer pays now, after an Avito sale discount; None when absent or the discount does not add up.",
    )
    old_price_rub: float | None = Field(
        default=None, description="Struck-through price during an Avito sale, else None."
    )
    discount_percent: int | None = Field(
        default=None, description="Avito's sale percent (computed from the two prices only when Avito shows none)."
    )
    description: str | None = Field(default=None, description="Listing description text.")
    location: str | None = Field(default=None, description="Item location string.")
    posted_at: str | None = Field(default=None, description="Publication time as reported by Avito.")
    views: int | None = Field(default=None, description="Total view count.")
    images: int | None = Field(default=None, description="Number of images attached; None when the page does not say.")
    seller: AvitoSellerOut | None = Field(default=None, description="Seller info.")
    url: str = Field(default="", description="Canonical avito.ru listing URL.")
    tier_used: str = Field(default="", description="Fetch tier used.")
    availability: Literal["active", "reserved", "unknown"] = Field(
        default="unknown",
        description=(
            "Listing status from Avito's own page flags and safe-deal banner, never from seller text: reserved = "
            "another buyer holds it, not purchasable now. schema.org InStock is not trusted (Avito says it for "
            "reserved ads too); unknown when the page is silent or its signals contradict each other."
        ),
    )
    availability_evidence: list[str] = Field(
        default_factory=list,
        description="The page flags or banner text the availability rests on; on a contradiction, both sides.",
    )
    condition: str | None = Field(
        default=None, description="Item condition («Состояние»: Б/у, Новое); None when not stated."
    )
    params: list[AvitoParamOut] = Field(default_factory=list, description="Item parameters (Характеристики).")
    delivery_available: bool | None = Field(default=None, description="Avito Delivery is offered for this item.")
    delivery_services: list[str] = Field(
        default_factory=list,
        description="Enabled delivery options, deduplicated, at most 10: pvz, courier, postamat, click_and_collect, ...",
    )
    payment_on_delivery: bool | None = Field(
        default=None, description="Payment on receipt is possible (cash on delivery)."
    )
    safe_deal: bool | None = Field(
        default=None,
        description=(
            "Buying through Avito payment and delivery («Купить с доставкой») is enabled; None when the page "
            "does not say, or shows an enabled button without a recognisable title."
        ),
    )
    price_offer_enabled: bool | None = Field(
        default=None,
        description=(
            "True when the «Предложить цену» form is on the page; None otherwise (never guessed False). "
            "It is not a statement that the price is negotiable."
        ),
    )
    bargain: bool | None = Field(
        default=None,
        description=(
            "From the explicit «Торг» parameter, else the listing text (negations such as «Торг: нет», «без торга» "
            "first); None when not stated or when the text and the parameter disagree."
        ),
    )
    item_badges: list[str] = Field(default_factory=list, description="Listing badges, e.g. «Авито Гарантия: …».")
    metro: list[AvitoMetroOut] = Field(default_factory=list, description="Nearby stations from the location block.")
    address: str | None = Field(
        default=None,
        description=(
            "Street address as the page gives it, only when every seller-type source on the page says company; "
            "None for a private seller, an unstated type or disagreeing sources."
        ),
    )
    coords: dict[str, float] | None = Field(
        default=None,
        description="Map point {lat, lng} of that address, under the same rule as address; otherwise None.",
    )
    today_views: int | None = Field(default=None, description="Views today.")
    photos: list[AvitoPhotoOut] = Field(
        default_factory=list, description="Photos tried with photos=N; delivered ones follow as image content."
    )
    photos_status: PhotosStatus | None = Field(
        default=None,
        description=(
            "None when photos=0. ok: every requested photo delivered; fewer_on_page: all the page had, fewer than "
            "asked; skipped_inactive: the ad is not active; "
            "none_on_page; refused_403 / rate_limited (429) / refused_http (other status); too_large; bad_type; "
            "fetch_failed. The first refusal stops the photo loop and is never retried."
        ),
    )
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AvitoSellerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    seller: AvitoSellerOut | None = Field(default=None, description="Seller info.")
    active_items: int | None = Field(default=None, description="Number of active listings the seller reports.")
    tier_used: str = Field(default="", description="Fetch tier used.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AvitoSelfcheckCheckOut(SelfCheckEntryBase):
    """Avito sub-check entry: adds the baseline-comparison fields."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class AvitoSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, AvitoSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
