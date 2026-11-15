"""Pydantic output models for the Lamoda MCP connector."""
# START_MODULE_CONTRACT
# PURPOSE: Describe Lamoda outputs and qualify shown old prices without claiming verified history.
# SCOPE: Schema descriptions only; preserve field types, defaults, aliases and serialization.
# DEPENDS: mcp_core.models/sources; Pydantic.
# LINKS: M-LAMODA; M-SOURCES; R-MARKETPLACE-PRICE-WORDING.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# LamodaSearchItemOut/LamodaSearchResponse: Describe native search items and their envelope.
# LamodaCardResponse/LamodaSizeOut: Describe the card, reference price and size availability.
# MetaOut/LamodaSelfcheckResponse: Preserve shared diagnostics and selfcheck outputs.
# END_MODULE_MAP

from __future__ import annotations

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from mcp_core.sources import MARKETPLACE_PRICE_WORDING
from pydantic import BaseModel, ConfigDict, Field


# START_BLOCK_OUTPUT_MODELS
class MetaOut(MetaOutBase):
    """Lamoda carries the shared envelope unchanged."""


class LamodaSearchItemOut(BaseModel):
    sku: str | None = Field(default=None, description="Lamoda SKU (e.g. MP002XM1RMM3).")
    title: str | None = Field(default=None, description="Product title.")
    brand: str | None = Field(default=None, description="Brand name.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(
        default=None, description=f"Strikethrough price in rubles — {MARKETPLACE_PRICE_WORDING}."
    )
    url: str | None = Field(default=None, description="Canonical product URL.")


class LamodaSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    tier_used: str | None = Field(default=None, description="Fetch tier used (cdp).")
    count: int = Field(default=0, description="Number of items returned.")
    items: list[LamodaSearchItemOut] = Field(default_factory=list, description="Search result items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class LamodaSizeOut(BaseModel):
    size: str | None = Field(default=None, description="Size label.")
    is_available: bool | None = Field(default=None, description="Whether the size is sellable.")


class LamodaCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    sku: str | None = Field(default=None, description="Lamoda SKU.")
    title: str | None = Field(default=None, description="Product title.")
    brand: str | None = Field(default=None, description="Brand name.")
    price_rub: float | None = Field(default=None, description="Price in rubles; None when absent — never 0.")
    old_price_rub: float | None = Field(
        default=None, description=f"Strikethrough price in rubles — {MARKETPLACE_PRICE_WORDING}."
    )
    is_available: bool | None = Field(default=None, description="Whether the product is sellable now.")
    sizes: list[LamodaSizeOut] = Field(default_factory=list, description="Per-size availability.")
    url: str = Field(default="", description="Canonical product URL.")
    tier_used: str = Field(default="", description="Fetch tier used: graphql, graphql_cdp.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class LamodaSelfcheckCheckOut(SelfCheckEntryBase):
    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class LamodaSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, LamodaSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")


# END_BLOCK_OUTPUT_MODELS
