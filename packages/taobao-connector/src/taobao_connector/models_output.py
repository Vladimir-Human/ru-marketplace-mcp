"""Pydantic output models for the Taobao MCP connector.

Prices stay in yuan (CNY): converting to rubles would need a rate source and a
timestamp, and a silently stale conversion is worse than an honest currency
label. The compare layer converts explicitly when it needs to rank.
"""

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """Taobao carries the shared envelope unchanged."""


class TaobaoSearchItemOut(BaseModel):
    item_id: str | None = Field(
        default=None, description="Taobao item id (numeric string; ids exceed JS-safe int range)."
    )
    title: str | None = Field(default=None, description="Listing title (Chinese).")
    price_cny: float | None = Field(default=None, description="Price in yuan; None when the listing hides its price.")
    shop_name: str | None = Field(default=None, description="Shop display name.")
    location: str | None = Field(default=None, description="Ship-from location string.")
    sales: str | None = Field(
        default=None, description="Sales label as displayed (e.g. '2000+人付款'); kept verbatim, counts vary by panel."
    )
    url: str | None = Field(default=None, description="Canonical item URL on item.taobao.com.")


class TaobaoSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    page: int = Field(default=1, description="Result page number.")
    tier_used: str | None = Field(default=None, description="Fetch tier used (cdp).")
    count: int = Field(default=0, description="Number of items returned on this page.")
    items: list[TaobaoSearchItemOut] = Field(default_factory=list, description="Search result items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class TaobaoCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    item_id: str | None = Field(default=None, description="Taobao item id.")
    title: str | None = Field(default=None, description="Item title (Chinese).")
    price_cny: float | None = Field(default=None, description="Price in yuan; None when hidden or variant-priced.")
    shop_name: str | None = Field(default=None, description="Shop display name.")
    sales: str | None = Field(default=None, description="Sales label as displayed.")
    description_images: int = Field(default=0, description="Number of images in the description block.")
    url: str = Field(default="", description="Canonical item URL.")
    tier_used: str = Field(default="", description="Fetch tier used (cdp).")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class TaobaoSelfcheckCheckOut(SelfCheckEntryBase):
    """Taobao sub-check entry."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class TaobaoSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, TaobaoSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
