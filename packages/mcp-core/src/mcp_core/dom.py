"""Shared JavaScript for the CDP text-scraping tiers, plus its Python side.

Four connectors (Citilink, DNS, Lamoda, Taobao) read prices out of a rendered
page rather than an API. They had each grown their own copy of the same two
heuristics, and the same two bugs with them. Audit 2026-07-28 found both against
live pages:

**Tile resolution via ``closest()``.** Every extractor located a tile by taking a
product anchor and calling
``closest('[class*="catalog-product"], [class*="product"], [class*="item"], li, div')``.
``closest()`` tests the element itself before any ancestor, so on DNS — whose
first product anchor is the image link with class ``catalog-product__image-link``
— the "tile" resolved to that anchor. Measured on the captured DNS grid: the
resolved node had ``textContent`` length 0 against the real tile's 402. Result:
``title=null`` and ``price=null`` for every product on a page that rendered fine.
The bare ``div`` in that selector list makes the same collapse possible on any
site, because a wrapper div is always nearer than the tile.

**Price via ``Math.min`` over currency-bearing lines.** Prices were found by
filtering ``innerText`` lines through ``/руб|₽/`` and taking the smallest number:

  * Citilink renders the ₽ glyph in a *separate element* from the digits
    (verified live), so no line ever matched and the price came back null.
  * DNS tiles carry "от 5 751 ₽/ мес." beside a 58 999 ₽ price, so the minimum
    is the monthly instalment. That is worse than a null — it validates, it looks
    plausible, and nothing downstream can tell it is wrong.
  * ``parseFloat(line.replace(/[^0-9.]/g, ''))`` concatenates every digit on a
    line, so "58 999 ₽54 999" became 5899954999.

So this module does no arithmetic in the browser. It returns raw display strings
from the DOM and leaves parsing to :func:`mcp_core.resilience.coerce_price`,
which already understands non-breaking spaces, a missing currency glyph and
comma decimals — and refuses an ambiguous multi-number blob instead of inventing
a value. It also reads ``textContent`` rather than ``innerText``: ``innerText``
depends on layout and CSS visibility, which differs between a warm tab and a
freshly navigated one, and is unavailable in DOM test harnesses.

``JS_HELPERS`` is prepended to a connector's extractor body. It defines:

``cleanText(el)``
    Whitespace-collapsed ``textContent``, or null.
``cleanTextWithout(el, selectors)``
    Same, with matching descendants removed first — so a strikethrough price
    nested inside the current-price node cannot glue itself on.
``tileRootFor(anchor, linkPattern)``
    The OUTERMOST ancestor still covering exactly one product id. No
    ``closest()``, so an anchor whose own class looks tile-ish cannot win.
``priceTextsIn(el)``
    Price-shaped strings from the subtree, skipping instalment, bonus and
    discount-badge nodes, nearest-to-the-currency-glyph first.
"""

from __future__ import annotations

import re
from typing import Any

from mcp_core.resilience import coerce_price, flatten_text

# Text markers for a node that holds a number which is NOT the price: monthly
# instalments, loyalty bonuses, discount badges, delivery-point counts, ratings.
#
# ONE list, and the JavaScript regex below is generated from it. Writing the
# words twice — once here, once inline in the JS — is how the two quietly stop
# agreeing: someone adds "трейд-ин" to the Python tuple, the browser never sees
# it, and the bug reads as a mysterious wrong price rather than a stale list.
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
    "%",
)


def _decoy_regex_literal() -> str:
    """The DECOY_MARKERS as a JavaScript regex literal.

    Escaped so a marker containing regex punctuation (``%``, ``-``) cannot change
    the pattern's meaning.
    """
    escaped = "|".join(re.escape(marker) for marker in DECOY_MARKERS)
    return f"/{escaped}/i"


# Currency glyphs that attach a number to a price: the rouble sign, the rouble
# word, and the two yuan signs (full- and half-width) used by Taobao. ONE list,
# and the JavaScript regex below is generated from it — for the same reason
# DECOY_MARKERS is: editing a glyph here but not in the JS would read as a
# mysterious null price on the site that uses it.
_PRICE_GLYPHS: tuple[str, ...] = ("₽", "руб", "¥", "￥")


def _price_glyph_literal() -> str:
    """The _PRICE_GLYPHS as a JavaScript regex literal, escaped like the decoys."""
    escaped = "|".join(re.escape(glyph) for glyph in _PRICE_GLYPHS)
    return f"/{escaped}/i"


def _glyph_remainder_literal() -> str:
    """JavaScript regex for the sibling-text check: what remains next to the
    digits is only the currency glyph.

    Covers both shapes in _PRICE_GLYPHS: single-character glyphs (``₽ ¥ ￥``)
    as a character class, and the ``руб``/``руб.`` word with an optional
    trailing dot. Generated from the same list as HAS_GLYPH so the two cannot
    silently disagree about what a price is.
    """
    single = "".join(glyph for glyph in _PRICE_GLYPHS if len(glyph) == 1)
    words = [glyph for glyph in _PRICE_GLYPHS if len(glyph) > 1]
    alternatives: list[str] = []
    if single:
        alternatives.append(f"^[{re.escape(single)}\\s.]*$")
    for word in words:
        alternatives.append(f"^{re.escape(word)}\\.?$")
    return "|".join(alternatives)


JS_HELPERS = (
    """
    const DECOY_RE = __DECOY_RE__;
    const HAS_GLYPH = __HAS_GLYPH__;
    const GLYPHS_REMAINDER = /__GLYPHS_REMAINDER__/i;
""".replace("__DECOY_RE__", _decoy_regex_literal())
    .replace("__HAS_GLYPH__", _price_glyph_literal())
    .replace("__GLYPHS_REMAINDER__", _glyph_remainder_literal())
    + r"""

    const cleanText = (el) => {
        if (!el) return null;
        const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
        return t || null;
    };



    const cleanTextWithout = (el, dropSelectors) => {
        if (!el) return null;
        const copy = el.cloneNode(true);
        for (const sel of (dropSelectors || [])) {
            for (const node of copy.querySelectorAll(sel)) node.remove();
        }
        const t = (copy.textContent || '').replace(/\s+/g, ' ').trim();
        return t || null;
    };

    // The tile is the OUTERMOST ancestor that still covers exactly one product
    // id. Walking up beyond that starts swallowing neighbouring tiles; stopping
    // short (or using closest(), which tests the element itself first) lands on
    // a decorative wrapper with no text.
    const tileRootFor = (anchor, linkPattern) => {
        const idOf = (el) => {
            const href = el.getAttribute('href') || el.href || '';
            const m = href.match(linkPattern);
            return m ? m[1] : null;
        };
        let node = anchor, best = anchor;
        for (let hop = 0; hop < 10 && node.parentElement; hop++) {
            node = node.parentElement;
            const ids = new Set();
            for (const a of node.querySelectorAll('a[href]')) {
                const id = idOf(a);
                if (id) ids.add(id);
            }
            if (ids.size > 1) break;
            best = node;
        }
        return best;
    };

    // Price-shaped strings from a subtree, split by how strongly each is tied to
    // a currency glyph.
    //
    //   attached — the node's own text carries the glyph, OR its immediate parent
    //              holds nothing but this text plus the glyph. That second case is
    //              Citilink, which renders <span>59 990</span><span>₽</span>: the
    //              digits and the glyph are siblings, so requiring them on one
    //              text line (as the old filter did) matched nothing at all.
    //   other    — a price-shaped number with no glyph nearby. Usually the
    //              strikethrough, which DNS renders bare as "54 999", so it is
    //              kept as an old-price candidate but never chosen as THE price.
    //
    // A bare number under three digits is dropped unless it carries the glyph:
    // "(от 8 дней)" and a "4.78" rating are not prices, and letting them
    // through is how a delivery estimate becomes a product price.
    const priceTextsIn = (root) => {
        if (!root) return {attached: [], other: []};
        const attached = [];
        const other = [];
        const nodes = [root, ...root.querySelectorAll('*')];


        for (const el of nodes) {
            // Leaf nodes only — a container repeats its children's digits and
            // produces an ambiguous multi-number blob. The exception is a
            // container that carries its OWN direct text beside the glyph
            // ("5 990 <span>₽</span>" with the digits as a bare text node):
            // dropping those would lose a real price, and the blob they can
            // form is a single number, not several.
            if (el.children.length > 0) {
                let ownText = '';
                for (const child of el.childNodes) {
                    if (child.nodeType === 3) ownText += child.textContent;
                }
                if (!ownText.replace(/\s+/g, '')) continue;
            }
            const raw = (el.textContent || '').replace(/\s+/g, ' ').trim();
            if (!raw || raw.length > 40) continue;
            if (!/\d/.test(raw)) continue;
            if (DECOY_RE.test(raw)) continue;

            const ownGlyph = HAS_GLYPH.test(raw);
            let parentGlyph = false;
            const parent = el.parentElement;
            if (!ownGlyph && parent) {
                const parentText = (parent.textContent || '').replace(/\s+/g, ' ').trim();
                // Strip this node's text once; whatever remains must be only the
                // glyph for the pair to count as a single price.
                const remainder = parentText.replace(raw, '').replace(/\s+/g, ' ').trim();
                parentGlyph = HAS_GLYPH.test(parentText) && GLYPHS_REMAINDER.test(remainder);


            }
            const digits = (raw.match(/\d/g) || []).length;
            if (!ownGlyph && !parentGlyph && digits < 3) continue;

            if (ownGlyph || parentGlyph) attached.push(raw);
            else other.push(raw);
        }
        const dedupe = (arr) => {
            const seen = new Set();
            const out = [];
            for (const t of arr) {
                if (seen.has(t)) continue;
                seen.add(t);
                out.push(t);
            }
            return out;
        };
        return {attached: dedupe(attached), other: dedupe(other)};
    };
"""
)


def prices_from_tile(tile: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return ``(price, old_price)`` for one extracted tile.

    Reads the shapes the shared extractor emits (``price_text``,
    ``old_price_text``, ``price_texts``) and also the numeric ``price_rub`` /
    ``old_price_rub`` an older build wrote, so a cache entry that outlives a
    deploy does not start answering with nulls.

    ``price_texts`` is an ordered list of candidates; the first that parses to a
    real positive price wins, and the largest remaining candidate above it is
    treated as the strikethrough. A candidate list is never averaged or
    min-ed — that guesswork is what produced instalment prices.
    """
    # `price_meta` is a machine-readable numeric attribute the site publishes for
    # its own analytics (Citilink: data-meta-price="59990"). When present it needs
    # no parsing and cannot be confused with an instalment or a badge, so it wins.
    price = coerce_price(tile.get("price_meta"))
    if price is None:
        price = coerce_price(tile.get("price_text"))
    if price is None:
        price = coerce_price(tile.get("price_rub"))

    def _parsed(raw: Any) -> list[float]:
        out: list[float] = []
        if isinstance(raw, (list, tuple)):
            for candidate in raw:
                parsed = coerce_price(candidate)
                if parsed is not None:
                    out.append(parsed)
        return out

    texts = tile.get("price_texts")
    if isinstance(texts, dict):
        attached = _parsed(texts.get("attached"))
        other = _parsed(texts.get("other"))
    else:
        # A flat list (or an older payload) carries no glyph information, so every
        # entry is treated as a weak candidate rather than trusted as THE price.
        attached = []
        other = _parsed(texts)

    # THE price must be glyph-attached. A bare number elsewhere in the tile is a
    # strikethrough, a rating or a delivery estimate — never promoted to price,
    # because a plausible wrong price is the failure mode that costs most.
    if price is None and attached:
        price = attached[0]

    old_price = coerce_price(tile.get("old_price_text"))
    if old_price is None:
        old_price = coerce_price(tile.get("old_price_rub"))
    if old_price is None and price is not None:
        above = [c for c in (*attached[1:], *other) if c > price]
        if above:
            old_price = max(above)

    # A strikethrough at or below the current price is a drifted read, not a
    # discount. Drop it rather than publish a nonsensical pair.
    if price is not None and old_price is not None and old_price <= price:
        old_price = None
    return price, old_price


def title_from_tile(tile: dict[str, Any]) -> str | None:
    """Title of an extracted tile, or an honest None."""
    return flatten_text(tile.get("title"))
