"""Pydantic output models for the Ozon MCP connector (Stage 2).

Every tool returns a typed Pydantic model instead of a raw dict. Validation
and transport failures raise ``ToolError`` via ``mcp_core.errors``; these
models describe only the success (and partial-success) shapes.

The ``meta`` field is populated from the ``_meta`` key that
``resilience.attach_meta`` writes, and serializes back to ``meta`` on the wire.
"""

# START_MODULE_CONTRACT
# PURPOSE: Define typed Ozon card, review, search and selfcheck wire responses.
# SCOPE: Review author identity removed; nullable RUB prices, 0..5 ratings, integer counts, the tile's price kind and unchanged aliases/envelopes.
# DEPENDS: Pydantic; mcp_core.models.
# LINKS: R-NO-PERSONAL-DATA; M-OZON; R-OZON-SEARCH-NUMERIC; R-COMPARE-IMAGE-URL; R-COMPARE-CONDITIONAL-PRICE; R-OZON-CUSTOMS-DUTY.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# OzonCardResponse/OzonReviewsResponse: Card and review response models.
# OzonSearchItemOut/OzonSearchResponse: Numeric tile values, the tile's price kind and bounded search results.
# OzonSelfcheckResponse: Operator-only canary response.
# END_MODULE_MAP

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
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    price: float | None = Field(default=None, description="Regular price in rubles.")
    card_price: float | None = Field(default=None, description="Ozon-card price (lowest) in rubles.")
    price_original: float | None = Field(default=None, description="Strikethrough original price in rubles.")
    customs_duty_rub: float | None = Field(
        default=None, description="Customs payment due on delivery, Ozon's estimate; not in price."
    )
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
    date: str | None = Field(default=None, description="Publication date as UTC ISO-8601.")
    item_id: int | None = Field(
        default=None,
        description=(
            "SKU of the variant this review is actually about. Ozon pools reviews across every "
            "variant on a card — and sometimes across different brands — so a review with an "
            "item_id other than the SKU you asked for describes a DIFFERENT product."
        ),
    )


class OzonReviewsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    url: str = Field(default="", description="Canonical Ozon reviews URL.")
    tier_used: str | None = Field(default=None, description="Fetch tier used for the first page.")
    sort: str = Field(
        default="",
        description="API sort key used (usefulness_desc, score_desc, score_asc); published_at_desc requests map to usefulness_desc.",
    )
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
    requested_item_id: int | None = Field(
        default=None, description="SKU the caller asked about — compare each review's item_id to it."
    )
    own_reviews: int = Field(
        default=0, description="How many of the returned reviews have item_id == requested_item_id."
    )
    pool_variants: dict[str, str] = Field(
        default_factory=dict,
        description="Variant SKU -> product name for every product sharing this review pool.",
    )
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


# START_BLOCK_SEARCH_OUTPUT
class OzonSearchItemOut(BaseModel):
    sku: int | str | None = Field(default=None, description="Product SKU (nmId).")
    url: str | None = Field(default=None, description="Canonical Ozon product URL.")
    canonical_path: str | None = Field(default=None, description="Canonical /product/<digits>/ path.")
    card_input: str | None = Field(default=None, description="Input string to pass to ozon_card for full details.")
    title: str | None = Field(default=None, description="Product title.")
    price: float | None = Field(
        default=None, description="Current/sale price shown on the tile in rubles; None when absent or unparseable."
    )
    price_original: float | None = Field(
        default=None,
        description="Strikethrough reference price in rubles, not an offer; None when absent or unparseable.",
    )
    price_kind: str | None = Field(
        default=None,
        description=(
            "'card' when the tile's priceV2.priceStyle.styleType is CARD_PRICE — that price is the "
            "Ozon-card price, not an everyday price. None for the other styles, though a tile without "
            "that badge was also measured showing the Ozon-card price (2026-11-17): the everyday price "
            "is ozon_card's price."
        ),
    )
    rating: float | None = Field(
        default=None, description="Product rating on the 0..5 scale; None when absent or invalid."
    )
    rating_count: int | None = Field(
        default=None,
        description=(
            "Review count. None when Ozon's display text does not name exactly one number — "
            "a magnitude form ('1.2K отзывов'), a range or two numbers are not guessed."
        ),
    )
    stock: str | None = Field(default=None, description="Stock label string.")
    image_url: str = Field(default="", description="Primary product image URL already present on the search tile.")


class OzonSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    status: str = Field(default="success", description="Response status: success or error.")
    query: str = Field(default="", description="Search query text.")
    page: int = Field(default=0, description="Search page number.")
    tier_used: str | None = Field(default=None, description="Fetch tier used.")
    count: int = Field(default=0, description="Number of items returned — always equal to len(items).")
    parsed_count: int = Field(
        default=0,
        description="Number of tiles parsed from the upstream page before the 30-item cap.",
    )
    truncated: bool = Field(
        default=False,
        description="True when more tiles were parsed than returned; count is still len(items).",
    )
    items: list[OzonSearchItemOut] = Field(default_factory=list, description="Search result items (max 30).")
    meta: MetaOut = Field(default_factory=MetaOut, alias="_meta", description="Validation metadata.")


# END_BLOCK_SEARCH_OUTPUT


class OzonSelfcheckCheckOut(SelfCheckEntryBase):
    """Ozon sub-check entry: adds the baseline-comparison fields Ozon reports."""

    ok: bool | None = Field(default=None, description="Boolean health summary if applicable.")
    baseline: str = Field(default="", description="Baseline identifier used for comparison.")
    reason: str | None = Field(default=None, description="Reason code for non-healthy verdicts.")


class OzonSelfcheckResponse(SelfCheckResponseBase):
    healthy: bool | None = Field(default=None, description="Whether all checks are healthy.")
    checks: dict[str, OzonSelfcheckCheckOut] = Field(default_factory=dict, description="Per-subcheck results.")
