"""Pydantic output models for the DNS-Shop MCP connector."""
# START_MODULE_CONTRACT
# PURPOSE: Describe DNS-Shop outputs and qualify shown old prices without claiming verified history.
# SCOPE: Schema descriptions only; preserve field types, defaults, aliases and serialization.
# DEPENDS: mcp_core.models/sources; Pydantic.
# LINKS: M-DNS; M-SOURCES; R-MARKETPLACE-PRICE-WORDING.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# DnsSearchItemOut/DnsSearchResponse: Describe native search items and their envelope.
# DnsCardResponse: Describe the requested card and marketplace reference price.
# MetaOut/DnsSelfcheckResponse: Preserve shared diagnostics and selfcheck outputs.
# END_MODULE_MAP

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from mcp_core.sources import MARKETPLACE_PRICE_WORDING
from pydantic import BaseModel, ConfigDict, Field


# START_BLOCK_OUTPUT_MODELS
class MetaOut(MetaOutBase):
    """DNS carries the shared envelope unchanged."""


class DnsSearchItemOut(BaseModel):
    product_id: str | None = Field(default=None, description="DNS product id/slug tail.")
    title: str | None = Field(default=None, description="Product title.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(
        default=None, description=f"Strikethrough price in rubles — {MARKETPLACE_PRICE_WORDING}."
    )
    url: str | None = Field(default=None, description="Canonical product URL.")


class DnsSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    tier_used: str | None = Field(default=None, description="Fetch tier used (cdp).")
    count: int = Field(default=0, description="Number of items returned.")
    items: list[DnsSearchItemOut] = Field(default_factory=list, description="Search result items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class DnsCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    product_id: str | None = Field(default=None, description="DNS product id/slug tail.")
    title: str | None = Field(default=None, description="Product title.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(
        default=None, description=f"Strikethrough price in rubles — {MARKETPLACE_PRICE_WORDING}."
    )
    is_available: bool | None = Field(default=None, description="Whether the product is sellable now.")
    url: str = Field(default="", description="Canonical product URL.")
    tier_used: str = Field(default="", description="Fetch tier used (cdp).")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class DnsSelfcheckCheckOut(SelfCheckEntryBase):
    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class DnsSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, DnsSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")


# END_BLOCK_OUTPUT_MODELS
