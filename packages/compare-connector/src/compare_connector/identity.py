"""Conservative product and offer identity helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
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
    return "".join(ch for ch in _text(value).upper() if ch.isalnum())


def _text(value: Any) -> str:
    # Do not turn a list, dict, boolean or float into an identifier. In
    # particular, 400638133393.1 must never become a valid GTIN after cleanup.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return unicodedata.normalize("NFC", str(value).strip())


def normalize_mpn(value: Any) -> str:
    return normalize_identifier(value)


def normalize_gtin(value: Any) -> str:
    """Return a valid GTIN-8/12/13/14, or empty for missing/invalid input."""
    raw = re.sub(r"\s+", "", _text(value))
    if not re.fullmatch(r"[0-9]+", raw) or len(raw) not in (8, 12, 13, 14) or set(raw) == {"0"}:
        return ""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(raw[:-1])))
    return raw if (10 - total % 10) % 10 == int(raw[-1]) else ""


def normalize_model(value: Any) -> str:
    return " ".join(re.findall(r"[^\W_]+", _text(value).casefold()))


def _field(raw: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _text(raw.get(name))
        if value:
            return value
    return ""


def identity_from_mapping(raw: Mapping[str, Any], *, source: str = "") -> ProductIdentity:
    """Build identity evidence from typed upstream fields only.

    Free-form titles are intentionally excluded: a model-like substring in a
    seller title is not manufacturer evidence. Invalid GTINs are discarded by
    ``normalize_gtin`` and therefore cannot create an exact match.
    """
    brand = _field(raw, "brand", "brand_name")
    model = _field(raw, "model", "model_name")
    # Marketplace SKU / vendor_code / article are seller identifiers, not MPN.
    mpn = _field(raw, "mpn", "manufacturer_part_number")
    gtin = _field(raw, "gtin", "barcode", "ean")
    variants: dict[str, str] = {}
    for key in ("color", "colour", "size", "storage", "memory", "capacity"):
        value = _text(raw.get(key))
        if value:
            variants["color" if key == "colour" else key] = value
    return ProductIdentity(
        brand=str(brand).strip(),
        model=str(model).strip(),
        mpn=normalize_mpn(mpn),
        gtin=normalize_gtin(gtin),
        variant_attributes=variants,
        native_product_id=_field(raw, "native_product_id", "product_id", "nm_id", "sku", "item_id", "id"),
        source=source,
    )


def _variants(values: dict[str, str]) -> dict[str, str]:
    return {("color" if k == "colour" else k): normalize_model(v) for k, v in values.items()}


def match_product_identity(expected: ProductIdentity, observed: ProductIdentity) -> IdentityMatch:
    """Match identifiers first, then conservatively use model/variant evidence."""
    left, right = _variants(expected.variant_attributes), _variants(observed.variant_attributes)
    if any(left[k] and right[k] and left[k] != right[k] for k in left.keys() & right.keys()):
        return IdentityMatch(status="mismatch", reasons=["variant_mismatch"])
    exp_brand, got_brand = normalize_model(expected.brand), normalize_model(observed.brand)
    if exp_brand and got_brand and exp_brand != got_brand:
        return IdentityMatch(status="mismatch", reasons=["brand_mismatch"])
    exp_gtin, got_gtin = normalize_gtin(expected.gtin), normalize_gtin(observed.gtin)
    exp_mpn, got_mpn = normalize_mpn(expected.mpn), normalize_mpn(observed.mpn)
    if exp_gtin and got_gtin and exp_gtin.zfill(14) != got_gtin.zfill(14):
        return IdentityMatch(status="mismatch", reasons=["gtin_mismatch"])
    if exp_mpn and got_mpn and exp_mpn != got_mpn:
        return IdentityMatch(status="mismatch", reasons=["mpn_mismatch"])
    if (expected.gtin and not exp_gtin) or (observed.gtin and not got_gtin):
        return IdentityMatch(reasons=["invalid_gtin"])
    if left.keys() != right.keys() or any(not v for v in (*left.values(), *right.values())):
        return IdentityMatch(reasons=["incomplete_variant_evidence"])
    if exp_gtin and got_gtin:
        return IdentityMatch(status="exact", score=1.0, reasons=["gtin_match"])
    if exp_mpn and got_mpn:
        if exp_brand and got_brand:
            return IdentityMatch(status="exact", score=0.98, reasons=["mpn_and_brand_match"])
        return IdentityMatch(reasons=["mpn_requires_brand"])
    em, om = normalize_model(expected.model), normalize_model(observed.model)
    if em and om and em == om:
        return IdentityMatch(status="likely", score=0.75, reasons=["model_match_without_manufacturer_id"])
    if not any((exp_gtin, got_gtin, exp_mpn, got_mpn, em, om)):
        return IdentityMatch(reasons=["insufficient_evidence"])
    return IdentityMatch(reasons=["insufficient_matching_evidence"])
