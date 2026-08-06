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
    coerce_int,
    coerce_price,
    coerce_rating,
    first_present,
    flatten_text,
    price_from_texts,
    selfcheck_entry,
    selfcheck_result,
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
