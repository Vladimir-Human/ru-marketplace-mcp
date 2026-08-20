"""Pydantic output models for the AliExpress MCP connector."""

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """AliExpress carries the shared envelope unchanged."""


class AliSearchItemOut(BaseModel):
    item_id: str | None = Field(default=None, description="AliExpress item id (12-digit).")
    title: str | None = Field(default=None, description="Product title from the search tile.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(default=None, description="Strikethrough/base price in rubles.")
    rating: float | None = Field(default=None, description="Tile rating, 0..5.")
    orders_count: int | None = Field(default=None, description="'N купили' from the tile — orders, not reviews.")
    sku_id: str | None = Field(default=None, description="sku_id query parameter, when the tile carries one.")
    url: str | None = Field(default=None, description="Canonical item URL.")


class AliSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    tier_used: str | None = Field(default=None, description="Fetch tier used (cdp).")
    count: int = Field(default=0, description="Number of items returned.")
    items: list[AliSearchItemOut] = Field(default_factory=list, description="Search result items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AliCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    item_id: str | None = Field(default=None, description="AliExpress item id (12-digit).")
    title: str | None = Field(default=None, description="Product title (the card h1).")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(default=None, description="Base/strikethrough price in rubles.")
    rating: float | None = Field(default=None, description="Product rating from the card module.")
    orders_count: int | None = Field(
        default=None, description="Order count from the page SEO block; None when drift-prone meta absent."
    )
    url: str = Field(default="", description="Canonical item URL.")
    tier_used: str = Field(default="", description="Fetch tier used (cdp).")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class AliSelfcheckCheckOut(SelfCheckEntryBase):
    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class AliSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, AliSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
