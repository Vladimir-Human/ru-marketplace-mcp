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

Plus structural drift detection for golden-fixture self-checks: shape_signature().
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

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

    Audit 2026-06-01 (CONFIRMED, but these helpers are not yet wired into any
    connector — latent contract): a string carrying a sign, a decimal point, or
    a K/M/тыс magnitude suffix is AMBIGUOUS — digit-concatenation silently
    fabricates a plausible-but-wrong count ('1.2K'->12, '-3'->3). Such inputs now
    return None (fail loud) rather than a fabricated integer. Pure grouped digits
    ('24 088', '1 057') still parse, because thousands separators are unambiguous.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        # Reject ambiguous forms instead of fabricating: a sign, a decimal
        # separator between digits, or a magnitude letter (K/M/тыс/k/тыс.).
        if re.search(r"[A-Za-zА-Яа-я]", s):  # any letter → unit/suffix → ambiguous
            return None
        if re.search(r"[-+]\s*\d", s):  # signed → drop-sign would lie
            return None
        if re.search(r"\d[.,]\d", s):  # decimal between digits → not an int
            return None
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else None
    return None


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
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        import math

        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    if isinstance(value, str):
        n = _parse_money_string(value)
        return n if (n is not None and n > 0) else None
    return None


def _parse_money_string(s: str) -> float | None:
    """Parse ONE money number from a display string, or None if absent/ambiguous.

    Handles RU/EU grouping (space, thin-space, NBSP, dot-as-grouping) and a
    comma/dot decimal with 1-2 fractional digits. Returns None when the string
    contains MORE THAN ONE distinct number (a range or discount blob) so the
    caller fails loud instead of concatenating unrelated digits.
    """
    if not s:
        return None
    # Normalize unicode spaces to plain space, drop currency/letters except sep.
    t = s.replace("\u2009", " ").replace("\u00a0", " ").replace("\u202f", " ")
    # Find number-like tokens: digits with optional grouping and one decimal tail.
    tokens = re.findall(r"\d[\d \.\,]*\d|\d", t)
    cleaned: list[str] = []
    for tok in tokens:
        tok = tok.strip()
        if tok:
            cleaned.append(tok)
    if len(cleaned) != 1:
        return None  # zero or multiple numbers → ambiguous → fail loud
    tok = cleaned[0]
    # Decimal tail: a comma or dot followed by exactly 1-2 digits at the END.
    m = re.search(r"[.,](\d{1,2})$", tok)
    frac = 0.0
    if m:
        frac = int(m.group(1)) / (10 ** len(m.group(1)))
        tok = tok[: m.start()]
    intpart = re.sub(r"[^\d]", "", tok)
    if not intpart:
        return None
    return float(int(intpart)) + frac


def coerce_rating(value: Any) -> float | None:
    """Coerce a rating like '4,9' / '4.9' / 4.9 to float in 0..5. None if
    unparseable, out of range, or carrying a non-/5 denominator.

    Audit 2026-06-01 (CONFIRMED — latent): the old version returned the FIRST
    numeric token of any string, so a review-count ('4,500 reviews' -> 4.5) or a
    wrong-scale rating ('4.9/10' -> 4.9) silently passed as an in-range 0..5
    rating. Now: an explicit denominator other than 5 returns None, a count-like
    blob returns None, and the parsed value must be within 0..5.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if 0 <= v <= 5 else None
    if isinstance(value, str):
        s = value.strip()
        # Explicit denominator: accept only /5; reject /10, /100, etc.
        dm = re.search(r"\d[.,]?\d*\s*/\s*(\d+)", s)
        if dm and dm.group(1) != "5":
            return None
        # A count-like blob ("4,500 reviews", "1 234 отзыва") is not a rating.
        if re.search(r"[A-Za-zА-Яа-я]", s) and re.search(r"\d[ ,]\d{3}\b", s):
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


def validate_offer(item: dict, *, require_title: bool = True) -> list[str]:
    """Invariant checks on one normalized offer (search item or card).

    These encode "things that must be true if parsing worked". A violation
    almost always means upstream schema drifted and a selector now points at
    the wrong field — exactly the silent failure mode we want to surface.
    """
    warnings: list[str] = []
    price = item.get("price_rub", item.get("price"))
    in_stock = item.get("in_stock")
    raw_rating = item.get("rating") if item.get("rating") is not None else item.get("rating_score")
    rating = coerce_rating(raw_rating)
    count = item.get("rating_count")

    if require_title and not item.get("title") and not item.get("name"):
        warnings.append("title_missing: parsed offer has no title/name (tile selector drift?)")

    # Normalize price for validation so a STRING price ('0', '-10', '3 983 ₽')
    # is range-checked, not just the literal int 0 (audit 2026-06-01: a string
    # '0' slipped through `price == 0` and a negative price ranked as cheapest).
    price_num = coerce_price(price) if isinstance(price, str) else price
    if price == 0 or price == 0.0:
        warnings.append("price_zero: price is 0 — should be None when no offer (price selector drift)")
    elif isinstance(price_num, (int, float)) and price_num <= 0:
        warnings.append(f"price_non_positive: price={price!r} <= 0 (selector drift / dead listing)")
    elif isinstance(price, str) and price.strip() and price_num is None:
        warnings.append(f"price_unparseable: price={price!r} did not parse to a number (selector drift)")

    if in_stock is True and (price is None):
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

    if reviews:
        texts_present = sum(
            1 for r in reviews if (r.get("text") or r.get("positive") or r.get("cons") or r.get("pros"))
        )
        if texts_present == 0:
            warnings.append("all_review_texts_empty: every returned review has empty text (content key drift)")

    if score is not None and not (0 <= score <= 5):
        warnings.append(f"score_out_of_range: rating_score={score} outside 0..5")

    return warnings


def attach_meta(result: dict, warnings: list[str], *, source: str) -> dict:
    """Attach a _meta block. Always present (even when healthy) so the agent can
    rely on its existence; warnings list is empty when healthy.
    """
    result["_meta"] = {
        "source": source,
        "healthy": not warnings,
        "warnings": warnings,
    }
    return result


# --------------------------------------------------------------------------
# Structural drift detection for golden-fixture self-checks.
# Compares the SHAPE (key sets / widget-name prefixes) of a live payload to a
# frozen baseline, ignoring volatile values. Catches "webPrice widget vanished"
# or "new required key appeared" before it silently breaks a parser.
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


# --------------------------------------------------------------------------
# Tri-state selfcheck contract (shared by youtube/fourpda/x selfcheck canaries).
#
# Design (10-lens gpt-5.5-xhigh consult 2026-06-01, all lenses converged): the
# wb/ozon 2-state {success|drift_detected} is INSUFFICIENT for connectors whose
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
