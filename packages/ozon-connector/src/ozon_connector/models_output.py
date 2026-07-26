"""Pydantic output models for the Ozon MCP connector (Stage 2).

Every tool returns a typed Pydantic model instead of a raw dict. Validation
and transport failures raise ``ToolError`` via ``mcp_common.errors``; these
models describe only the success (and partial-success) shapes.

The ``meta`` field is populated from the ``_meta`` key that
``resilience.attach_meta`` writes, and serializes back to ``meta`` on the wire.
"""

from __future__ import annotations

from typing import Any

from mcp_core.models import MetaOutBase, SelfCheckEntryBase, SelfCheckResponseBase
from pydantic import BaseModel, ConfigDict, Field


class MetaOut(MetaOutBase):
    """Ozon carries the shared envelope unchanged."""


class OzonSellerOut(BaseModel):
    name: str | None = Field(default=None, description="Seller display name.")
    link: str | None = Field(default=None, description="Seller profile link.")


class OzonCardResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    price: float | None = Field(default=None, description="Regular price in rubles.")
    card_price: float | None = Field(default=None, description="Ozon-card price (lowest) in rubles.")
    price_original: float | None = Field(default=None, description="Strikethrough original price in rubles.")
    is_available: bool | None = Field(default=None, description="Whether the product is sellable now.")
    rating_score: float | None = Field(default=None, description="Aggregate review score.")
    rating_count: int | None = Field(default=None, description="Total review count.")
    title: str | None = Field(default=None, description="Product title.")
    seller: OzonSellerOut | None = Field(default=None, description="Seller info.")
    characteristics: list[Any] = Field(default_factory=list, description="Short characteristics (max 30).")
    url: str = Field(default="", description="Canonical Ozon product URL.")
    tier_used: str = Field(default="", description="Fetch tier used: curl_cffi, cdp, etc.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class OzonReviewItemOut(BaseModel):
    score: int | None = Field(default=None, description="Review star score 1..5.")
    text: str = Field(default="", description="Review comment text (truncated to 1500 chars).")
    positive: str = Field(default="", description="Positive review text (truncated to 500 chars).")
    negative: str = Field(default="", description="Negative review text (truncated to 500 chars).")
    useful: int | None = Field(default=None, description="Helpfulness vote count.")
    photos: int = Field(default=0, description="Number of photos attached.")
    author: str = Field(default="", description="Author first name (truncated to 60 chars).")
    date: str | None = Field(default=None, description="Publication date as UTC ISO-8601.")


class OzonReviewsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    url: str = Field(default="", description="Canonical Ozon reviews URL.")
    tier_used: str | None = Field(default=None, description="Fetch tier used for the first page.")
    sort: str = Field(default="", description="API sort key used (published_at_desc, score_desc, score_asc).")
    rating_score: float | None = Field(default=None, description="Aggregate review score.")
    reviews_count: int | None = Field(default=None, description="Total review count from paging or score widget.")
    distribution: dict[str, Any] = Field(default_factory=dict, description="Star distribution: stars -> count.")
    pages_fetched: int = Field(default=0, description="Number of pages fetched (max 10).")
    returned: int = Field(default=0, description="Number of review texts returned.")
    partial: bool = Field(default=False, description="Whether a later-page failure degraded to partial success.")
    stop_reason: str | None = Field(
        default=None, description="Why pagination stopped: http, parse, blocked, max_pages, etc."
    )
    last_error: dict[str, Any] | None = Field(default=None, description="Last error detail on partial success.")
    requested_limit: int = Field(default=0, description="The limit argument requested by the caller.")
    reviews: list[OzonReviewItemOut] = Field(default_factory=list, description="Collected review items.")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class OzonSearchItemOut(BaseModel):
    sku: int | str | None = Field(default=None, description="Product SKU (nmId).")
    url: str | None = Field(default=None, description="Canonical Ozon product URL.")
    canonical_path: str | None = Field(default=None, description="Canonical /product/<digits>/ path.")
    card_input: str | None = Field(default=None, description="Input string to pass to ozon_card for full details.")
    title: str | None = Field(default=None, description="Product title.")
    price: str | None = Field(default=None, description="Display price string.")
    price_original: str | None = Field(default=None, description="Strikethrough original price string.")
    rating: str | None = Field(default=None, description="Rating display string.")
    rating_count: int | str | None = Field(default=None, description="Review count (int or display string).")
    stock: str | None = Field(default=None, description="Stock label string.")


class OzonSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    page: int = Field(default=0, description="Search page number.")
    tier_used: str | None = Field(default=None, description="Fetch tier used.")
    count: int = Field(default=0, description="Number of items returned.")
    items: list[OzonSearchItemOut] = Field(default_factory=list, description="Search result items (max 30).")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


class OzonSelfcheckCheckOut(SelfCheckEntryBase):
    """Ozon sub-check entry: adds the baseline-comparison fields Ozon reports."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class OzonSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, OzonSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
