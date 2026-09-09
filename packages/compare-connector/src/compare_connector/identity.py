"""Conservative product and offer identity helpers."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class ProductIdentity(BaseModel):
    """Product-family/variant evidence, independent of a seller offer."""

    brand: str = ""
    model: str = ""
    mpn: str = ""
    gtin: str = ""
    variant_attributes: dict[str, str] = Field(default_factory=dict)
    native_product_id: str = ""
    source: str = ""


class OfferEvidence(BaseModel):
    """Provenance for a time-varying marketplace offer."""

    source: str = ""
    route: str = ""
    observed_at: str = ""
    native_id: str = ""
    identity: ProductIdentity = Field(default_factory=ProductIdentity)
    condition: str = ""
    availability: str = ""
    currency: str = "rub"
    untrusted_text: str = ""


class IdentityMatch(BaseModel):
    status: str = "unknown"  # exact, likely, mismatch, unknown
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


def normalize_identifier(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_mpn(value: Any) -> str:
    return normalize_identifier(value)


def normalize_gtin(value: Any) -> str:
    """Return a valid GTIN-8/12/13/14, or empty for missing/invalid input."""
    raw = normalize_identifier(value)
    if not raw.isdigit() or len(raw) not in (8, 12, 13, 14):
        return ""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(raw[:-1])))
    return raw if (10 - total % 10) % 10 == int(raw[-1]) else ""


def normalize_model(value: Any) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", str(value or "").upper()))


def _variants_equal(left: dict[str, str], right: dict[str, str]) -> bool:
    if not left or not right:
        return True
    keys = set(left) & set(right)
    return bool(keys) and all(normalize_model(left[k]) == normalize_model(right[k]) for k in keys)


def match_product_identity(expected: ProductIdentity, observed: ProductIdentity) -> IdentityMatch:
    """Match identifiers first, then conservatively use model/variant evidence."""
    exp_gtin, got_gtin = normalize_gtin(expected.gtin), normalize_gtin(observed.gtin)
    if exp_gtin and got_gtin:
        if exp_gtin != got_gtin:
            return IdentityMatch(status="mismatch", reasons=["gtin_mismatch"])
        if _variants_equal(expected.variant_attributes, observed.variant_attributes):
            return IdentityMatch(status="exact", score=1.0, reasons=["gtin_match"])
        return IdentityMatch(status="mismatch", reasons=["variant_mismatch"])
    exp_mpn, got_mpn = normalize_mpn(expected.mpn), normalize_mpn(observed.mpn)
    if exp_mpn and got_mpn:
        if exp_mpn != got_mpn:
            return IdentityMatch(status="mismatch", reasons=["mpn_mismatch"])
        if expected.brand and observed.brand and normalize_model(expected.brand) != normalize_model(observed.brand):
            return IdentityMatch(status="mismatch", reasons=["brand_mismatch"])
        if not _variants_equal(expected.variant_attributes, observed.variant_attributes):
            return IdentityMatch(status="mismatch", reasons=["variant_mismatch"])
        return IdentityMatch(status="exact", score=0.98, reasons=["mpn_match"])
    em, om = normalize_model(expected.model), normalize_model(observed.model)
    if em and om and em == om and _variants_equal(expected.variant_attributes, observed.variant_attributes):
        return IdentityMatch(status="likely", score=0.75, reasons=["model_match_without_manufacturer_id"])
    if not any((exp_gtin, got_gtin, exp_mpn, got_mpn, em, om)):
        return IdentityMatch(reasons=["insufficient_evidence"])
    return IdentityMatch(reasons=["insufficient_matching_evidence"])
