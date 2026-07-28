"""Pydantic output models for the Megamarket MCP connector."""

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """Megamarket carries the shared envelope unchanged."""


class MegamarketSearchItemOut(BaseModel):
    item_id: str | None = Field(default=None, description="Megamarket goods id.")
    title: str | None = Field(default=None, description="Product title.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(default=None, description="Strikethrough price in rubles.")
    rating: float | None = Field(default=None, description="Average rating.")
    rating_count: int | None = Field(default=None, description="Review count.")
    url: str | None = Field(default=None, description="Canonical product URL.")
    is_available: bool | None = Field(
        default=None,
        description="Stock status as the search payload reports it (isAvailable). None when absent, never False by default.",
    )


class MegamarketSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    tier_used: str | None = Field(default=None, description="Fetch tier used (cdp).")
    count: int = Field(default=0, description="Number of items returned.")
    total_count: int | None = Field(default=None, description="Total matches reported.")
    items: list[MegamarketSearchItemOut] = Field(default_factory=list, description="Search result items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class MegamarketCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    item_id: str | None = Field(default=None, description="Megamarket goods id.")
    title: str | None = Field(default=None, description="Product title.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(default=None, description="Strikethrough price in rubles.")
    is_available: bool | None = Field(default=None, description="Whether the product is sellable now.")
    rating: float | None = Field(default=None, description="Average rating.")
    rating_count: int | None = Field(default=None, description="Review count.")
    url: str = Field(default="", description="Canonical product URL.")
    tier_used: str = Field(default="", description="Fetch tier used (cdp).")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class MegamarketSelfcheckCheckOut(SelfCheckEntryBase):
    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class MegamarketSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, MegamarketSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
