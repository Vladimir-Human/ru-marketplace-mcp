"""Pydantic output models for the Avito MCP connector.

Every tool returns a typed Pydantic model instead of a raw dict. The ``meta``
field is populated from the ``_meta`` key that ``resilience.attach_meta``
writes, and serializes back to ``meta`` on the wire.
"""

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """Avito carries the shared envelope unchanged."""


class AvitoSearchItemOut(BaseModel):
    item_id: int | None = Field(default=None, description="Avito item id (the digits in the listing URL).")
    title: str | None = Field(default=None, description="Listing title.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when the listing has no price.")
    url: str | None = Field(default=None, description="Canonical avito.ru listing URL.")
    location: str | None = Field(default=None, description="Seller location string.")
    seller_name: str | None = Field(default=None, description="Seller display name.")
    seller_id: str | None = Field(default=None, description="Seller/user id when present.")
    is_company: bool | None = Field(default=None, description="Whether the seller is a company profile.")
    posted_at: str | None = Field(default=None, description="Publication time as reported by Avito.")
    images: int = Field(default=0, description="Number of images attached to the listing.")


class AvitoSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    page: int = Field(default=1, description="Result page number.")
    location_id: str = Field(default="", description="Avito location id the search ran against.")
    tier_used: str | None = Field(default=None, description="Fetch tier used: curl_cffi, cdp, cache.")
    count: int = Field(default=0, description="Number of items returned on this page.")
    total_count: int | None = Field(default=None, description="Total matches Avito reports for the query.")
    items: list[AvitoSearchItemOut] = Field(default_factory=list, description="Search result items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AvitoSellerOut(BaseModel):
    name: str | None = Field(default=None, description="Seller display name.")
    seller_id: str | None = Field(default=None, description="Seller/user id when present.")
    is_company: bool | None = Field(default=None, description="Whether this is a company profile.")
    rating_score: float | None = Field(default=None, description="Seller rating score.")
    rating_count: int | None = Field(default=None, description="Seller review count.")
    profile_url: str | None = Field(default=None, description="Seller profile URL.")


class AvitoCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    item_id: int | None = Field(default=None, description="Avito item id.")
    title: str | None = Field(default=None, description="Listing title.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when the listing has no price.")
    description: str | None = Field(default=None, description="Listing description text.")
    location: str | None = Field(default=None, description="Item location string.")
    posted_at: str | None = Field(default=None, description="Publication time as reported by Avito.")
    views: int | None = Field(default=None, description="Total view count.")
    images: int = Field(default=0, description="Number of images attached.")
    seller: AvitoSellerOut | None = Field(default=None, description="Seller info.")
    url: str = Field(default="", description="Canonical avito.ru listing URL.")
    tier_used: str = Field(default="", description="Fetch tier used.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AvitoSellerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
