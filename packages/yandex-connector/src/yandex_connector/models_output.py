"""Typed responses for the Yandex Market connector.

Prices carry two fields on purpose. Yandex advertises a subscriber price
prominently, and quoting only that would misstate what most people pay — so
``price_rub`` (everyday) and ``price_with_plus`` (requires Yandex Plus) are always
reported separately.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MetaOut(BaseModel):
    source: str = Field(default="", description="Tool name that produced this response.")
    healthy: bool = Field(default=True, description="Whether the response passed structural validation.")
    warnings: list[str] = Field(
        default_factory=list, description="Connector-level warnings (drift, partial data, fallbacks)."
    )
    extraction: str = Field(
        default="", description="How data was extracted: 'ssr' (widget state) or 'ld+json' (degraded fallback)."
    )


class YandexProduct(BaseModel):
    """One Yandex Market product as it appears in search results."""

    product_id: str = Field(default="", description="Yandex Market product id — pass to yandex_card for full detail.")
    sku_id: str = Field(default="", description="SKU id of the specific offer shown.")
    title: str = Field(default="", description="Product title.")
    brand: str = Field(default="", description="Brand name.")
    seller: str = Field(default="", description="Seller/shop name for the displayed offer.")
    price_rub: float | None = Field(
        default=None,
        description="Everyday price in roubles — what a buyer without a subscription pays. None when absent, never 0.",
    )
    price_with_plus: float | None = Field(
        default=None,
        description="Discounted price requiring a Yandex Plus/Pay subscription. Typically 25-30% below price_rub.",
    )
    price_old_rub: float | None = Field(default=None, description="Struck-through reference price, when shown.")
    currency: str = Field(default="RUR", description="Currency code as reported upstream.")
    rating: float | None = Field(default=None, description="Average rating, 1..5.")
    rating_count: int | None = Field(
        default=None,
        description="Number of star ratings — not the number of written reviews, which is usually far smaller.",
    )
    in_stock: bool | None = Field(default=None, description="Whether the offer is in stock, when reported.")
    is_express: bool = Field(default=False, description="Whether express delivery is offered.")
    url: str = Field(default="", description="Canonical product page URL.")
    image: str = Field(default="", description="Primary product image URL.")


class YandexSearchResponse(BaseModel):
    query: str = Field(default="", description="The search query, echoed back from the page.")
    page: int | None = Field(default=None, description="Page number this response covers.")
    page_count: int | None = Field(default=None, description="Total pages available upstream.")
    total_available: int | None = Field(default=None, description="Total matches upstream reports.")
    has_next_page: bool = Field(default=False, description="Whether a further page exists.")
    returned: int = Field(default=0, description="Number of products in this response.")
    items: list[YandexProduct] = Field(default_factory=list, description="Products on this page, in display order.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class YandexReview(BaseModel):
    """One buyer review, as server-rendered on a product page."""

    author: str = Field(default="", description="Reviewer display name.")
    rating: int | None = Field(default=None, description="Star rating the reviewer gave, 1..5.")
    date: str = Field(default="", description="Human-readable date as displayed (e.g. '20 июля').")
    pros: str = Field(default="", description="What the reviewer liked.")
    cons: str = Field(default="", description="What the reviewer disliked.")
    comment: str = Field(default="", description="Free-form comment body.")
    votes_up: int = Field(default=0, description="How many readers found the review helpful.")
    votes_down: int = Field(default=0, description="How many readers found it unhelpful.")
    photos: list[str] = Field(default_factory=list, description="Reviewer-uploaded photo URLs.")


class YandexCardResponse(BaseModel):
    """Full detail for one product, including its rating breakdown and reviews."""

    product_id: str = Field(default="", description="Yandex Market product id.")
    sku_id: str = Field(default="", description="SKU id of the default offer.")
    title: str = Field(default="", description="Product title.")
    brand: str = Field(default="", description="Brand name.")
    seller: str = Field(default="", description="Seller of the default offer.")
    description: str = Field(default="", description="Product description.")
    image: str = Field(default="", description="Primary product image URL.")
    price_rub: float | None = Field(default=None, description="Everyday price in roubles, without a subscription.")
    price_with_plus: float | None = Field(default=None, description="Price requiring a Yandex Plus/Pay subscription.")
    price_before_discount_rub: float | None = Field(default=None, description="Pre-discount reference price.")
    discount_percent: int | None = Field(default=None, description="Discount percentage as reported upstream.")
    currency: str = Field(default="RUR", description="Currency code.")
    offers_count: int | None = Field(default=None, description="How many competing offers exist for this product.")
    rating: float | None = Field(default=None, description="Average rating, 1..5.")
    rating_count: int | None = Field(default=None, description="Number of star ratings.")
    review_count: int | None = Field(default=None, description="Number of written reviews.")
    rating_stars: dict[int, int] = Field(
        default_factory=dict,
        description="Ratings per star level, 1..5 — reveals whether a 4.8 hides a cluster of 1-star complaints.",
    )
    reviews: list[YandexReview] = Field(
        default_factory=list,
        description="Server-rendered reviews (first ~13 only; the rest load over a closed API).",
    )
    url: str = Field(default="", description="Canonical product page URL.")
    meta: MetaOut = Field(default_factory=MetaOut, description="Validation metadata.")


class YandexSelfcheckEntry(BaseModel):
    state: str = Field(default="", description="Sub-check verdict: healthy, drift, or inconclusive.")
    detail: str = Field(default="", description="What was observed.")
    notes: list[str] = Field(default_factory=list, description="Diagnostic notes.")


class YandexSelfcheckResponse(BaseModel):
    status: str = Field(default="", description="Overall verdict: success, drift_detected, or inconclusive.")
    connector: str = Field(default="yandex", description="Connector name.")
    checks: dict[str, YandexSelfcheckEntry] = Field(default_factory=dict, description="Per-page-type results.")
    server_version: str = Field(default="", description="Connector version.")
    server_started_at: str = Field(default="", description="Server start timestamp (UTC ISO-8601).")
    process_id: int = Field(default=0, description="OS process id.")
    config_loaded: bool = Field(default=False, description="Whether settings loaded successfully.")
    tool_count: int = Field(default=0, description="Number of registered MCP tools.")
    cache_stats: dict[str, Any] = Field(default_factory=dict, description="TTL cache counters for this process.")
