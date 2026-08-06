"""Property tests for the coercion helpers: contracts about EVERY input.

Example tests sample the input space; the promises these helpers make — "never
0 where the meaning is 'no value'", "ambiguous input is None, never a plausible
guess", "no input ever raises" — are quantified over all inputs, so they need
quantified checks. hypothesis generates the space.

The doctrine under test (CONTRIBUTING / ARCHITECTURE):

* a missing value is ``None``, never ``0`` — a zero price ranks a dead listing
  as the cheapest option;
* fall loud, not plausible — an ambiguous shape is ``None``, not a guess;
* helpers are total — one poisoned cell must degrade a field, not abort a tool.
"""

from __future__ import annotations

import math
import re

from hypothesis import given
from hypothesis import strategies as st
from mcp_core.resilience import coerce_int, coerce_price, first_present, flatten_text

# Full unicode text, plus numbers including the wire-poisoned forms (NaN/inf
# survive json.loads by default, so the helpers really do receive them).
_any_scalar = st.one_of(st.text(), st.integers(), st.floats(allow_nan=True, allow_infinity=True), st.none())


# ------------------------------------------------------------- coerce_price ----


@given(value=_any_scalar)
def test_coerce_price_is_total_and_never_zero(value):
    """The whole contract in one property: never raises, and the result is
    either None or a real positive price — never 0.0, never negative, never
    NaN/inf leaking through to a comparison."""
    result = coerce_price(value)

    assert result is None or (isinstance(result, float) and math.isfinite(result) and result > 0)


@given(a=st.integers(min_value=1, max_value=10**9), b=st.integers(min_value=1, max_value=10**9))
def test_coerce_price_refuses_two_numbers_even_without_a_glyph_between_them(a, b):
    """A price and its instalment/discount neighbour, kept apart only by
    anything that breaks a digit run. Concatenating them is the fabrication
    these helpers exist to prevent."""
    for glue in (" ₽ ", " руб. ", " / ", " и "):
        assert coerce_price(f"{a}{glue}{b}") is None


@given(
    whole=st.integers(min_value=0, max_value=10**12),
    kopecks=st.integers(min_value=0, max_value=99),
)
def test_coerce_price_is_idempotent_on_its_own_results(whole, kopecks):
    """A parsed price, rendered back to a display string, must re-parse to
    itself — otherwise a cache round-trip quietly changes the number. Prices
    carry at most two fractional digits, and anything below 1e16 renders
    without scientific notation, so str() is a faithful display form."""
    value = whole + kopecks / 100
    result = coerce_price(value)
    if result is None:
        return
    assert coerce_price(str(result)) == result


@given(
    value=st.one_of(
        st.just(""),
        st.just("0"),
        st.just("0 ₽"),
        st.just("-10"),
        st.just("-10 ₽"),
        st.just(0),
        st.just(-10),
        st.just(float("nan")),
        st.just(float("inf")),
        st.just(-0.0),
    )
)
def test_coerce_price_dead_listing_inputs_are_none(value):
    """Every shape a dead or poisoned listing can present — all of them None."""
    assert coerce_price(value) is None


def test_coerce_price_is_total_beyond_the_float_ceiling():
    """Regression for the hole the properties surfaced but their sampling never
    hit: a digit run past ~1.8e308 raised OverflowError instead of degrading.
    One poisoned cell must cost a field, not abort the whole tool."""
    assert coerce_price("9" * 400) is None
    assert coerce_price(10**400) is None
    # 309 ones still fit in a float — the guard must not eat in-range values.
    assert coerce_price("1" * 309) is not None


# -------------------------------------------------------------- coerce_int ----


@given(value=_any_scalar)
def test_coerce_int_is_total(value):
    """One poisoned cell degrades a field, never aborts the tool."""
    result = coerce_int(value)

    assert result is None or isinstance(result, int)


@given(s=st.text())
def test_coerce_int_without_digits_is_none_never_zero(s):
    """No digits anywhere means no value — and 'no value' is None, never 0."""
    if re.search(r"\d", s):
        return
    assert coerce_int(s) is None


@given(s=st.text())
def test_coerce_int_with_letters_is_ambiguous_and_none(s):
    """A letter means a unit/magnitude suffix ('1.2K', '15 тыс.') — digit
    concatenation would fabricate a count, so the answer is None."""
    if not re.search(r"[A-Za-zА-Яа-я]", s):
        return
    assert coerce_int(s) is None


@given(n=st.integers())
def test_coerce_int_passes_ints_through(n):
    assert coerce_int(n) == n


@given(groups=st.lists(st.from_regex(r"[0-9]{1,3}", fullmatch=True), min_size=1, max_size=4))
def test_coerce_int_parses_grouped_display_strings(groups):
    """'24 088', '1 057' — thousands separators are unambiguous and must
    parse to the concatenated value, whichever space flavour the site uses."""
    for space in (" ", "\u2009", "\u00a0"):
        assert coerce_int(space.join(groups)) == int("".join(groups))


# ------------------------------------------------------------- first_present ----


@given(d=st.dictionaries(st.text(), _any_scalar), aliases=st.lists(st.text(), max_size=5))
def test_first_present_is_total_and_never_invents(d, aliases):
    """The answer is always either one of the dict's own values or the
    default — a renamed field must never produce a fabricated one."""
    result = first_present(d, *aliases)

    assert result is None or result in set(d.values()) or result in {d.get(key) for key in aliases}


@given(aliases=st.lists(st.text(min_size=1), min_size=1, max_size=4, unique=True))
def test_first_present_all_absent_or_null_is_default(aliases):
    d = dict.fromkeys(aliases)

    assert first_present(d, *aliases) is None
    assert first_present(d, *aliases, default="fallback") == "fallback"


@given(aliases=st.lists(st.text(min_size=1), min_size=2, max_size=4, unique=True))
def test_first_present_returns_the_first_non_null_alias(aliases):
    """The first alias with a present value wins — even when it is an empty
    string, which is present by contract (the caller coerces, not this helper)."""
    d = dict.fromkeys(aliases)
    d[aliases[0]] = ""

    assert first_present(d, *aliases) == ""


# -------------------------------------------------------------- flatten_text ----


_nested = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text()),
    lambda children: st.one_of(st.lists(children, max_size=4), st.dictionaries(st.text(), children, max_size=4)),
    max_leaves=10,
)


@given(value=_nested)
def test_flatten_text_is_total_and_honest(value):
    """Never raises, and the result is either None or a usable display string:
    non-empty, with no edge whitespace."""
    result = flatten_text(value)

    if result is None:
        return
    assert isinstance(result, str)
    assert result != ""
    assert result == result.strip()


def test_flatten_text_never_returns_a_container_repr():
    """The crash that started this helper: a nested object must degrade to a
    name or to None — never ``str(dict)`` with Python syntax in front of a
    user, and never a crash."""
    assert flatten_text({"id": 637640, "name": "Москва"}) == "Москва"
    assert flatten_text({"id": 637640}) is None
    assert flatten_text([{"id": 1}, "Товар"]) == "Товар"


@given(value=st.booleans())
def test_flatten_text_refuses_booleans(value):
    """'True' is not a display value; flattening a bool would put Python
    literals in front of a user."""
    assert flatten_text(value) is None
