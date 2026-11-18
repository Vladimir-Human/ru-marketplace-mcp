"""Conservative product and offer identity helpers."""
# START_MODULE_CONTRACT
# PURPOSE: Extract and compare product identifiers, capacity tokens, size/model lists and query model tokens.
# SCOPE: Pure text/identifier helpers with no I/O, network or browser access.
# DEPENDS: Pydantic; stdlib re, unicodedata, collections.abc, typing.
# LINKS: M-COMPARE; M-COMPARE-DECIDE; R-COMPARE-VARIANT-IDENTITY; R-COMPARE-SIZE-LIST; R-COMPARE-MODEL-LIST;
#        R-COMPARE-MODEL-TOKENS; R-COMPARE-DEEP-TRAPS.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# ProductIdentity/OfferEvidence/IdentityMatch: Typed product and offer identity models.
# extract_size_tokens/sizes_conflict/lists_sizes: Capacity extraction, conflict checks and multi-size list detection.
# lists_models: Multi-model family and compact slash list detection.
# match_product_identity: Conservative GTIN/MPN/model/variant matching.
# model_mismatch: Query model token, suffix, sub-brand, decimal and accessory-window checks.
# END_MODULE_MAP

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
_RAM_ROM_PAIR = re.compile(r"(?<!\w)(?<!\d[.,])(\d{1,2})\s*/\s*(\d{2,4})\s*(gb|гб|tb|тб)(?!\w)", re.IGNORECASE)
_SIZE_UNITS = {"gb": "GB", "гб": "GB", "tb": "TB", "тб": "TB"}
_RAM_RATIO = 4


def extract_size_tokens(value: Any) -> frozenset[str]:
    """Canonical sizes named in free-form text, e.g. ``"64 ГБ"`` -> ``"64GB"``.

    Unit spelling, case and spacing are normalised; a RAM+storage slash pair
    (``"12/256 ГБ"`` where ``rom >= 4 * ram``) yields both sizes so RAM+storage
    queries catch wrong-RAM variants; a bare number, a fractional value and a
    unit outside GB/TB yield nothing rather than a guess.
    """
    text = _text(value)
    tokens = {f"{int(number)}{_SIZE_UNITS[unit.lower()]}" for number, unit in _SIZE_TOKEN.findall(text)}
    for ram_s, rom_s, unit in _RAM_ROM_PAIR.findall(text):
        ram_n, rom_n = int(ram_s), int(rom_s)
        if ram_n > 0 and rom_n >= _RAM_RATIO * ram_n:
            tokens.add(f"{ram_n}{_SIZE_UNITS[unit.lower()]}")
    return frozenset(tokens)


def _size_in_gb(token: str) -> int:
    return int(token[:-2]) * (1024 if token.endswith("TB") else 1)


def _size_readings(token: str) -> set[int]:
    """Gigabyte readings of one size: a terabyte is sold as 1024 GB (phones) or 1000 GB (SSDs)."""
    number = int(token[:-2])
    return {number * 1024, number * 1000} if token.endswith("TB") else {number}


def _distinct_sizes(tokens: Iterable[str]) -> list[set[int]]:
    distinct: list[set[int]] = []
    for token in tokens:
        readings = _size_readings(token)
        if not any(readings & seen for seen in distinct):
            distinct.append(readings)
    return distinct


def sizes_conflict(asked: frozenset[str], stated: frozenset[str]) -> bool:
    """Whether sizes named in a title contradict the sizes a query asks for.

    ``match_product_identity``'s rule, applied to sizes: only positive evidence
    on both sides can disagree. A query that states no size, a title that names
    none, and a title naming one of the asked sizes are all unknown, never a
    contradiction — the same reason a missing variant attribute abstains.
    When a RAM + storage query names two or more sizes and the title also states
    two or more sizes, every asked size must appear in the title's stated sizes.
    Sizes are compared in gigabytes, so ``"1TB"`` agrees with ``"1024GB"`` and ``"1000GB"``.
    """
    if not (asked and stated):
        return False
    stated_gb = {reading for token in stated for reading in _size_readings(token)}
    asked_distinct = _distinct_sizes(asked)
    stated_distinct = _distinct_sizes(stated)
    if len(asked_distinct) >= 2 and len(stated_distinct) >= 2:
        mins = sorted(min(readings) for readings in asked_distinct)
        if mins[-1] >= _RAM_RATIO * mins[0]:
            return not all(readings & stated_gb for readings in asked_distinct)
    asked_gb = {reading for readings in asked_distinct for reading in readings}
    return not (asked_gb & stated_gb)


# START_BLOCK_SIZE_LIST
# A title listing several capacities ("128GB/256GB/512GB", "64 / 128 / 256 ГБ",
# "6/128ГБ, 6/256ГБ", "8/128, 8/256 ГБ", "128-256 ГБ") sells several variants under
# one price that belongs to a variant it does not name (R-COMPARE-SIZE-LIST).
# A single RAM + storage pair ("8/256GB", "16 ГБ / 512 ГБ") is one variant: two sizes,
# the larger at least four times the smaller.
_SIZE_RUN = re.compile(r"(?<!\w)(?<!\d[.,])(\d{1,4})((?:\s*/\s*\d{1,4})+)\s*(gb|гб|tb|тб)(?!\w)", re.IGNORECASE)
_SIZE_RANGE = re.compile(
    r"(?<!\w)(?<!\d[.,])(\d{1,4})(?:\s*(gb|гб|tb|тб))?[-–](\d{1,4})\s*(gb|гб|tb|тб)(?!\w)",
    re.IGNORECASE,
)
_PAIR_SIZE_LIST = re.compile(
    r"(?<!\w)\d{1,2}\s*/\s*\d{2,4}(?:\s*(?:gb|гб|tb|тб))?"
    r"(?:\s*(?:[,|/]|или|or)\s*\d{1,2}\s*/\s*\d{2,4}(?:\s*(?:gb|гб|tb|тб))?)+\s*(?!\w)",
    re.IGNORECASE,
)
_LIST_GAP = re.compile(r"\s*(?:[/|,]|или|or)\s*(?:\d{1,3}\s*/\s*)?", re.IGNORECASE)


def _size_lists(text: str) -> list[list[str]]:
    """Runs of two or more sizes joined by a slash, bar, comma, range or "или"/"or"."""
    lists = []
    for match in _SIZE_RUN.finditer(text):
        unit = _SIZE_UNITS[match[3].lower()]
        lists.append([f"{int(number)}{unit}" for number in (match[1], *re.findall(r"\d{1,4}", match[2]))])
    for match in _SIZE_RANGE.finditer(text):
        first_unit = _SIZE_UNITS[(match[2] or match[4]).lower()]
        second_unit = _SIZE_UNITS[match[4].lower()]
        lists.append([f"{int(match[1])}{first_unit}", f"{int(match[3])}{second_unit}"])
    run: list[str] = []
    end = None
    for match in _SIZE_TOKEN.finditer(text):
        token = f"{int(match[1])}{_SIZE_UNITS[match[2].lower()]}"
        if end is not None and _LIST_GAP.fullmatch(text[end : match.start()]):
            run.append(token)
        else:
            lists.append(run)
            run = [token]
        end = match.end()
    lists.append(run)
    return [sizes for sizes in lists if len(sizes) > 1]


def lists_sizes(title: Any) -> bool:
    """Whether a title lists several capacities, so its price names no one variant.

    Sizes compare in gigabytes ("1TB / 1000GB" is one size). Explicit hyphen/en-dash
    ranges ("128–512 ГБ", "64-256 ГБ") always name a multi-capacity range; two slash-
    or separator-joined sizes are a RAM + storage pair when the larger is at least
    four times the smaller.
    """
    raw = _text(title)
    for match in _SIZE_RANGE.finditer(raw):
        first_unit = _SIZE_UNITS[(match[2] or match[4]).lower()]
        second_unit = _SIZE_UNITS[match[4].lower()]
        if len(_distinct_sizes((f"{int(match[1])}{first_unit}", f"{int(match[3])}{second_unit}"))) >= 2:
            return True
    for match in _PAIR_SIZE_LIST.finditer(raw):
        chunk = match[0]
        if re.search(r"(?:gb|гб|tb|тб)", chunk, re.IGNORECASE):
            pairs = {(int(ram), int(rom)) for ram, rom in re.findall(r"(\d{1,2})\s*/\s*(\d{2,4})", chunk)}
            if len(pairs) > 1:
                return True
    for sizes in _size_lists(raw):
        distinct = _distinct_sizes(sizes)
        if len(distinct) > 2:
            return True
        if len(distinct) == 2:
            small, large = sorted(min(readings) for readings in distinct)
            if large < _RAM_RATIO * small:
                return True
    return False


# END_BLOCK_SIZE_LIST


# START_BLOCK_MODEL_LIST
# A title naming several models of one family ("Dyson V11/V15", "iPhone 15 / iPhone 16",
# "Galaxy S24 / S25", "iPhone 15/16", "Galaxy S24/25", "Redmi Note 13/14") sells or fits
# several products under one price (R-COMPARE-MODEL-LIST).
_MODEL_GAP = r"\s*(?:/|\||,|\bили\b|\bor\b)\s*"
_SAME_WORD_MODELS = re.compile(
    r"(?<![\w-])([a-zа-яё]{2,})\s*(\d{1,4}[a-zа-яё]{0,2})\b" + _MODEL_GAP + r"\1\s*(\d{1,4}[a-zа-яё]{0,2})\b",
    re.IGNORECASE,
)
_SAME_PREFIX_MODELS = re.compile(r"(?<![\w-])([a-z]{1,4})(\d{1,5})\b" + _MODEL_GAP + r"\1(\d{1,5})\b", re.IGNORECASE)
_SHARED_WORD_MODELS = re.compile(r"(?<![\w-])([a-z]{2,})\s+(\d{1,4})\s*/\s*(\d{1,4})(?![\w.,-])", re.IGNORECASE)
_SHARED_PREFIX_MODELS = re.compile(r"(?<![\w-])([a-z]{1,4})(\d{1,4})\s*/\s*(\d{1,4})(?![\w.,-])", re.IGNORECASE)
_SPEC_PREFIXES = frozenset({"ip", "usb", "ddr", "lpddr", "wifi", "bt", "pcie", "hdmi", "cat"})
_UNIT_AFTER = re.compile(
    r"\s*(?:gb|гб|tb|тб|mb|мб|мм|mm|см|cm|кг|kg|г|g|л|l|мл|ml|w|вт|mah|мач|гц|hz|шт|дюйм\w*|r\d{2}|%)(?!\w)",
    re.IGNORECASE,
)


def lists_models(title: Any) -> bool:
    """Whether a title names several models of one family, so its price names no one product."""
    text = _text(title)
    for pattern in (_SAME_WORD_MODELS, _SAME_PREFIX_MODELS):
        for match in pattern.finditer(text):
            family, first, second = match[1], match[2], match[3]
            if family.lower() in _SPEC_PREFIXES or first.lower() == second.lower():
                continue
            if _UNIT_AFTER.match(text, match.end()):
                continue
            return True
    for pattern in (_SHARED_WORD_MODELS, _SHARED_PREFIX_MODELS):
        for match in pattern.finditer(text):
            family, first, second = match[1], match[2], match[3]
            if family.lower() in _SPEC_PREFIXES:
                continue
            n1, n2 = int(first), int(second)
            if n1 == n2 or n1 == 0 or n2 == 0 or max(n1, n2) >= _RAM_RATIO * min(n1, n2):
                continue
            if len(first) != len(second) and {len(first), len(second)} != {1, 2}:
                continue
            if _UNIT_AFTER.match(text, match.end()):
                continue
            return True
    return False


# END_BLOCK_MODEL_LIST


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
_DECIMAL_RUN = r"[0-9]+(?:[.,][0-9]+)+"
_LATIN_PART = rf"(?:[a-z]+|{_DECIMAL_RUN}|[0-9]+)+"
_LATIN_TOKEN = re.compile(rf"(?<![{_WORD_EDGE}]){_LATIN_PART}(?:[-]{_LATIN_PART})*(?![{_WORD_EDGE}])")
_RUNS = re.compile(rf"[a-z]+|{_DECIMAL_RUN}|[0-9]+")
# One spelling per product line: titles write "PS5", "PS 5" and "PlayStation 5", and "S25+" / "S25 Plus".
_ALIASES = (
    (re.compile(rf"(?<![{_WORD_EDGE}])(?:playstation|ps)[\s-]?5(?![{_WORD_EDGE}])"), "ps5"),
    (re.compile(rf"(?<=[0-9a-z])\+(?![{_WORD_EDGE}])"), " plus"),
    (re.compile(rf"(\d+)(?=(?:шт|л|мл|кг|г|м|см|мм)(?![{_WORD_EDGE}]))"), r"\1 "),
)
# Words that name the kind of goods or a sales claim, not the model: the DNS title of
# the 990 PRO says "NVMe накопитель", not "SSD"; Russian titles name the category in
# Russian when a query names it in English.
_GENERIC_WORDS = frozenset(
    {
        "ssd", "hdd", "nvme",
        "smartphone", "phone", "laptop", "notebook", "tablet", "console", "headphones", "earbuds",
        "vacuum", "cleaner", "monitor", "tv", "sneakers", "shoes", "tires", "tyres", "diapers",
        "original", "new", "global", "not", "unused",
    }
)  # fmt: skip
_SUFFIX_CANON = {
    "про": "pro",
    "макс": "max",
    "плюс": "plus",
    "ультра": "ultra",
    "мини": "mini",
    "лайт": "lite",
    "фе": "fe",
}
_SUBBRANDS = ("redmi", "poco", "note")
# A suffix right after a model word names a different model ("iPhone 16 Pro", "PS5 Pro", "Switch OLED", "S25+", "Watch SE").
_MODEL_SUFFIXES = frozenset(
    {
        "pro", "max", "plus", "ultra", "mini", "lite", "fe", "edge", "oled",
        "se", "air", "fold", "flip",
        *_SUBBRANDS,
        *_SUFFIX_CANON,
    }
)  # fmt: skip
_DIGIT_LETTER_SUFFIXES = frozenset({"e", "c", "s", "t", "a", "i"})
# "для", "под", "for", "совместим с", "compatible with" before a model word mark goods made for the device.
_FOR_WORD = re.compile(
    rf"(?<![{_WORD_EDGE}])(?:для|под|for|совместим[а-я]*\s+с|compatible\s+with)\s+(?=(?P<window>[^,;|(]*))"
)
_FOR_WINDOW_WORDS = 5
# A title that calls itself a copy is not the original the query names.
_COPY_WORDS = re.compile(rf"(?<![{_WORD_EDGE}])(?:аналог|копия|реплика|replica)(?![{_WORD_EDGE}])")
_COPY_NEGATION = re.compile(rf"(?<![{_WORD_EDGE}])(?:не|ни|not(?:\s+a)?)(?:\s+|-)$")
_NEXT_WORD = re.compile(r"[\s-]*([0-9a-zа-яё]+)")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    for pattern, spelling in _ALIASES:
        folded = pattern.sub(spelling, folded)
    return folded


_NEGATED_EN_QUALIFIER = re.compile(
    rf"(?<![{_WORD_EDGE}])not(?:\s+a)?[\s-]+(?:used|refurbished|renewed|pre-owned|replica|open[\s-]box)(?![{_WORD_EDGE}])"
)


def _strip_sizes(text: str) -> str:
    cleaned = _NEGATED_EN_QUALIFIER.sub(" ", text)
    cleaned = _RAM_ROM_PAIR.sub(
        lambda m: " " if int(m[1]) > 0 and int(m[2]) >= _RAM_RATIO * int(m[1]) else m[0],
        cleaned,
    )
    return _SIZE_TOKEN.sub(" ", cleaned)


def _model_tokens(query: str) -> list[str]:
    tokens = _LATIN_TOKEN.findall(_strip_sizes(_fold(query)))
    return [token for token in dict.fromkeys(tokens) if token not in _GENERIC_WORDS]


def _token_pattern(token: str) -> re.Pattern[str]:
    """A token as a whole word whose letter and digit runs may be split by a space or hyphen (WH-1000XM5)."""
    decimal_lead = r"(?<!\d[.,])" if token[0].isdigit() else ""
    decimal_tail = r"(?!\.\d)" if ("." in token or "," in token) else ""
    runs = [re.sub(r"[.,]", "[.,]", run) for run in _RUNS.findall(token)]
    return re.compile(
        rf"(?<![{_WORD_EDGE}]){decimal_lead}" + r"[\s-]?".join(runs) + rf"(?![{_WORD_EDGE}]){decimal_tail}"
    )


def _is_code(token: str) -> bool:
    """A manufacturer code strong enough to stand without the brand: 42151, WH-1000XM5, ECAM22.110."""
    plain = re.sub(r"[-.,]", "", token)
    if "." in token or "," in token:
        return not plain.isdigit() and not plain.isalpha() and len(plain) >= 5
    return (plain.isdigit() and len(plain) >= 4) or (not plain.isdigit() and not plain.isalpha() and len(plain) >= 5)


def _has_copy_word(text: str) -> bool:
    return any(not _COPY_NEGATION.search(text, 0, match.start()) for match in _COPY_WORDS.finditer(text))


# START_CONTRACT: model_mismatch
# PURPOSE: Say why a search title is not the model the query names, or None when nothing contradicts it.
# INPUTS: query and title, free text; brand, the offer's own brand field (Wildberries keeps it out of the
#         title); titles and brands are untrusted marketplace data.
# OUTPUTS: None for a query with no Latin model token (the rule abstains) or a title that names them all;
#          otherwise a short reason: "missing <token>", "for <token>" (goods made for the device), "copy",
#          "<token> <suffix>" (another model, e.g. "16 pro", "s25 plus", "16 e") or an unrequested sub-brand.
# SIDE_EFFECTS: None.
def model_mismatch(query: str, title: str, brand: str = "") -> str | None:
    tokens = _model_tokens(query)
    if not tokens:
        return None
    text, asked = _fold(title), _fold(query)
    patterns = {token: _token_pattern(token) for token in tokens}
    named = f"{text} {_fold(brand)}"
    found = [token for token in tokens if patterns[token].search(named)]
    has_code = any(_is_code(token) for token in found)
    for token in tokens:
        if token not in found and not (has_code and token.isalpha()):
            return f"missing {token}"
    asked_for_windows = [
        " ".join(match.group("window").split()[:_FOR_WINDOW_WORDS]) for match in _FOR_WORD.finditer(asked)
    ]
    asked_for_tokens = {token for token in found if any(patterns[token].search(window) for window in asked_for_windows)}
    for match in _FOR_WORD.finditer(text):
        window = " ".join(match.group("window").split()[:_FOR_WINDOW_WORDS])
        for token in found:
            if (
                token not in asked_for_tokens
                and (not token.isdigit() or _is_code(token))
                and patterns[token].search(window)
            ):
                return f"for {token}"
    if _has_copy_word(text) and not _has_copy_word(asked):
        return "copy"
    raw_asked_words = set(re.findall(rf"[{_WORD_EDGE}]+", asked))
    asked_words = raw_asked_words | {_SUFFIX_CANON[w] for w in raw_asked_words if w in _SUFFIX_CANON}
    for token in found:
        for match in patterns[token].finditer(text):
            suffix = _NEXT_WORD.match(text, match.end())
            if not suffix:
                continue
            word = suffix.group(1)
            canon = _SUFFIX_CANON.get(word, word)
            if canon in _MODEL_SUFFIXES and canon not in asked_words and word not in asked_words:
                return f"{token} {word}"
            if (
                "." not in token
                and "," not in token
                and token[-1].isdigit()
                and word in _DIGIT_LETTER_SUFFIXES
                and word not in asked_words
            ):
                return f"{token} {word}"
    for subbrand in _SUBBRANDS:
        if subbrand not in asked_words and re.search(rf"(?<![{_WORD_EDGE}]){subbrand}(?![{_WORD_EDGE}])", text):
            return subbrand
    return None


# END_CONTRACT: model_mismatch
# END_BLOCK_MODEL_TOKENS
