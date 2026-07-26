"""Typed responses for cross-marketplace comparison.

The schema is built around one honesty requirement: a comparison that silently
omits a marketplace is worse than one that says it failed. Hence per-source
outcomes alongside the merged offer list, and a ``complete`` flag the caller can
check before drawing conclusions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MarketOffer(BaseModel):
    """One offer, normalised across marketplaces so prices are comparable."""

    source: str = Field(default="", description="Marketplace the offer came from (wildberries, yandex_market, ozon).")
    product_id: str = Field(default="", description="Marketplace-native product id, for a follow-up card lookup.")
    title: str = Field(default="", description="Product title as the marketplace names it.")
    brand: str = Field(default="", description="Brand name, when reported.")
    seller: str = Field(default="", description="Seller/shop name, when reported.")
    price_rub: float | None = Field(
        default=None,
        description="Everyday price in roubles — the basis for ranking. None when absent, never 0.",
    )
    price_with_subscription_rub: float | None = Field(
        default=None,
        description=(
            "Lower price requiring a paid subscription (Yandex Plus). Excluded from ranking, "
            "since it is unavailable to non-subscribers."
        ),
    )
    rating: float | None = Field(default=None, description="Average rating, 1..5, when reported.")
    rating_count: int | None = Field(default=None, description="Number of ratings or reviews behind that average.")
    in_stock: bool | None = Field(default=None, description="Stock status, when the marketplace reports it.")
    url: str = Field(default="", description="Direct product URL.")


class SourceOutcome(BaseModel):
    """What happened when one marketplace was queried.

    Reported for every source, successful or not — that is what lets a caller
    tell "cheapest overall" from "cheapest among the two that answered".
    """

    source: str = Field(default="", description="Marketplace name.")
    status: str = Field(
        default="",
        description="ok, blocked (anti-bot or rate limit), timeout, error, or not_installed.",
    )
    detail: str = Field(default="", description="Human-readable outcome detail; the error text when it failed.")
    offers_returned: int = Field(default=0, description="How many offers this marketplace contributed.")
    elapsed_ms: int = Field(default=0, description="Round-trip time for this marketplace, in milliseconds.")


class CompareResponse(BaseModel):
    """A ranked cross-marketplace price comparison with per-source provenance."""

    query: str = Field(default="", description="The query that was priced.")
    sources_queried: list[str] = Field(default_factory=list, description="Marketplaces that were attempted.")
    sources_ok: list[str] = Field(default_factory=list, description="Marketplaces that answered successfully.")
    complete: bool = Field(
        default=False,
        description="True only when every queried marketplace answered. False means the ranking is partial.",
    )
    total_offers: int = Field(default=0, description="Total offers across all marketplaces.")
    cheapest: MarketOffer | None = Field(
        default=None,
        description="Lowest everyday price found. None when no marketplace returned a price.",
    )
    price_spread_rub: float | None = Field(
        default=None,
        description="Difference between the highest and lowest everyday price — how much the choice is worth.",
    )
    offers: list[MarketOffer] = Field(
        default_factory=list,
        description="All offers, cheapest first. Offers without a price are kept at the end.",
    )
    source_outcomes: list[SourceOutcome] = Field(
        default_factory=list,
        description="Per-marketplace outcome, including failures — read this before trusting the ranking.",
    )
    warnings: list[str] = Field(default_factory=list, description="Connector-level warnings (partial data, no prices).")
    server_version: str = Field(default="", description="Connector version.")
