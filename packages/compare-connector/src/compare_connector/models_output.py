"""Typed responses for cross-marketplace comparison.

The schema is built around one honesty requirement: a comparison that silently
omits a marketplace is worse than one that says it failed. Hence per-source
outcomes alongside the merged offer list, and a ``complete`` flag the caller can
check before drawing conclusions.
"""
# START_MODULE_CONTRACT
# PURPOSE: Describe comparison offers, everyday-price ranking and per-source outcomes.
# SCOPE: Additive typed outputs; preserve native conditional prices without ranking them.
# DEPENDS: Pydantic; compare_connector.identity.
# LINKS: M-COMPARE; R-COMPARE-CONDITIONAL-PRICE; R-COMPARE-VARIANT-IDENTITY; R-NO-FABRICATION; R-TOOL-SCHEMA;
#        R-CDP-HOST-PAUSE; R-CHALLENGE-ACTION.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# MarketOffer: Carry everyday/native prices and optional source price kind.
# SourceOutcome: Report source access, diagnostics and recovery signals.
# CompareResponse: Return eligible winners, all offers and warnings.
# END_MODULE_MAP

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from compare_connector.identity import OfferEvidence, ProductIdentity


# START_BLOCK_OUTPUT_MODELS
class MarketOffer(BaseModel):
    """One offer, normalised across marketplaces so prices are comparable."""

    source: str = Field(default="", description="Marketplace the offer came from (wildberries, yandex_market, ozon).")
    product_id: str = Field(default="", description="Marketplace-native product id, for a follow-up card lookup.")
    variant_id: str = Field(default="", description="Sellable variant/SKU id when the source exposes one separately.")
    title: str = Field(default="", description="Product title as the marketplace names it.")
    brand: str = Field(default="", description="Brand name, when reported.")
    seller: str = Field(default="", description="Seller/shop name, when reported.")
    price_rub: float | None = Field(
        default=None,
        description=(
            "Everyday price in roubles — the only basis for ranking. None when absent, never 0, "
            "and always None for a marketplace that quotes in another currency."
        ),
    )
    price_kind: str | None = Field(
        default=None,
        description=(
            "Kind of price_native: 'regular', 'wallet', 'subscription', or another source-reported condition. "
            "None when unreported. Conditional amounts never rank; price_rub stays None without an everyday price."
        ),
    )
    currency: str = Field(
        default="rub",
        description="Currency of price_native, lowercase ISO code. Only 'rub' offers enter the ranking.",
    )
    price_native: float | None = Field(
        default=None,
        description=(
            "Price as the marketplace quotes it, in `currency`. Mirrors an available everyday price_rub; "
            "also preserves conditional-only amounts when price_rub is None. For Taobao it carries the yuan price."
        ),
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
    identity: ProductIdentity = Field(
        default_factory=ProductIdentity,
        description="Manufacturer and variant identity evidence; empty means identity is unknown.",
    )
    evidence: OfferEvidence | None = Field(
        default=None,
        description="Optional provenance for the native offer and its identity evidence.",
    )

    @model_validator(mode="after")
    def _mirror_rouble_price_into_native(self) -> MarketOffer:
        """Mirror everyday RUB prices only when no explicit native price is supplied.

        Conditional-only quotes supplied by an adapter remain in price_native
        without inventing an everyday price_rub.
        """
        if self.price_native is None and self.currency == "rub":
            self.price_native = self.price_rub
        return self


class SourceOutcome(BaseModel):
    """What happened when one marketplace was queried.

    Reported for every source, successful or not — that is what lets a caller
    tell "cheapest overall" from "cheapest among the two that answered".
    """

    source: str = Field(default="", description="Marketplace name.")
    status: str = Field(
        default="",
        description="ok, blocked (anti-bot or rate limit), timeout, error, unhealthy, or not_installed.",
    )
    detail: str = Field(default="", description="Human-readable outcome detail; the error text when it failed.")
    warnings: list[str] = Field(
        default_factory=list, description="Native diagnostics; successful access can still return degraded data."
    )
    offers_returned: int = Field(default=0, description="How many offers this marketplace contributed.")
    elapsed_ms: int = Field(default=0, description="Round-trip time for this marketplace, in milliseconds.")
    error_code: str | None = Field(default=None, description="Structured connector error code, if known.")
    retryable: bool = Field(
        default=False, description="Retry may succeed; user action must be completed first if required."
    )
    retry_after_s: float | None = Field(
        default=None, description="Seconds to wait before retrying, when the source reports a pause."
    )
    retryable_now: bool | None = Field(
        default=None, description="False when a plain retry now cannot work; None when unreported."
    )
    retry_when: str | None = Field(default=None, description="When to call again, e.g. after_user_confirmation.")
    user_action: str | None = Field(default=None, description="Human step that unblocks this source, when named.")
    requires_user_action: bool = Field(
        default=False, description="Pause retries for this source until browser action completes."
    )
    challenge_type: str | None = Field(default=None, description="captcha, login, or login_or_captcha when known.")
    handoff_expires_at: str | None = Field(
        default=None,
        description="UTC expiry when the challenged tab is retained; repeat the same request after action.",
    )
    handoff_id: str | None = Field(
        default=None, description="Opaque same-session handle for an optional browser snapshot."
    )


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
    cheapest_comparable: MarketOffer | None = Field(
        default=None,
        description=(
            "Lowest everyday price among offers whose titles do not name an accessory, a used/display "
            "condition, or a storage/memory size contradicting the size the query states. "
            "Use this for a like-for-like recommendation; cheapest retains the raw lowest listing."
        ),
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


# END_BLOCK_OUTPUT_MODELS
