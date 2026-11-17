"""Conservative product and offer identity helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
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


# Storage and memory sizes as marketplace titles write them: "128GB", "128 ГБ",
# "1TB", "1 ТБ". Only a number attached to a gigabyte/terabyte unit counts: a
# bare "128" is a model number far more often than a capacity, and a fractional
# "1.5 ТБ" is not a size this extractor can state without guessing. Tokens keep
# the unit the title used; only the comparison converts. A terabyte reads as
# 1024 GB (phones, laptops) or 1000 GB (SSDs: DNS titles the Samsung 990 PRO 1TB
# "1000 ГБ", A7 benchmark 2026-11-17), so "1 ТБ" agrees with both.
_SIZE_TOKEN = re.compile(r"(?<!\w)(?<!\d[.,])([0-9]{1,4})\s*(gb|гб|tb|тб)(?!\w)", re.IGNORECASE)
_SIZE_UNITS = {"gb": "GB", "гб": "GB", "tb": "TB", "тб": "TB"}


def extract_size_tokens(value: Any) -> frozenset[str]:
    """Canonical sizes named in free-form text, e.g. ``"64 ГБ"`` -> ``"64GB"``.

    Unit spelling, case and spacing are normalised; a value with no unit, a
    fractional value and a unit outside GB/TB (megabytes never name the variant
    of a phone or a laptop) all yield nothing rather than a guess.
    """
    return frozenset(f"{int(number)}{_SIZE_UNITS[unit.lower()]}" for number, unit in _SIZE_TOKEN.findall(_text(value)))


def _size_in_gb(token: str) -> int:
    return int(token[:-2]) * (1024 if token.endswith("TB") else 1)


def _size_readings(token: str) -> set[int]:
    """Gigabyte readings of one size: a terabyte is sold as 1024 GB (phones) or 1000 GB (SSDs)."""
    number = int(token[:-2])
    return {number * 1024, number * 1000} if token.endswith("TB") else {number}


def sizes_conflict(asked: frozenset[str], stated: frozenset[str]) -> bool:
    """Whether sizes named in a title contradict the sizes a query asks for.

    ``match_product_identity``'s rule, applied to sizes: only positive evidence
    on both sides can disagree. A query that states no size, a title that names
    none, and a title naming one of the asked sizes are all unknown, never a
    contradiction — the same reason a missing variant attribute abstains.
    Sizes are compared in gigabytes, so ``"1TB"`` agrees with ``"1024GB"`` and ``"1000GB"``.
    """
    if not (asked and stated):
        return False
    asked_gb = {reading for token in asked for reading in _size_readings(token)}
    return not (asked_gb & {reading for token in stated for reading in _size_readings(token)})


def format_size_tokens(sizes: Iterable[str]) -> str:
    """Canonical sizes in capacity order, e.g. ``"64GB/256GB/1TB"``."""
    return "/".join(sorted(sizes, key=lambda token: (_size_in_gb(token), token)))


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


# START_BLOCK_MODEL_TOKENS
# R-COMPARE-MODEL-TOKENS. The Latin words and numbers a query names (brand, series,
# model number) are the one identity evidence every search title carries. On the A7
# benchmark (cycle 1, 2026-11-17) cheapest_comparable without them named another
# product on 8 of 8 queries: a Dyson battery, Sony W919 earbuds, an iPhone 14, LEGO
# gears. Cyrillic words stay out (they name the category, "наушники", and are spelt
# many ways), as do sizes (sizes_conflict owns them) and the generic words below.
_WORD_EDGE = "0-9a-zа-яё"
_LATIN_TOKEN = re.compile(rf"(?<![{_WORD_EDGE}])[0-9a-z]+(?:-[0-9a-z]+)*(?![{_WORD_EDGE}])")
_RUNS = re.compile(r"[a-z]+|[0-9]+")
# One spelling per product line: titles write "PS5", "PS 5" and "PlayStation 5".
_ALIASES = ((re.compile(rf"(?<![{_WORD_EDGE}])(?:playstation|ps)[\s-]?5(?![{_WORD_EDGE}])"), "ps5"),)
# Words that name the kind of goods, not the model: the DNS title of the 990 PRO says
# "NVMe накопитель", not "SSD".
_GENERIC_WORDS = frozenset({"ssd", "hdd", "nvme"})
# A suffix right after a model word names a different model ("iPhone 16 Pro", "PS5 Pro").
_MODEL_SUFFIXES = frozenset({"pro", "max", "plus", "ultra", "mini", "lite", "fe", "edge"})
_FOR_WORD = re.compile(rf"(?<![{_WORD_EDGE}])(?:для|for)\s+(\S+)(?:\s+(\S+))?")
_NEXT_WORD = re.compile(r"[\s-]*([0-9a-z]+)")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    for pattern, spelling in _ALIASES:
        folded = pattern.sub(spelling, folded)
    return folded


def _model_tokens(query: str) -> list[str]:
    tokens = _LATIN_TOKEN.findall(_SIZE_TOKEN.sub(" ", _fold(query)))
    return [token for token in dict.fromkeys(tokens) if token not in _GENERIC_WORDS]


def _token_pattern(token: str) -> re.Pattern[str]:
    """A token as a whole word whose letter and digit runs may be split by a space or hyphen (WH-1000XM5)."""
    return re.compile(rf"(?<![{_WORD_EDGE}])" + r"[\s-]?".join(_RUNS.findall(token)) + rf"(?![{_WORD_EDGE}])")


def _is_code(token: str) -> bool:
    """A manufacturer code strong enough to stand without the brand: 42151, WH-1000XM5."""
    plain = token.replace("-", "")
    return (plain.isdigit() and len(plain) >= 4) or (not plain.isdigit() and not plain.isalpha() and len(plain) >= 5)


# START_CONTRACT: model_mismatch
# PURPOSE: Say why a search title is not the model the query names, or None when nothing contradicts it.
# INPUTS: query and title, free text; titles are untrusted marketplace data.
# OUTPUTS: None for a query with no Latin model token (the rule abstains) or a title that names them all;
#          otherwise a short reason: "missing <token>", "for <token>" (an accessory for the device) or
#          "<token> <suffix>" (another model, e.g. "16 pro").
# RULES: every token must appear as a whole word, except that pure-letter words (brand, series) may be
#        absent when a code token (_is_code) is present; "для"/"for" followed within two words by a non-number
#        token marks an accessory unless the query says "для"/"for" too; a _MODEL_SUFFIXES word right after a
#        token is another model unless the query names it.
# SIDE_EFFECTS: None.
def model_mismatch(query: str, title: str) -> str | None:
    tokens = _model_tokens(query)
    if not tokens:
        return None
    text, asked = _fold(title), _fold(query)
    patterns = {token: _token_pattern(token) for token in tokens}
    found = [token for token in tokens if patterns[token].search(text)]
    has_code = any(_is_code(token) for token in found)
    for token in tokens:
        if token not in found and not (has_code and token.isalpha()):
            return f"missing {token}"
    if not _FOR_WORD.search(asked):
        for match in _FOR_WORD.finditer(text):
            window = " ".join(word for word in match.groups() if word)
            for token in found:
                if not token.isdigit() and patterns[token].search(window):
                    return f"for {token}"
    asked_words = set(_LATIN_TOKEN.findall(asked))
    for token in found:
        for match in patterns[token].finditer(text):
            suffix = _NEXT_WORD.match(text, match.end())
            if suffix and suffix.group(1) in _MODEL_SUFFIXES and suffix.group(1) not in asked_words:
                return f"{token} {suffix.group(1)}"
    return None


# END_CONTRACT: model_mismatch
# END_BLOCK_MODEL_TOKENS
