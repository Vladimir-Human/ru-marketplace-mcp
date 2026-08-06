"""Tests for the shared DOM-extraction layer.

``mcp_core.dom`` carries four connectors (Citilink, DNS, Lamoda, Taobao): the
JavaScript that finds a product tile, and the Python that decides which of the
numbers on that tile is the price. Both halves shipped bugs in July 2026, and
both were untested — the connectors' own suites mock the render call away.

The rule these tests encode, in one line: **a price is a number attached to a
currency glyph.** Everything else on a tile — an instalment, a bonus, a discount
badge, a delivery estimate, a rating — is a number that must never be promoted.
Getting that wrong does not produce a visible failure; it produces a plausible
wrong price, which is the expensive kind.
"""

from __future__ import annotations

import re

from mcp_core.dom import DECOY_MARKERS, JS_HELPERS, prices_from_tile, title_from_tile

# ---------------------------------------------------------------------------
# The decoy list and the JavaScript regex must not be able to disagree.
# ---------------------------------------------------------------------------


def test_the_js_decoy_regex_is_generated_from_the_python_list() -> None:
    """One list, two consumers. Hand-writing the regex is how they drift apart."""
    match = re.search(r"const DECOY_RE = /(.+?)/i;", JS_HELPERS)
    assert match, "the shared helpers no longer define DECOY_RE"
    alternatives = match.group(1).split("|")
    assert len(alternatives) == len(DECOY_MARKERS), (
        f"DECOY_RE has {len(alternatives)} alternatives but DECOY_MARKERS has {len(DECOY_MARKERS)} — "
        "the regex was hand-edited instead of generated"
    )
    for marker in DECOY_MARKERS:
        assert re.escape(marker) in alternatives, f"{marker!r} is in DECOY_MARKERS but not in the generated regex"


def test_regex_punctuation_in_a_marker_cannot_change_the_pattern() -> None:
    """`%` and `-` are markers; unescaped they would alter the regex's meaning."""
    match = re.search(r"const DECOY_RE = /(.+?)/i;", JS_HELPERS)
    assert match
    pattern = match.group(1)
    # A bare `-` inside an alternation is harmless, but the escaping must be
    # present and consistent so a future marker like `[скидка]` stays literal.
    assert r"trade\-in" in pattern or "trade-in" in pattern
    re.compile(pattern)  # must still be a valid pattern after generation


def test_the_shared_helpers_expose_what_connectors_rely_on() -> None:
    for helper in ("cleanText", "cleanTextWithout", "tileRootFor", "priceTextsIn"):
        assert f"const {helper} =" in JS_HELPERS, f"{helper} vanished from the shared helpers"


def test_the_shared_helpers_never_reintroduce_closest() -> None:
    """`closest()` tests the element itself first — the DNS tile bug in one call."""
    code = "\n".join(line.split("//")[0] for line in JS_HELPERS.splitlines())
    assert ".closest(" not in code


def test_the_shared_helpers_read_text_content_not_inner_text() -> None:
    """innerText depends on layout, differs between tabs, and is absent in jsdom."""
    code = "\n".join(line.split("//")[0] for line in JS_HELPERS.splitlines())
    assert "innerText" not in code
    assert "textContent" in code


def test_the_price_glyph_regex_is_generated_from_the_python_list() -> None:
    """One list of currency glyphs feeds HAS_GLYPH and the pixel check alike."""
    from mcp_core.dom import _PRICE_GLYPHS

    match = re.search(r"const HAS_GLYPH = /(.+?)/i;", JS_HELPERS)
    assert match, "the shared helpers no longer define HAS_GLYPH"
    alternatives = match.group(1).split("|")
    assert len(alternatives) == len(_PRICE_GLYPHS), (
        f"HAS_GLYPH has {len(alternatives)} alternatives but _PRICE_GLYPHS has {len(_PRICE_GLYPHS)}"
    )
    for glyph in _PRICE_GLYPHS:
        assert re.escape(glyph) in alternatives, f"{glyph!r} is in _PRICE_GLYPHS but not in HAS_GLYPH"


def test_the_price_glyph_list_covers_rouble_and_yuan() -> None:
    """Rouble signs for DNS/Citilink/Lamoda, both yuan glyphs for Taobao."""
    from mcp_core.dom import _PRICE_GLYPHS

    assert "₽" in _PRICE_GLYPHS
    assert "руб" in _PRICE_GLYPHS
    assert "¥" in _PRICE_GLYPHS
    assert "￥" in _PRICE_GLYPHS


def test_yuan_glue_counts_as_a_price() -> None:
    """Taobao renders "999¥" / "¥129.00" with the glyph glued to the digits."""
    price, old = prices_from_tile({"price_text": "999¥"})
    assert price == 999.0
    assert old is None
    assert prices_from_tile({"price_text": "¥129.00"})[0] == 129.0
    assert prices_from_tile({"price_text": "￥1 299"})[0] == 1299.0


def test_yuan_glyph_in_a_sibling_element_counts_as_a_price() -> None:
    """The same split-glyph shape as Citilink: digits and ¥ rendered apart."""
    price, _old = prices_from_tile({"price_texts": {"attached": ["59.90"], "other": []}})
    assert price == 59.9


# ---------------------------------------------------------------------------
# Price selection. These are the strings the live sites actually serve.
# ---------------------------------------------------------------------------


def test_glyph_attached_number_is_the_price() -> None:
    price, old = prices_from_tile({"price_texts": {"attached": ["58 999 ₽"], "other": []}})
    assert price == 58999.0
    assert old is None


def test_a_bare_number_alone_is_never_promoted_to_price() -> None:
    """Citilink's strikethrough and DNS's `.product-buy__prev` are both bare.

    Reporting one as the price would be indistinguishable downstream from a
    correct read. None is the honest answer.
    """
    price, old = prices_from_tile({"price_texts": {"attached": [], "other": ["66 990"]}})
    assert price is None
    assert old is None


def test_bare_number_above_the_price_becomes_the_strikethrough() -> None:
    price, old = prices_from_tile({"price_texts": {"attached": ["59 990₽"], "other": ["66 990"]}})
    assert price == 59990.0
    assert old == 66990.0


def test_a_strikethrough_below_the_price_is_dropped_not_reported() -> None:
    """An "old" price under the current one is a drifted read, not a discount."""
    price, old = prices_from_tile({"price_texts": {"attached": ["58 999 ₽"], "other": ["1 000"]}})
    assert price == 58999.0
    assert old is None


def test_the_exact_meta_attribute_wins_over_display_text() -> None:
    """`data-meta-price` is the site's own number: no parsing, no ambiguity."""
    price, _old = prices_from_tile({"price_meta": "59990", "price_text": "59 990₽"})
    assert price == 59990.0


def test_a_broken_meta_attribute_falls_back_to_the_display_text() -> None:
    price, _old = prices_from_tile({"price_meta": "", "price_text": "55 170₽"})
    assert price == 55170.0


def test_an_ambiguous_blob_is_refused_rather_than_concatenated() -> None:
    """ "58 999 ₽54 999" must not become 5899954999."""
    price, _old = prices_from_tile({"price_text": "58 999 ₽54 999"})
    assert price is None


def test_zero_is_not_a_price() -> None:
    """A dead listing at 0 would rank cheapest in every comparison."""
    price, _old = prices_from_tile({"price_text": "0 ₽"})
    assert price is None


def test_a_cache_entry_from_an_older_build_still_maps() -> None:
    """Numeric payloads predate the text shape; they must not start reading null."""
    price, old = prices_from_tile({"price_rub": 58999.0, "old_price_rub": 64999.0})
    assert price == 58999.0
    assert old == 64999.0


def test_a_flat_candidate_list_is_treated_as_weak() -> None:
    """Without glyph information there is no evidence which number is the price."""
    price, _old = prices_from_tile({"price_texts": ["59 990"]})
    assert price is None


def test_an_empty_tile_yields_no_price_and_no_crash() -> None:
    assert prices_from_tile({}) == (None, None)
    assert prices_from_tile({"price_texts": {"attached": [], "other": []}}) == (None, None)


def test_meta_attribute_beats_a_numeric_price_rub_field() -> None:
    """price_meta is the site's own machine-readable number; price_rub is a
    legacy/cache value. When both are present, meta must win — preferring
    price_rub would silently publish the stale number."""
    price, _old = prices_from_tile({"price_meta": "59990", "price_rub": 12345.0})
    assert price == 59990.0


def test_a_strikethrough_equal_to_the_price_is_dropped() -> None:
    """old == price is a drifted read, not a discount — the same promise as the
    below-price case. Candidate lists are pre-filtered with a strict '>', so the
    equal-value case only reaches the guard through the explicit old-price field."""
    price, old = prices_from_tile({"price_text": "58 999 ₽", "old_price_text": "58 999"})
    assert price == 58999.0
    assert old is None


def test_the_largest_candidate_above_the_price_is_the_strikethrough() -> None:
    """With several higher candidates the strikethrough is the LARGEST — picking
    the smallest would publish a discount that was never on the page."""
    price, old = prices_from_tile({"price_texts": {"attached": ["59 990₽"], "other": ["66 990", "71 990"]}})
    assert price == 59990.0
    assert old == 71990.0


def test_a_flat_candidate_list_still_feeds_the_strikethrough() -> None:
    """Older payloads carry a flat candidate list. Its entries are weak — never
    the price — but they must still be eligible as strikethrough candidates."""
    price, old = prices_from_tile({"price_text": "52 999 ₽", "price_texts": ["62 999"]})
    assert price == 52999.0
    assert old == 62999.0


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def test_title_passes_through_and_trims() -> None:
    assert title_from_tile({"title": "  Ноутбук HUAWEI  "}) == "Ноутбук HUAWEI"


def test_missing_title_is_none_not_empty_string() -> None:
    assert title_from_tile({}) is None
    assert title_from_tile({"title": "   "}) is None
