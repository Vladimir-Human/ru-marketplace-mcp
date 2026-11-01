"""Tests for the tolerant-reader coercion helpers.

These five functions decide whether a wrong number reaches the user. Every
connector routes its prices, counts and ratings through them, and the project's
worst historical bugs were all of one shape: something that was *not* a price
arriving in a price field — an instalment across five sites, a `0.0` for a
delisted item that then won a "cheapest" ranking.

Until now the module had no test file of its own; it was exercised only
sideways, through connector suites that happened to import it, which left the
coercion edges — the part that has to be paranoid — unpinned. This file covers
them head-on.

The rule they all share: **when in doubt, return None.** A missing value is
honest; a plausible wrong one is the failure this project exists to avoid.
"""

from __future__ import annotations

import pytest
from mcp_core.resilience import (
    REVIEW_TEXT_DRIFT_MIN_SAMPLE,
    attach_meta,
    coerce_int,
    coerce_price,
    coerce_rating,
    first_present,
    flatten_text,
    health_warnings,
    price_from_texts,
    selfcheck_entry,
    selfcheck_result,
    validate_items,
    validate_offer,
    validate_review_block,
)

# --------------------------------------------------------------------------- #
# Non-finite floats — reachable from the wire
# --------------------------------------------------------------------------- #

NON_FINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("value", NON_FINITE)
def test_coerce_int_survives_non_finite_floats(value):
    """`json.loads` accepts NaN and Infinity by default, so both arrive from the
    wire, and `int()` raises on either. One poisoned cell has to degrade that
    field to "no data", never abort the whole tool."""
    assert coerce_int(value) is None


@pytest.mark.parametrize("value", NON_FINITE)
def test_coerce_price_survives_non_finite_floats(value):
    assert coerce_price(value) is None


@pytest.mark.parametrize("value", NON_FINITE)
def test_coerce_rating_survives_non_finite_floats(value):
    assert coerce_rating(value) is None


def test_coerce_rating_survives_an_int_past_the_float_ceiling():
    """`json.loads` returns arbitrary-precision ints, so a rating cell past the
    float ceiling makes `float()` raise OverflowError. coerce_price already
    degrades such values to None; coerce_rating must match — a poisoned cell
    strips the field to no-data, never aborts the tool."""
    assert coerce_rating(10**400) is None


@pytest.mark.parametrize("value", ["4.5 из 10", "4,5 из 10"])
def test_coerce_rating_rejects_a_non_five_denominator_in_words(value):
    """A 10-point rating is not the 0..5 scale. The denominator guard knew only
    '/', so '4.5 из 10' passed as 4.5 (audit 2026-11-01)."""
    assert coerce_rating(value) is None


def test_coerce_rating_keeps_a_five_denominator_written_in_words():
    assert coerce_rating("4.5 из 5") == pytest.approx(4.5)
    assert coerce_rating("4,5 из 5") == pytest.approx(4.5)


@pytest.mark.parametrize(
    "value",
    [
        "1 299",  # plain space
        "1\u00a0299",  # no-break space
        "1\u202f299",  # narrow no-break space
        "1\u2009299",  # thin space
        "5 000",  # the count that would fabricate the maximum rating
        "12 345",  # already out of range on its own; pinned for the rule
    ],
)
def test_coerce_rating_rejects_a_thousands_grouped_count(value):
    """A rating selector that lands on the review counter reads a grouped
    count, not a rating. The old count rule needed letters, so the bare
    '1 299' came back as a fabricated in-range 1.0 (audit 2026-11-01); the
    space, not the letters, is what betrays the count."""
    assert coerce_rating(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("4,9", 4.9),
        ("4.9", 4.9),
        ("4.9/5", 4.9),
        ("4,9 из 5", 4.9),
        ("5", 5.0),
        (" 4.7 ", 4.7),
        (4.9, 4.9),
    ],
)
def test_coerce_rating_keeps_ordinary_ratings(value, expected):
    """The count guard must not eat the shapes a real rating arrives in."""
    assert coerce_rating(value) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# coerce_price — never 0.0, never negative
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [
        (3983, 3983.0),
        (3983.5, 3983.5),
        ("3 983 ₽", 3983.0),  # narrow no-break space, the usual separator
        ("3 983 ₽", 3983.0),
        ("1 234,50", 1234.50),  # non-breaking space + decimal comma
    ],
)
def test_real_prices_parse(value, expected):
    assert coerce_price(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [0, 0.0, "0", "0 ₽", -1, -1.0])
def test_zero_and_negative_are_not_prices(value):
    """A dead listing rendered as 0 must not win a "cheapest" comparison."""
    assert coerce_price(value) is None


@pytest.mark.parametrize(
    "value",
    ["-500 ₽", "\u2212500 ₽", "\u2013 500 ₽", "скидка -500 ₽", "-1 234,50"],
)
def test_a_leading_minus_means_a_discount_not_a_price(value):
    """Marketplaces print the discount badge as "-500 ₽" next to the real price.
    The token regex sees only digits, so without a sign check the badge parses to
    500 and — being smaller — wins a "cheapest" ranking. Same bug class as the
    instalment payment that once reached five connectors' price fields."""
    assert coerce_price(value) is None


@pytest.mark.parametrize("value", [None, "", "   ", "цена по запросу", "нет в наличии", [], {}, True])
def test_non_prices_are_none(value):
    assert coerce_price(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "1 999 2 999",  # two prices glued by the thousands-separator space
        "1 999 2 999 ₽",  # same, with the currency glyph
        "от 5 751 ₽/ мес.",  # a monthly instalment, not the price
        "выгода 5 000 ₽",  # a saving
        "скидка 30%",  # a discount
        "3 983.999",  # a three-digit fraction is not a price display
        "1,999,2,999",  # short non-final groups glue two numbers
    ],
)
def test_ambiguous_or_non_price_blobs_are_none(value):
    """The space is both the Russian thousands separator and the gap between two
    numbers, so the old token regex read '1 999 2 999' as one number and the
    "more than one number" guard never fired. A saving, a discount and a monthly
    instalment sit beside the real price and are smaller, so a guess would win a
    "cheapest" ranking. All of them are None (audit 2026-11-01)."""
    assert coerce_price(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1 999 ₽", 1999.0),
        ("1\u00a0999 ₽", 1999.0),  # no-break space
        ("1\u2009999,50 ₽", 1999.50),  # thin space + decimal comma
        ("1999.00", 1999.0),
        ("от 1 999 ₽", 1999.0),  # a listing's "from" price is a real price
    ],
)
def test_price_shapes_the_stricter_parse_must_keep(value, expected):
    """The grouping/qualifier checks must not eat the ordinary display shapes."""
    assert coerce_price(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [
        "Рассрочка 5 751 ₽",  # an instalment plan, not the price
        "в 1356 пунктов",  # a delivery-point count
        "+ 1 655 бонусов",  # loyalty points
        "кэшбэк 500 ₽",  # cashback
    ],
)
def test_coerce_price_refuses_the_shared_decoy_markers(value):
    """R-NO-FABRICATION: the non-price word guard was a second hand-written list,
    so 'Рассрочка 5 751 ₽' parsed as 5751.0 and a delivery-point count as its
    number. coerce_price now uses DECOY_MARKERS, the one list the DOM decoy
    regex is generated from; every value here returns None on the new code and
    a number on the old one."""
    assert coerce_price(value) is None


def test_price_from_texts_skips_a_decoy_and_takes_the_real_price():
    """The DOM extractors feed candidates in order: a decoy marker must not win
    just because it came first (it used to answer 1356.0 here)."""
    assert price_from_texts("в 1356 пунктов", "58 999 ₽") == pytest.approx(58999.0)


@pytest.mark.parametrize("value", ["12万", "1.2万", "1.2K", "15 тыс. ₽", "15 ТЫС. ₽", "2 Млн ₽"])
def test_a_magnitude_suffix_is_not_a_price(value):
    """R-NO-FABRICATION: coerce_int refuses a magnitude suffix, the price path
    ignored it — '12万' read as 12.0, '1.2K' as 1.2. The digits alone understate
    the price, so a marker right after the number is None like the count case."""
    assert coerce_price(value) is None


@pytest.mark.parametrize("value", ["1 2999", "1\u00a02999"])
def test_coerce_rating_rejects_a_grouped_count_without_its_own_separator(value):
    """The count guard ended on a word boundary, so a run longer than three
    digits ('1 2999', a counter missing its second separator) slipped past it
    and came back as a fabricated in-range 1.0."""
    assert coerce_rating(value) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1 999 ₽", 1999.0),
        ("1 999,50 ₽", 1999.5),
        ("от 1 999 ₽", 1999.0),
        ("¥129", 129.0),
        ("129.00", 129.0),
        ("129 990 Купить", 129990.0),
        ("5 990 KZT", 5990.0),
    ],
)
def test_price_shapes_the_decoy_and_magnitude_guards_must_keep(value, expected):
    """The stricter guards must not eat the ordinary shapes: a currency glyph,
    a decimal comma and the "от" ("from") qualifier stay real prices, and a
    word that only starts with a magnitude letter is not a magnitude."""
    assert coerce_price(value) == pytest.approx(expected)


def test_validate_offer_flags_an_ambiguous_price():
    """The audit case: an in-stock offer whose price is '1 999 2 999' used to
    report only the missing title. Once coerce_price refuses the blob, the
    validator must surface it as an unparseable price, not stay silent."""
    warnings = validate_offer({"in_stock": True, "price": "1 999 2 999"})
    assert any("title" in warning for warning in warnings)
    assert any("price" in warning for warning in warnings)


def test_validate_offer_flags_a_blank_or_unparseable_price_string():
    """R-ITEM-HONESTY: a price field that is present but useless is drift.

    Audit 2026-11-01: a whitespace-only price passed with NO warning at all
    (`price.strip()` was empty, so the unparseable branch stayed silent), and
    after coerce_price was hardened the remaining fabricated blobs — a
    discount, an instalment line, two glued prices — still passed with
    healthy=true because validate_offer only range-checked them.
    """
    for value, code in [
        ("   ", "price_blank"),
        ("", "price_blank"),
        ("скидка 30%", "price_unparseable"),
        ("1 999 2 999", "price_unparseable"),
        ("от 5 751 ₽/ мес.", "price_unparseable"),
        ("30%", "price_unparseable"),
    ]:
        warnings = validate_offer({"title": "X", "price": value})
        assert any(warning.startswith(code) for warning in warnings), (value, warnings)


def test_validate_offer_reads_a_connector_specific_price_key():
    """R-ITEM-HONESTY: Taobao prices live in price_cny; a validator that only
    knows price_rub/price would see no price at all and stay silent."""
    warnings = validate_offer({"title": "X", "price_cny": "1 999 2 999"}, price_key="price_cny")
    assert any(warning.startswith("price_unparseable") for warning in warnings)


def test_validate_items_reports_a_systemic_invariant_break():
    """R-ITEM-HONESTY: one bad tile is a listing, every bad tile is drift."""
    items = [{"title": f"t{i}", "price_rub": "1 999 2 999"} for i in range(4)]
    warnings = validate_items(items)
    assert any(warning.startswith("price_unparseable: 4/4") for warning in warnings)


def test_validate_items_does_not_call_one_bad_tile_in_five_drift():
    items = [{"title": f"t{i}", "price_rub": 100.0 + i} for i in range(5)]
    items[0]["price_rub"] = "1 999 2 999"
    assert validate_items(items) == []


def test_validate_items_names_the_absent_price_case_in_the_connectors_wording():
    """R-ITEM-HONESTY: the per-code tally cannot express "no tile has a price",
    so the caller supplies its established code and the wire stays stable."""
    warnings = validate_items(
        [{"title": "t", "price_cny": None}], price_key="price_cny", absent_price="no_prices_on_page"
    )
    assert warnings == ["no_prices_on_page"]


def test_validate_items_is_silent_on_an_empty_page():
    """Zero offers is not a per-item finding: the caller owns that verdict."""
    assert validate_items([]) == []


def test_attach_meta_keeps_an_informational_note_out_of_health():
    """R-ITEM-HONESTY: healthy is False for any doubt about the data, and an
    `info:` note is context, not a doubt (audit 2026-11-01: WB hardcoded
    healthy=True next to a warning, so the flag meant nothing)."""
    assert attach_meta({}, ["info: the region follows the scraping Chrome"], source="s")["_meta"]["healthy"] is True
    assert attach_meta({}, ["no_prices_on_page"], source="s")["_meta"]["healthy"] is False
    # A connector that builds MetaOut by hand uses the same split.
    assert health_warnings(["info: region", "no_prices_on_page"]) == ["no_prices_on_page"]


# --------------------------------------------------------------------------- #
# coerce_int — a missing count must not read as a real zero
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [
        (42, 42),
        (3.7, 3),
        ("24 088", 24088),
        ("1 057", 1057),
        ("(15 374)", 15374),
    ],
)
def test_counts_parse(value, expected):
    assert coerce_int(value) == expected


@pytest.mark.parametrize("value", [None, "", "нет", [], {}])
def test_absent_counts_are_none_not_zero(value):
    assert coerce_int(value) is None


@pytest.mark.parametrize("value", [True, False])
def test_bools_are_not_counts(value):
    """`isinstance(True, int)` is True in Python; a flag must not become a count."""
    assert coerce_int(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "1 000–2 000 ₽",  # range with a unicode en dash
        "1 000—2 000",  # range with a unicode em dash
        "5–10",  # compact unicode range
        "−5",  # unicode minus — dropping the sign would lie
        "-3",  # ascii sign already rejected; pinned here for parity
    ],
)
def test_counts_with_signs_or_ranges_are_ambiguous_and_none(value):
    """A sign or a dash-separated range is ambiguous. Digit-concatenation would
    fabricate a plausible-but-wrong count ('1 000–2 000' -> 10002000, '−5' -> 5),
    exactly the bug coerce_price rejects for prices — counters inherit it. The
    guard must cover the unicode minus/en-dash/em-dash, not only ASCII -/+."""
    assert coerce_int(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "12万",  # CJK magnitude suffix: 120000, not 12
        "24 088₽",  # a price, not a count
    ],
)
def test_counts_with_any_script_suffix_or_currency_are_none(value):
    """The old letter guard knew only Latin and Cyrillic, so '12万' read as 12;
    a currency glyph is not a letter at all, so '24 088₽' read as a count. Both
    are a fabricated number where the honest answer is None (audit 2026-11-01)."""
    assert coerce_int(value) is None


# --------------------------------------------------------------------------- #
# flatten_text — upstream ships a field as either a string or an object
# --------------------------------------------------------------------------- #


def test_flatten_text_passes_a_plain_string():
    assert flatten_text("Москва") == "Москва"


def test_flatten_text_reads_the_named_key_out_of_an_object():
    """Avito's `location` arrived as a string until it became `{"name": ...}`,
    and the connector reported every listing from "None"."""
    assert flatten_text({"name": "Москва", "id": 621540}, "name") == "Москва"


def test_flatten_text_tries_keys_in_order():
    assert flatten_text({"title": "Тверь"}, "name", "title") == "Тверь"


@pytest.mark.parametrize("value", [None, "", {}, {"other": "x"}, []])
def test_flatten_text_gives_none_rather_than_a_guess(value):
    assert flatten_text(value, "name") is None


def test_flatten_text_stringifies_a_scalar():
    """A bare number is a value upstream chose to send unquoted, not a guess."""
    assert flatten_text(42, "name") == "42"


# --------------------------------------------------------------------------- #
# price_from_texts — first candidate that is a real price
# --------------------------------------------------------------------------- #


def test_price_from_texts_takes_the_first_real_price():
    assert price_from_texts(None, "", "нет цены", "4 599 ₽", "9 999 ₽") == pytest.approx(4599.0)


def test_price_from_texts_skips_zero_and_discount_badges():
    assert price_from_texts("0 ₽", "-100 ₽", "250 ₽") == pytest.approx(250.0)


def test_a_discount_badge_does_not_outrank_the_real_price():
    """The tile order that matters: badge first, price second."""
    assert price_from_texts("-500 ₽", "4 599 ₽") == pytest.approx(4599.0)


def test_price_from_texts_returns_none_when_nothing_parses():
    assert price_from_texts(None, "", "по запросу") is None


# --------------------------------------------------------------------------- #
# first_present — alias binding survives an upstream rename
# --------------------------------------------------------------------------- #


def test_first_present_returns_the_first_bound_alias():
    assert first_present({"cost": 10, "price": 20}, "price", "priceValue", "cost") == 20


def test_first_present_falls_through_to_a_later_alias():
    assert first_present({"cost": 10}, "price", "priceValue", "cost") == 10


def test_first_present_treats_none_as_absent():
    """A key present with a null value is upstream saying "no data", not a value."""
    assert first_present({"price": None, "cost": 10}, "price", "cost") == 10


def test_first_present_uses_the_default_when_nothing_binds():
    assert first_present({}, "price", "cost", default="unset") == "unset"


# --------------------------------------------------------------------------- #
# Tri-state selfcheck contract — ok flag and aggregation precedence
# --------------------------------------------------------------------------- #


def test_selfcheck_entry_ok_flag_is_tri_state():
    """ok=True only for healthy; False for drift; None for inconclusive, so a
    caller can tell 'proved broken' from 'could not be judged'."""
    assert selfcheck_entry("healthy")["ok"] is True
    assert selfcheck_entry("drift")["ok"] is False
    assert selfcheck_entry("inconclusive")["ok"] is None


def test_selfcheck_entry_unknown_state_is_inconclusive_never_ok():
    entry = selfcheck_entry("exploded")
    assert entry["state"] == "inconclusive"
    assert entry["ok"] is None


def test_selfcheck_result_drift_dominates():
    checks = {
        "search": selfcheck_entry("healthy"),
        "card": selfcheck_entry("drift"),
        "other": selfcheck_entry("inconclusive"),
    }
    result = selfcheck_result("x", checks)
    assert result["status"] == "drift_detected"
    assert result["healthy"] is False


def test_selfcheck_result_inconclusive_beats_all_healthy():
    checks = {"search": selfcheck_entry("healthy"), "card": selfcheck_entry("inconclusive")}
    result = selfcheck_result("x", checks)
    assert result["status"] == "inconclusive"
    assert result["healthy"] is None


def test_selfcheck_result_all_healthy_is_success():
    checks = {"search": selfcheck_entry("healthy"), "card": selfcheck_entry("healthy")}
    result = selfcheck_result("x", checks)
    assert result["status"] == "success"
    assert result["healthy"] is True


def test_selfcheck_result_no_checks_is_inconclusive_not_vacuous_success():
    result = selfcheck_result("x", {})
    assert result["status"] == "inconclusive"
    assert result["healthy"] is None


def test_selfcheck_result_missing_required_check_is_injected_inconclusive():
    """A required sub-check the caller forgot to populate must not be silently
    dropped — it is injected as inconclusive so it can never aggregate to a
    false success."""
    result = selfcheck_result("x", {"search": selfcheck_entry("healthy")}, required=("search", "card"))
    assert "card" in result["checks"]
    assert result["checks"]["card"]["state"] == "inconclusive"
    assert result["status"] == "inconclusive"


# --- validate_review_block: small all-empty samples are not drift (issue #111) ---


def _rating_only(n: int) -> dict:
    return {"returned": n, "reviews": [{"text": "", "pros": "", "cons": "", "rating": 5}] * n}


def test_small_rating_only_sample_is_not_drift() -> None:
    assert validate_review_block(_rating_only(3)) == []


def test_large_rating_only_sample_is_drift() -> None:
    warnings = validate_review_block(_rating_only(REVIEW_TEXT_DRIFT_MIN_SAMPLE))
    assert len(warnings) == 1
    assert warnings[0].startswith("all_review_texts_empty:")


def test_large_sample_with_some_text_is_healthy() -> None:
    block = _rating_only(REVIEW_TEXT_DRIFT_MIN_SAMPLE)
    block["reviews"] = [*block["reviews"][:-1], {"text": "ok"}]
    assert validate_review_block(block) == []
