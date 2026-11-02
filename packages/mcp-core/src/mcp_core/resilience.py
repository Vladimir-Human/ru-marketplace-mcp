"""Tolerant-reader resilience helpers shared across marketplace connectors.

Rationale (researched Nov 2026 — Martin Fowler "Tolerant Reader" + resilient-
scraping literature): a parser of an UNOFFICIAL API can never be unbreakable,
but it can be made to (a) break rarely, (b) fail LOUDLY not silently, (c) be
fixable in one place, (d) degrade partially not totally.

This module is intentionally dependency-free (no pydantic) — the schema is ~8
fields per connector, so hand-rolled validation is simpler than a framework and
keeps both connector venvs untouched. Pure functions only: no network, no I/O,
so they are unit-testable offline in <1ms.

The three pillars implemented here:
  1. Multi-alias binding   -> first_present()       (renamed-field resilience)
  2. Type coercion         -> coerce_int/coerce_price (type-drift resilience)
  3. Loud output validation-> validate_offer/validate_review_block (drift alarm)

Plus structural drift detection for golden-fixture self-checks: shape_signature()
fingerprints a payload's paths and types with the values dropped, and diff_keys()
turns two fingerprints into missing/added paths. Connector selfchecks currently
do a live parse-smoke instead; wiring them onto stored baselines is the next
step, not something already in place.
"""
# START_MODULE_CONTRACT
# PURPOSE: Normalize drifting upstream values and report structural health honestly.
# SCOPE: Offline coercion, validation, shape fingerprints and tri-state canaries.
# DEPENDS: stdlib math,re,collections.abc,typing; mcp_core.redact; no network or persistence.
# LINKS: M-RESILIENCE; M-DOM; M-MODELS.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# DECOY_MARKERS: The one list of non-price text markers, shared with the DOM decoy regex.
# first_present: Bind the first non-null alias while preserving false/empty values.
# deep_first: Search nested dict/list payloads within a depth bound.
# coerce_int: Parse unambiguous counts or return None.
# coerce_count: Parse a labelled display count ("24 086 отзывов") or return None.
# coerce_price: Accept positive finite prices and reject ambiguous display blobs.
# coerce_rating: Normalize ratings on the 0..5 scale.
# health_warnings: Strip informational notes; the rest decide healthy.
# validate_offer: Report missing or contradictory normalized offer fields.
# validate_items: Aggregate per-item offer validation into systemic drift warnings.
# validate_review_block: Check returned counts and sufficiently large text samples.
# attach_meta: Mutate a result with source, health and warnings.
# bound_warnings: Deliver a bounded warning list that names what it suppressed.
# widget_prefixes: Strip volatile numeric widget suffixes.
# shape_signature: Fingerprint bounded paths/types independently of values.
# diff_keys: Return sorted missing and added structural keys.
# shape_drift_warning: One compact runtime warning when a required key family vanished.
# selfcheck_entry: Build healthy/drift/inconclusive sub-check metadata.
# selfcheck_result: Aggregate verdicts and inject omitted required checks.
# flatten_text: Reduce display fields to usable scalar text, never container repr.
# price_from_texts: Pick the first candidate that parses as a real price.
# END_MODULE_MAP

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from mcp_core.redact import redact_error_text

# A non-value sentinel distinct from None, so callers can tell "key absent"
# from "key present but null". Most callers just want the coalesced value, but
# validators need the distinction.
MISSING = object()


def first_present(d: dict, *aliases: str, default: Any = None) -> Any:
    """Return the first alias key whose value is present (not None/missing).

    Multi-alias binding: if upstream renames `price` -> `priceValue` -> `cost`,
    pass all three and the parser keeps working regardless of which is live.
    Empty string is treated as present (caller coerces); only None/absent skip.
    """
    for key in aliases:
        if key in d:
            v = d[key]
            if v is not None:
                return v
    return default


def deep_first(node: Any, key_names: Iterable[str], _depth: int = 0, _max: int = 40) -> Any:
    """JSONPath-shield: find the first occurrence of any key in key_names at any
    depth. Use ONLY as a last-resort fallback when the stable path is unknown —
    depth-bounded to avoid runaway recursion on pathological nesting.
    """
    if _depth > _max:
        return None
    targets = set(key_names)
    if isinstance(node, dict):
        for k, v in node.items():
            if k in targets and v is not None:
                return v
        for v in node.values():
            found = deep_first(v, targets, _depth + 1, _max)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = deep_first(item, targets, _depth + 1, _max)
            if found is not None:
                return found
    return None


def coerce_int(value: Any) -> int | None:
    """Coerce int / float / grouped-string ('24 088', '1\u2009057', '(15 374)') to int.

    Returns None (never 0) when no digits are present, so a missing count never
    masquerades as a real zero in comparisons.

    Audit 2026-06-01 (CONFIRMED; the helper is now used across the connectors):
    a string carrying a sign, a decimal point, or a K/M/тыс magnitude suffix
    is AMBIGUOUS — digit-concatenation silently
    fabricates a plausible-but-wrong count ('1.2K'->12, '-3'->3). Such inputs now
    return None (fail loud) rather than a fabricated integer. Pure grouped digits
    ('24 088', '1 057') still parse, because thousands separators are unambiguous.

    Audit 2026-11-01 (CONFIRMED): the letter guard knew only Latin and Cyrillic,
    so a CJK magnitude suffix ('12万') slipped through as 12, and a currency
    glyph ('24 088₽') was stripped and read as a count. Any-script letter and a
    currency glyph now return None instead of a fabricated count.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN and infinity reach here from the wire: json.loads accepts both by
        # default, and int() raises on either. coerce_price already guards with
        # isfinite; without the same guard here one poisoned cell aborts the
        # whole tool instead of degrading to "no data" for that field.
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        s = value.strip()
        # Reject ambiguous forms instead of fabricating: a sign, a decimal
        # separator between digits, a magnitude letter in any script
        # (K/M/тыс/万) or a currency glyph ('24 088₽' is a price, not a count).
        if any(ch.isalpha() for ch in s):  # any letter → unit/suffix → ambiguous
            return None
        if re.search(r"[₽$€¥￥£₸₴₺₹₩]", s):
            return None
        # signed → drop-sign would lie. Must cover the unicode minus (U+2212)
        # and the en/em dashes (U+2013/U+2014) a marketplace renders ranges
        # with ('1 000–2 000'): ASCII-only [-+] let a range digit-concatenate
        # into a fabricated count (10002000) — the very bug this guard exists
        # to prevent, inherited from coerce_price's range rejection.
        if re.search(r"[-+\u2212\u2013\u2014]\s*\d", s):
            return None
        if re.search(r"\d[.,]\d", s):  # decimal between digits → not an int
            return None
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else None
    return None


def coerce_count(value: Any) -> int | None:
    """Coerce a display count that may carry a unit label into an int, or None.

    ``coerce_int`` refuses a bare count that carries letters, because "1.2K"
    there is ambiguous. A marketplace review counter is different: it arrives
    as DISPLAY TEXT with a known label — Ozon renders "24 086 отзывов", the
    older format was "(15 374)". So the label is split off first and the
    NUMBER is then parsed strictly:

    * "24 086 отзывов", "1 200", "1200", "(15 374)" -> the number;
    * a sign ("-3", "+5", "−5"), a range ("1 000–2 000"), two numbers
      ("1 999 2 999"), a magnitude suffix ("1.2K", "2 тыс.") or any other
      letter -> None.

    Audit 2026-11-02 (CONFIRMED — R05): the Ozon tile parser and compare's
    count adapter both glued every digit of the display text together, so
    "1.2K отзывов" became the plausible-but-wrong 12 and the answer stayed
    healthy. A magnitude suffix is refused here for the same reason
    ``coerce_int`` refuses it: the mantissa is a guess about the site's
    rounding, and a wrong count is worse than an absent one. The grouped
    forms keep parsing, because a thousands separator is unambiguous.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    text = _strip_count_label(value)
    if not text:
        return None
    # Any leftover letter (a magnitude suffix, a currency word, a stray
    # symbol) means the text does not name exactly one plain number.
    if any(ch.isalpha() for ch in text):
        return None
    if re.search(r"[₽$€¥￥£₸₴₺₹₩]", text):
        return None
    # A sign, a range dash or a tilde: dropping the sign would fabricate a
    # count, and a range is two numbers. The unicode minus (U+2212) and the
    # en/em dashes (U+2013/U+2014) count as signs.
    if re.search(r"[-+−–—~]", text):
        return None
    return _parse_grouped_count(text)


# The unit labels a marketplace puts in front of / behind a counter. Only
# these are stripped; any other word stays in the text and makes it ambiguous.
_COUNT_LABEL_RE = re.compile(r"(?i)\b(?:отзыв\w*|review(?:s)?|оценк\w*)\b")
# Spaces a marketplace uses as a thousands separator: plain, no-break
# (U+00A0), narrow no-break (U+202F) and thin (U+2009).
_COUNT_SPACES = " \u00a0\u202f\u2009"


def _strip_count_label(text: str) -> str:
    """Remove a known review/rating label and the brackets around the number."""
    cleaned = _COUNT_LABEL_RE.sub(" ", text)
    cleaned = "".join(ch for ch in cleaned if ch not in "()[]{}")
    return " ".join(cleaned.replace("\t", " ").split())


def _parse_grouped_count(text: str) -> int | None:
    """Parse one grouped integer, or None when the text is not exactly one.

    Grouping is the same rule ``coerce_price`` uses for its integer part: a
    first group of one to three digits followed by three-digit groups, or a
    single ungrouped run. Anything else is two numbers glued together
    ("1 999 2 999") or a run with a foreign separator, and is refused.
    """
    normalized = text
    for space in _COUNT_SPACES:
        normalized = normalized.replace(space, " ")
    normalized = normalized.strip()
    if not normalized:
        return None
    groups = normalized.split(" ")
    groups = [group for group in groups if group]
    if not groups:
        return None
    if len(groups) == 1:
        if not re.fullmatch(r"\d+", groups[0]):
            return None
    elif not re.fullmatch(r"\d{1,3}", groups[0]) or not all(re.fullmatch(r"\d{3}", g) for g in groups[1:]):
        return None
    digits = re.sub(r"[^\d]", "", normalized)
    return int(digits) if digits else None


def coerce_price(value: Any) -> float | None:
    """Coerce a price (int rubles, float, or display string like '3\u2009983\u2009₽')
    to float rubles. Returns None when there is no real positive price — NEVER 0.0
    and NEVER a negative, because a dead/out-of-stock listing rendered as 0.0 or a
    drifted negative would falsely rank as the cheapest item and corrupt any
    "find the best/cheapest" comparison (real bug class on WB sizes[0].price=null).

    Audit 2026-06-01 (CONFIRMED — latent until a connector wires this helper):
      * <= 0 and non-finite now return None (the docstring's "NEVER 0.0" promise
        was previously violated: '0 ₽' -> 0.0, -10 -> -10.0).
      * A single decimal price ('1 234,56', '1,99') now parses to its real value
        (was inflated x100 by blind digit-concatenation).
      * A multi-number blob / price-range ('1 999 ₽ 2 999 ₽') is AMBIGUOUS and
        returns None (fail loud) rather than the concatenated nonsense 19992999.0.

    Audit 2026-11-01 (CONFIRMED): the same space that groups thousands also
    separates two numbers, so '1 999 2 999' and '1 999 2 999 ₽' glued into
    19992999.0, '3 983.999' (a 3-digit fraction) into 3983999.0, and a
    neighbour figure ('от 5 751 ₽/ мес.', 'выгода 5 000 ₽', 'скидка 30%')
    parsed as the price. All are None now: grouping is checked to be
    consistent and a non-price qualifier is refused, while 'от' ("from") stays
    a real price. The qualifier list is DECOY_MARKERS, the same one list the DOM
    extractor's decoy regex is generated from, so the two cannot drift apart.

    Audit 2026-11-01 (CONFIRMED): a magnitude suffix next to the number was
    ignored — '12万' read as 12.0 and '1.2K' as 1.2, while coerce_int refuses
    both. The price path now refuses a magnitude marker after the number too.

    Audit 2026-08-06 (found by property tests): a digit run past the float
    ceiling (~1.8e308) raised OverflowError, breaking totality — one poisoned
    cell aborted the whole tool. Such values are not prices; they are None.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except OverflowError:
            return None
        return v if math.isfinite(v) and v > 0 else None
    if isinstance(value, str):
        n = _parse_money_string(value)
        return n if (n is not None and n > 0) else None
    return None


# Text markers for a number that is NOT the price: monthly instalments, loyalty
# bonuses, discount badges, delivery-point counts, ratings, savings, and
# (Taobao) sales counts like «200+人付款» — the number of buyers, which a naive
# read promotes into a plausible-looking price.
#
# ONE list, and both consumers are generated from it: the display-string guard
# in _parse_money_string below and the DOM extractor's JavaScript decoy regex
# (mcp_core.dom). A second hand-written list is how the two quietly stop
# agreeing — someone adds a marker to the Python side, the other never sees it,
# and the bug reads as a mysterious wrong price rather than a stale list.
# Adding a marker anywhere means adding it here.
DECOY_MARKERS: tuple[str, ...] = (
    "мес",
    "рассрочк",
    "бонус",
    "балл",
    "кэшбэк",
    "кешбэк",
    "cashback",
    "трейд-ин",
    "trade-in",
    "пункт",
    "отзыв",
    "выгод",
    "скидк",
    "эконом",
    "saving",
    "discount",
    "month",
    "%",
    "付款",
    "收货",
    "已售",
    "约售",
)

_DECOY_RE = re.compile("|".join(re.escape(marker) for marker in DECOY_MARKERS), re.IGNORECASE)

# Magnitude shorthands make the digits a different number than they read:
# '12万' is 120 000 CNY, '1.2K' is 1 200 and '15 тыс.' is 15 000. Returning the
# digits alone understates the price, so a marker right after the number is
# ambiguous — the price-path half of coerce_int's magnitude-letter rule. A
# currency word ('руб'), a per-unit slash ('1 999 ₽/м') and a word that only
# starts with such a letter ('129 990 Купить', 'KZT') are not markers.
# Case-blind: '15 ТЫС. ₽' is the same 15 000.
_MAGNITUDE_SUFFIX_RE = re.compile(
    r"\s*(?:万|千|百|亿|тыс|млн|млрд|трлн|[KkMmBbКкМм](?![A-Za-zА-Яа-яЁё]))", re.IGNORECASE
)


def _parse_money_string(s: str) -> float | None:
    """Parse ONE money number from a display string, or None if absent/ambiguous.

    Handles RU/EU grouping (space, thin-space, NBSP, dot-as-grouping) and a
    comma/dot decimal with 1-2 fractional digits. Returns None when the string
    names something other than the item's price (a saving, a discount, a
    monthly instalment) or contains MORE THAN ONE number / an inconsistent
    grouping, so the caller fails loud instead of concatenating unrelated digits.
    """
    if not s:
        return None
    # Normalize unicode spaces to plain space, drop currency/letters except sep.
    t = s.replace("\u2009", " ").replace("\u00a0", " ").replace("\u202f", " ")
    # A display blob that names a saving, a discount, an instalment, a bonus or a
    # delivery-point count is not the item's price: those figures sit beside the
    # real price and are smaller, so a "cheapest" ranking would pick one of them.
    # DECOY_MARKERS is the project's ONE list of such words, shared with the DOM
    # extractor's JavaScript; "от" ("from") is NOT in it — a listing's "from"
    # price is a real price.
    if _DECOY_RE.search(t):
        return None
    # Find number-like tokens: digits with optional grouping and one decimal tail.
    matches = list(re.finditer(r"\d[\d \.\,]*\d|\d", t))
    # A leading minus makes this a delta, not a price. Marketplaces render the
    # discount badge as "-500 ₽" right beside the real price, and the token regex
    # below sees only digits — so without this the badge parses to 500 and, being
    # the smaller number, wins any "cheapest" ranking. Same bug class as the
    # instalment payment that once landed in five connectors' price fields. The
    # numeric branch above already rejects negatives; this is the string half of
    # the same promise. Covers ASCII hyphen, U+2212 minus and the dashes that
    # marketplaces use interchangeably.
    if matches:
        before = t[: matches[0].start()].rstrip()
        if before.endswith(("-", "\u2212", "\u2013", "\u2014")):
            return None
    tokens = [m.group(0).strip() for m in matches]
    tokens = [tok for tok in tokens if tok]
    if len(tokens) != 1:
        return None  # zero or multiple numbers → ambiguous → fail loud
    # A magnitude suffix right after the number makes the digits a different
    # number than they read ('12万' is 120 000, '1.2K' is 1 200, '15 тыс.' is
    # 15 000); returning the digits alone would understate the price, so it is
    # refused exactly as coerce_int refuses a magnitude letter.
    if _MAGNITUDE_SUFFIX_RE.match(t, matches[0].end()):
        return None
    return _parse_money_token(tokens[0])


def _parse_money_token(tok: str) -> float | None:
    """Parse one digit run, or None when its grouping is inconsistent.

    The space is both the Russian thousands separator and the gap between two
    numbers ('1 999 2 999'), so a run is one number only when its groups follow
    the grouping rule: a 1-3 digit first group and 3-digit later groups, or a
    single ungrouped run, with an optional 1-2 digit decimal tail. Mixing
    separators ('3 983.999') or leaving a short non-final group is ambiguous —
    a second number glued on — and returns None instead of concatenating digits.
    """
    # Decimal tail: a comma or dot followed by exactly 1-2 digits at the END.
    m = re.search(r"[.,](\d{1,2})$", tok)
    frac = 0.0
    if m:
        frac = int(m.group(1)) / (10 ** len(m.group(1)))
        tok = tok[: m.start()]
    groups = [g for g in re.split(r"[ .,]+", tok.strip()) if g]
    separators = set(re.findall(r"[ .,]", tok))
    if not groups or len(separators) > 1:
        return None  # mixed separators → cannot tell grouping from decimals
    if len(groups) == 1:
        if not re.fullmatch(r"\d+", groups[0]):
            return None
    elif not re.fullmatch(r"\d{1,3}", groups[0]) or not all(re.fullmatch(r"\d{3}", g) for g in groups[1:]):
        return None  # a short non-final group is another number, not a separator
    intpart = re.sub(r"[^\d]", "", tok)
    if not intpart:
        return None
    try:
        return float(int(intpart)) + frac
    except OverflowError:
        # A digit run past the float ceiling is not a price; degrade, don't raise.
        return None


def coerce_rating(value: Any) -> float | None:
    """Coerce a rating like '4,9' / '4.9' / 4.9 to float in 0..5. None if
    unparseable, out of range, or carrying a non-5 denominator ('/10', 'из 10').

    Audit 2026-06-01 (CONFIRMED — latent): the old version returned the FIRST
    numeric token of any string, so a review-count ('4,500 reviews' -> 4.5) or a
    wrong-scale rating ('4.9/10' -> 4.9) silently passed as an in-range 0..5
    rating. Now: an explicit denominator other than 5 returns None, a count-like
    blob returns None, and the parsed value must be within 0..5.

    Audit 2026-11-01 (CONFIRMED): the denominator was recognised only through
    '/', so the Russian '4.5 из 10' passed as 4.5. 'из N' now joins '/N'.

    Audit 2026-11-01 (CONFIRMED): the count rule above needed letters, so a
    bare thousands-grouped count ('1 299' — plain, no-break U+00A0, narrow
    no-break U+202F or thin U+2009 space) reached the number extraction and
    came back as a fabricated in-range rating (1.0). A space-grouped count is
    now None with or without letters; letter-free comma text keeps its old
    reading. The guard also carries a run longer than three digits without its
    own separator ('1 2999'), which a trailing word boundary let through.

    Audit 2026-08-07 (wave of cycles 24-30): json.loads admits
    arbitrary-precision ints, and float() raises OverflowError past the ceiling;
    such a value is not a rating — degrade to None like coerce_price does,
    never abort the tool.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            v = float(value)
        except OverflowError:
            return None
        return v if 0 <= v <= 5 else None
    if isinstance(value, str):
        s = value.strip()
        # Explicit denominator: accept only /5 or 'из 5'; reject /10, из 10, etc.
        dm = re.search(r"\d[.,]?\d*\s*(?:/|из)\s*(\d+)", s, re.IGNORECASE)
        if dm and dm.group(1) != "5":
            return None
        # A count-like blob ("4,500 reviews", "1 234 отзыва") is not a rating.
        if re.search(r"[A-Za-zА-Яа-я]", s) and re.search(r"\d[ ,]\d{3}\b", s):
            return None
        # A thousands-grouped count ("1 299", "12 345") is not a rating even
        # without letters: a rating selector that lands on the review counter
        # must not fabricate an in-range rating out of the first group. The
        # space, not the letters, is what betrays the count — plain, no-break
        # (U+00A0), narrow no-break (U+202F) and thin (U+2009) alike. A comma
        # without letters keeps its old reading ('4,9' stays a rating). Three or
        # more digits after the space: a word boundary let '1 2999' (a longer
        # counter missing its own separator) through as a fabricated 1.0.
        if re.search(r"\d[ \u00a0\u202f\u2009]\d{3,}", s):
            return None
        m = re.search(r"\d+[.,]?\d*", s)
        if not m:
            return None
        try:
            v = float(m.group(0).replace(",", "."))
        except ValueError:
            return None
        return v if 0 <= v <= 5 else None
    return None


# --------------------------------------------------------------------------
# Loud validation: turn silent corruption into explicit warnings the agent sees
# in-band. Each validator returns a list[str] of human-readable drift warnings.
# Empty list == healthy.
# --------------------------------------------------------------------------


def health_warnings(warnings: list[str]) -> list[str]:
    """The warnings that decide ``healthy``, per the contract in ``attach_meta``.

    Any warning is a doubt about the data unless it is an ``info:`` note, so a
    connector that builds its ``MetaOut`` by hand (WB does, for the tool-level
    fields it adds) uses this instead of writing ``healthy=not warnings`` and
    silently disagreeing with the connectors that go through ``attach_meta``.
    """
    return [warning for warning in warnings if not warning.startswith("info:")]


def bound_warnings(
    warnings: Iterable[str],
    *,
    limit: int = 10,
    subject: str = "warning",
    marker_prefix: str = "info: ",
) -> list[str]:
    """Deliver a bounded warning list that says what it left out.

    A shortened list of diagnostics reads as a different claim than the truth:
    ten warnings where the source reported twelve look like ten problems, not
    like a budget. So the budget stays, but the last entry names how many
    diagnostics were suppressed.

    * every entry is redacted, single-line and at most 500 characters;
    * duplicates collapse to their first occurrence;
    * a diagnostic that explains an unhealthy answer is never dropped behind
      an ``info:`` note: real warnings fill the budget first, notes take what
      is left;
    * the marker is itself an ``info:`` note — truncation is a fact about the
      delivery, not a doubt about the data, so it must not flip ``healthy``.

    Callers that compute ``healthy`` from the warnings they deliver should
    compute it from the FULL list: when the real warnings alone exceed the
    budget, the delivered list can no longer carry all of them.

    Audit 2026-11-02 (CONFIRMED — R18): compare's source adapter cut a
    twelve-warning reply to ten without a word, and the WB DOM path cut its
    warnings with ``warnings[:10]``.
    """
    cleaned: list[str] = []
    for warning in warnings:
        if not isinstance(warning, str):
            continue
        text = " ".join(redact_error_text(warning).split())
        if not text:
            continue
        if len(text) > 500:
            text = text[:497] + "..."
        if text not in cleaned:
            cleaned.append(text)
    if limit <= 0:
        return []
    if len(cleaned) <= limit:
        return cleaned
    budget = limit - 1
    real = [warning for warning in cleaned if not warning.startswith("info:")]
    notes = [warning for warning in cleaned if warning.startswith("info:")]
    kept = real[:budget]
    if len(kept) < budget:
        kept.extend(notes[: budget - len(kept)])
    suppressed = len(cleaned) - len(kept)
    kept.append(
        f"{marker_prefix}{subject}s_truncated: {suppressed} further {subject}(s) suppressed by the "
        f"{limit}-{subject} budget; {len(cleaned)} in total"
    )
    return kept


def validate_offer(item: dict, *, require_title: bool = True, price_key: str | None = None) -> list[str]:
    """Invariant checks on one normalized offer (search item or card).

    These encode "things that must be true if parsing worked". A violation
    almost always means upstream schema drifted and a selector now points at
    the wrong field — exactly the silent failure mode we want to surface.

    ``price_key`` names the price field when the connector does not call it
    ``price_rub``/``price`` (Taobao's ``price_cny``). Without it such an item
    would have no price at all as far as this validator is concerned, so a
    drifted price would pass unmentioned.
    """
    warnings: list[str] = []
    price = item.get(price_key) if price_key else item.get("price_rub", item.get("price"))
    in_stock = item.get("in_stock")
    raw_rating = item.get("rating") if item.get("rating") is not None else item.get("rating_score")
    rating = coerce_rating(raw_rating)
    count = item.get("rating_count")

    if require_title and not item.get("title") and not item.get("name"):
        warnings.append("title_missing: parsed offer has no title/name (tile selector drift?)")

    # Normalize price for validation so a STRING price ('0', '-10', '3 983 ₽')
    # is range-checked, not just the literal int 0 (audit 2026-06-01: a string
    # '0' slipped through `price == 0` and a negative price ranked as cheapest).
    # A price string that coerce_price REFUSES must be reported here: the
    # coercion hardening is only half the promise, because an unparsed price
    # otherwise reaches the caller as a silent None next to healthy=True.
    price_num = coerce_price(price)
    if isinstance(price_num, (int, float)) and not isinstance(price_num, bool) and price_num <= 0:
        # coerce_price already refuses <= 0, so this is the literal 0/-10 case.
        warnings.append(f"price_non_positive: price={price!r} <= 0 (selector drift / dead listing)")
    elif isinstance(price, str):
        if not price.strip():
            warnings.append(f"price_blank: price={price!r} is blank (price selector drift)")
        elif price_num is None:
            warnings.append(f"price_unparseable: price={price!r} did not parse to a number (selector drift)")
    elif price is not None and price_num is None:
        # A present non-string value coerce_price refuses (a list, an object).
        warnings.append(f"price_unparseable: price={price!r} is not a number (selector drift)")

    if in_stock is True and (price is None or price_num is None):
        warnings.append("instock_no_price: in_stock=True but price is None (contradiction)")

    # A rating value that is PRESENT but coerce_rating rejected (out of 0..5,
    # wrong scale, or count-blob) must still fire the drift alarm. Previously the
    # out-of-range check lived only AFTER coerce, but coerce now returns None for
    # such inputs, so we detect "present-but-unparseable" here instead (audit
    # 2026-06-01: coerce-hardening must not silence rating drift).
    if raw_rating is not None and rating is None:
        warnings.append(f"rating_out_of_range: rating={raw_rating!r} not a valid 0..5 rating (selector drift)")
    elif rating is not None and not (0 <= rating <= 5):
        warnings.append(f"rating_out_of_range: rating={rating} outside 0..5 (rating selector drift)")

    if isinstance(count, int) and count < 0:
        warnings.append(f"count_negative: rating_count={count} < 0")

    return warnings


def validate_items(
    items: Iterable[Any],
    *,
    require_title: bool = True,
    price_key: str | None = None,
    absent_price: str | None = None,
) -> list[str]:
    """Aggregate per-item offer validation into connector-level drift warnings.

    One warning per invariant CODE, kept only when at least half the returned
    items break it (`max(1, n // 2)`, so a single-item result still speaks).
    That threshold is what separates systemic selector drift — every tile's
    price field now points at a neighbour — from one unusual listing, and it
    keeps the response's warning list short enough to read.

    Items may be dicts or Pydantic models (the connectors build both); a model
    is validated with ``model_dump()``.

    ``absent_price`` is the code used when NOT ONE item carries a price; the
    per-code tally cannot express that by itself, because it counts items that
    HAVE a price field holding a bad value. Pass the connector's established
    wording (``no_prices_on_page``) to keep the wire stable. Any other
    invariant code is reported as ``<code>: <hits>/<n> items affected
    (likely schema drift)``, the form WB and Ozon already ship.

    An empty ``items`` returns nothing: there are no offers to validate, and
    the caller decides on its own whether emptiness is honest.
    """
    from collections import Counter

    tally: Counter[str] = Counter()
    total = 0
    priced = 0
    for item in items:
        if not isinstance(item, dict):
            item = item.model_dump()
        total += 1
        if coerce_price(item.get(price_key) if price_key else item.get("price_rub", item.get("price"))) is not None:
            priced += 1
        for warning in validate_offer(item, require_title=require_title, price_key=price_key):
            tally[warning.split(":")[0]] += 1
    if not total:
        return []
    threshold = max(1, total // 2)
    out: list[str] = []
    if absent_price and priced == 0:
        out.append(absent_price)
    out.extend(
        f"{code}: {hits}/{total} items affected (likely schema drift)"
        for code, hits in tally.items()
        if hits >= threshold
    )
    return out


REVIEW_TEXT_DRIFT_MIN_SAMPLE = 10


def validate_review_block(block: dict) -> list[str]:
    """Invariant checks on a reviews response (ozon_reviews / wb_reviews shape)."""
    warnings: list[str] = []
    returned = block.get("returned")
    # Presence-based alias (not truthiness): an EXPLICIT empty primary list must
    # NOT be silently replaced by a secondary pool (audit 2026-06-01 — `reviews or
    # feedbacks` let an empty primary `reviews=[]` fall through to `feedbacks`,
    # masking real drift). first_present preserves [] and 0.
    reviews = first_present(block, "reviews", "feedbacks", default=[])
    count = first_present(block, "reviews_count", "feedback_count", default=None)
    score = coerce_rating(first_present(block, "rating_score", "valuation", default=None))

    if count and not reviews:
        warnings.append(f"reviews_empty: count={count} but zero review texts returned (list widget drift)")

    if returned is not None and reviews is not None and returned != len(reviews):
        warnings.append(f"returned_mismatch: returned={returned} != len(reviews)={len(reviews)}")

    # Rating-only reviews are normal on mass-market cards, so a small all-empty
    # sample says nothing about the parser (issue #111). Only a sample of at
    # least REVIEW_TEXT_DRIFT_MIN_SAMPLE reviews with zero text is drift.
    if reviews and len(reviews) >= REVIEW_TEXT_DRIFT_MIN_SAMPLE:
        texts_present = sum(
            1 for r in reviews if (r.get("text") or r.get("positive") or r.get("cons") or r.get("pros"))
        )
        if texts_present == 0:
            warnings.append(
                f"all_review_texts_empty: none of {len(reviews)} returned reviews has text (content key drift?)"
            )

    if score is not None and not (0 <= score <= 5):
        warnings.append(f"score_out_of_range: rating_score={score} outside 0..5")

    return warnings


def attach_meta(result: dict, warnings: list[str], *, source: str) -> dict:
    """Attach a _meta block. Always present (even when healthy) so the agent can
    rely on its existence; warnings list is empty when healthy.

    **What ``healthy`` means, everywhere in this workspace** (audit 2026-11-01:
    it meant three different things, so the one flag the agent is told to read
    was worth nothing): ``healthy`` is True only when the parser got a complete
    answer AND every value in it was trustworthy. Any drift, validation or
    completeness warning makes it False — including a warning the connector
    adds by hand to a response model instead of going through this helper. A
    connector must therefore never hardcode ``healthy=True`` next to a
    non-empty ``warnings`` list.

    Informational notes are not health: a warning whose text starts with
    ``info:`` is a note about context the caller may want (which region was
    used), never a doubt about the data, and it does not flip the flag. Every
    other warning is a doubt about the data or about the parser, and does.
    """
    data = health_warnings(warnings)
    result["_meta"] = {
        "source": source,
        "healthy": not data,
        "warnings": warnings,
    }
    return result


# --------------------------------------------------------------------------
# Structural drift detection for golden-fixture self-checks and for the
# runtime data path (shape_drift_warning). Compares the SHAPE (key sets /
# widget-name prefixes) of a live payload to a frozen baseline, ignoring
# volatile values. Catches "webPrice widget vanished" or "new required key
# appeared" before it silently breaks a parser.
# --------------------------------------------------------------------------


def widget_prefixes(widget_states: dict) -> set[str]:
    """Reduce Ozon widgetStates keys to their stable prefixes (strip the numeric
    instance id): 'webPrice-3121879-default-1' -> 'webPrice'. The prefix is the
    semantic anchor; the numbers are volatile.
    """
    out: set[str] = set()
    for k in widget_states or {}:
        out.add(re.split(r"-\d", k, maxsplit=1)[0])
    return out


def shape_signature(payload: Any, *, max_depth: int = 6) -> list[str]:
    """Fingerprint a parsed payload's structure, dropping every value.

    Returns a sorted list of ``path:type`` entries — ``items[].price_rub:float``
    rather than ``items[0].price_rub: 52999.0``. Values change on every request
    and say nothing about drift; the set of paths and their types is what a
    parser actually depends on, so comparing two signatures answers the one
    question a selfcheck cares about: did the shape move, or did the data just
    change?

    List indices collapse to ``[]`` and element shapes are unioned, so a
    10-item page and a 2-item page of the same products fingerprint identically
    while a renamed or retyped field shows up immediately. Feed the result to
    ``diff_keys`` against a stored baseline to get missing/added paths.

    Depth is bounded because some of these payloads are recursive (WB's
    category tree), and an unbounded walk on a drifted response is a hang
    rather than a diagnosis.
    """
    acc: set[str] = set()
    _walk_shape(payload, "", acc, max_depth)
    return sorted(acc)


def _type_tag(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _walk_shape(value: Any, path: str, acc: set[str], depth: int) -> None:
    if depth <= 0:
        acc.add(f"{path or '.'}:<truncated>")
        return
    if isinstance(value, dict):
        if not value:
            acc.add(f"{path or '.'}:empty_object")
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            _walk_shape(item, child, acc, depth - 1)
        return
    if isinstance(value, (list, tuple)):
        if not value:
            acc.add(f"{path or '.'}:empty_array")
        for item in value:
            _walk_shape(item, f"{path}[]", acc, depth - 1)
        return
    acc.add(f"{path or '.'}:{_type_tag(value)}")


def diff_keys(expected: Iterable[str], actual: Iterable[str]) -> dict[str, list[str]]:
    """Return {'missing': [...], 'added': [...]} comparing two key collections.
    'missing' (in expected, gone from live) is the dangerous one — a parser
    likely depends on it. 'added' is informational (new upstream feature).
    """
    exp, act = set(expected), set(actual)
    return {
        "missing": sorted(exp - act),
        "added": sorted(act - exp),
    }


def _emptied_container(path: str, entries: set[str]) -> bool:
    """True when the path descends into a NESTED array that is empty everywhere.

    The walker writes an empty list as ``<array path>:empty_array`` at the
    array's own path (``products[].sizes:empty_array``), so the elements a
    member below it looks for (``products[].sizes[].price``) do not exist:
    the container being empty is a data fact — a page of delisted products —
    not a key that vanished. It stays narrow on purpose:
    - the top-level envelope (the first ``[]``) never counts: a zero-item
      payload is the call site's business, and an empty sibling envelope
      (``items: []`` next to a filled ``goods[]``) must not hide drift there;
    - the signature merges elements, so ``:empty_array`` next to any
      ``<container>[]`` entry means some elements are filled: a page mixing
      delisted products and sizes that lost the key still warns;
    - an ``:empty_object`` element is not an empty container.
    """
    start = path.find("[]", path.find("[]") + 2) if "[]" in path else -1
    while start != -1:
        container = path[:start]
        if f"{container}:empty_array" in entries and not any(e.startswith(f"{container}[]") for e in entries):
            return True
        start = path.find("[]", start + 2)
    return False


def shape_drift_warning(
    payload: Any,
    required_families: Iterable[Iterable[str]],
    *,
    source: str,
) -> str | None:
    """One compact warning when a live payload lost a required key family.

    The runtime companion to the selfcheck's structural comparison: the
    tool's own response carries ``shape_drift: <source>: missing <family>``
    in its meta warnings when a parser-critical family of keys vanished, so
    the agent sees drift without a human running ``doctor``.
    ``required_families`` is the connector's registry of alias families
    (a flat required-key list is a list of one-member families); a rename
    WITHIN a family is tolerated, the loss of every member is drift. A
    family member is PRESENT regardless of its type — ``price:null`` is a
    page without a price, not a page that lost its price field. A member
    below an array the payload shows as empty (``sizes: []``) is present
    too: with no elements there is no key to lose (``_emptied_container``).

    Comparison only, so the normal data path stays cheap: no fetch, no
    validation, no state. A payload whose families all survive costs one
    bounded signature walk and returns None; the first missing family is
    named and the rest of the walk stops there.
    """
    entries = set(shape_signature(payload))
    for family in required_families:
        paths = tuple(family)
        # A member counts when a signature entry EXTENDS one of its paths:
        # entries continue a path with ':type', a '[...] array segment or a
        # '.nested' key, so an entry like 'items[].id:int' or
        # 'items[].price_texts.attached[]:str' matches, while 'identifier'
        # does not extend 'id'.
        present = any(
            entry.startswith(f"{path}{sep}") for path in paths for entry in entries for sep in (":", "[", ".")
        ) or any(_emptied_container(path, entries) for path in paths)
        if not present:
            return f"shape_drift: {source}: missing {'/'.join(paths)}"
    return None


# --------------------------------------------------------------------------
# Tri-state selfcheck contract, shared by the nine connectors that ship a
# *_selfcheck canary.
#
# Two states — {success | drift_detected} — are not enough for a connector whose
# baseline can rot, whose session can expire, or whose transport can be blocked.
# Collapsing not_authenticated / transport_down / baseline_gone into "drift"
# trains the operator to ignore real drift alarms (false positives); collapsing
# them into "success" recreates the silent-false-empty bug. So a canary check is
# tri-state:
#   * "healthy"      — baseline reached AND parser-critical anchors present.
#   * "drift"        — baseline reached (loaded, authenticated, not gone) BUT a
#                      parser-critical anchor / parse-smoke is missing. LOUD.
#   * "inconclusive" — could NOT judge drift: transport_down, not_authenticated,
#                      blocked/rate_limited, or baseline_gone. NOT an alarm.
#
# The verdict logic is a PURE function over an already-fetched payload, separate
# from the live fetch, so both-ways tests run OFFLINE (no network/CDP/yt-dlp).
# --------------------------------------------------------------------------

# Reason codes for an inconclusive check (never a drift alarm).
INCONCLUSIVE_REASONS = frozenset(
    {
        "not_authenticated",
        "transport_down",
        "blocked",
        "rate_limited",
        "timeout",
        "baseline_gone",
        "dependency_unavailable",
        "skipped",
    }
)


def selfcheck_entry(
    state: str,
    *,
    baseline: str = "",
    reason: str | None = None,
    notes: list[str] | None = None,
    **extra: Any,
) -> dict:
    """Build one normalized selfcheck sub-check entry.

    state must be 'healthy' | 'drift' | 'inconclusive'. `ok` is True only for
    healthy, False for drift, None for inconclusive (so a caller can tell "this
    proved broken" from "this could not be judged"). Extra structural diagnostics
    (counts, missing anchors) pass through as **extra.
    """
    if state not in ("healthy", "drift", "inconclusive"):
        # Defensive: an unknown state is itself inconclusive, never silently ok.
        notes = (notes or []) + [f"unknown state {state!r} coerced to inconclusive"]
        state = "inconclusive"
        reason = reason or "dependency_unavailable"
    ok = {"healthy": True, "drift": False, "inconclusive": None}[state]
    entry: dict[str, Any] = {"state": state, "ok": ok, "baseline": baseline, "reason": reason, "notes": notes or []}
    entry.update(extra)
    return entry


def selfcheck_result(
    connector: str,
    checks: dict[str, dict],
    *,
    required: Iterable[str] = (),
    server_version: str | None = None,
    server_started_at: str | None = None,
    process_id: int | None = None,
) -> dict:
    """Aggregate per-check tri-state entries into the unified top-level verdict.

    Aggregation precedence (lens-agreed): ANY check 'drift' -> top
    'drift_detected' (a real parsed baseline showing a missing anchor is the LOUD
    alarm and must dominate); else ANY 'inconclusive' -> 'inconclusive'; else all
    healthy -> 'success'. `healthy` is True only for success, False for drift,
    None for inconclusive — so the agent never reads an inconclusive canary as a
    clean bill of health.

    Completeness invariant (audit 2026-06-01 CONTRACT): a `required` sub-check
    that the caller forgot to populate is NOT silently dropped — it is injected
    as an `inconclusive`/`selfcheck_incomplete` entry, so a missing parser
    dimension can never aggregate to a false `success`.
    """
    checks = dict(checks)  # don't mutate caller's dict
    for key in required:
        if key not in checks:
            checks[key] = selfcheck_entry(
                "inconclusive",
                reason="dependency_unavailable",
                notes=[f"required check {key!r} never ran (selfcheck_incomplete)"],
            )
    states = [c.get("state") for c in checks.values()]
    if "drift" in states:
        status, healthy = "drift_detected", False
    elif not states:
        # No checks AND none required -> cannot certify health; inconclusive,
        # never a vacuous success (audit CONTRACT: empty-checks hole).
        status, healthy = "inconclusive", None
    elif all(s == "healthy" for s in states):
        status, healthy = "success", True
    else:
        status, healthy = "inconclusive", None
    result: dict[str, Any] = {
        "status": status,
        "healthy": healthy,
        "connector": connector,
        "checks": checks,
    }
    if server_version is not None:
        result["server_version"] = server_version
    if server_started_at is not None:
        result["server_started_at"] = server_started_at
    if process_id is not None:
        result["process_id"] = process_id
    return result


def flatten_text(value: Any, *keys: str) -> str | None:
    """Reduce a value upstream ships as EITHER a string OR an object to text.

    Audit 2026-07-28 (CONFIRMED live): Avito's ``js/items`` returns ``location``
    as a nested object (``{"id": 637640, "name": "Москва", ...}``), while the
    output model declares ``location: str | None``. Passing the dict through
    ``first_present`` unchanged made Pydantic raise ``ValidationError`` and took
    the whole ``avito_search`` call down — a page of perfectly good listings lost
    to one field that grew a level of nesting.

    The rule this encodes: a display field must degrade to a name or to an honest
    ``None``, never to ``str(dict)``. ``"{'id': 637640, 'name': 'Москва'}"`` is
    not a location, and shipping it would put Python repr syntax in front of a
    user while passing validation — silent-wrong, which is worse than the crash.

    Lists take their first usable element, because upstream sometimes wraps a
    single value in an array.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in keys or ("name", "title", "text", "value"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()
            if isinstance(got, (int, float)) and not isinstance(got, bool):
                return str(got)
        return None
    if isinstance(value, (list, tuple)):
        for element in value:
            text = flatten_text(element, *keys)
            if text:
                return text
        return None
    return None


def price_from_texts(*candidates: Any) -> float | None:
    """First candidate that parses to a real positive price, else None.

    DOM extractors collect several price-shaped strings per tile (current price,
    strikethrough price, an instalment line). They must NOT do the arithmetic
    themselves: ``coerce_price`` already understands non-breaking spaces, a
    missing currency glyph, comma decimals, and — critically — refuses an
    ambiguous multi-number blob instead of concatenating it into nonsense.

    Audit 2026-07-28: the CDP extractors were computing ``Math.min`` over every
    line containing ``руб``/``₽``, which (a) missed Citilink entirely, where the
    glyph lives in a sibling element of the digits, and (b) returned the monthly
    instalment on DNS, whose tiles carry "от 5 751 ₽/ мес." next to a 58 999 ₽
    price. Order the candidates most-specific-first and let this pick.
    """
    for candidate in candidates:
        price = coerce_price(candidate)
        if price is not None:
            return price
    return None
