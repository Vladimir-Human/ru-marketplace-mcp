"""Reference shape signature for the Taobao search extractor.

Generated from the LIVE capture (``tests/fixtures/search_grid_live.html``,
captured 2026-08-07 through the operator's logged-in Chrome; provenance
recorded alongside) by running the real extractor over it and fingerprinting
the payload with ``mcp_core.resilience.shape_signature``. Re-measured
2026-09-10 (login-wall triage) after the extractor gained the structural
wall-marker fields (``anchors_total`` / ``login_anchors`` / ``body_snippet``)
by re-running the same extractor over the same committed capture. Two
consumers:

* ``taobao_selfcheck`` compares a live payload against
  ``SEARCH_REQUIRED_FAMILIES`` and reports drift with paths when a
  parser-critical family of keys vanishes;
* ``tests/test_shape_reference.py`` asserts the reference still agrees with
  the live fixture, so the registry cannot go stale silently.

Never edit by hand: regenerate by re-running the extractor over a fresh
captured page and replacing the fingerprint.
"""

from __future__ import annotations

from collections.abc import Iterable

SEARCH_SHAPE_REFERENCE: tuple[str, ...] = (
    "anchors_total:int",
    "body_snippet:str",
    "items[].item_id:str",
    "items[].location:str",
    "items[].price_texts.attached[]:str",
    "items[].price_texts.other:empty_array",
    "items[].price_texts.other[]:str",
    "items[].sales:str",
    "items[].shop_name:str",
    "items[].title:str",
    "items[].url:str",
    "login_anchors:int",
    "title:str",
)

# Key families the parser reads, as alternatives: the price binds through
# every shape the mapper accepts (the glyph-attached candidate list the
# current extractor emits, or the numeric ``price_cny`` an older build
# cached), so drift means the WHOLE family vanished, not a single key.
SEARCH_REQUIRED_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("items[].item_id",),
    ("items[].title",),
    (
        "items[].price_texts.attached",
        "items[].price_cny",
    ),
    ("items[].url",),
    # The structural login-wall markers (2026-09-10): the wall classifier
    # reads all three, and losing them silently degrades detection to the
    # title-only check that missed the EMPTY-title wall variant
    # (tests/fixtures/search_login_wall_live.html). Each is its own family —
    # anchors_total gates the other two markers, so any one vanishing is
    # parser-critical drift and must be loud.
    ("anchors_total",),
    ("login_anchors",),
    ("body_snippet",),
)


def _family_present(family: tuple[str, ...], entries: Iterable[str]) -> bool:
    """A family is present when some signature entry extends one of its paths.

    Entries continue a path with ``:type``, ``[...]`` or ``.nested`` — check
    all three, otherwise an array segment like ``attached[]`` never matches.
    """
    for path in family:
        for entry in entries:
            for sep in (":", "[", "."):
                if entry.startswith(f"{path}{sep}"):
                    return True
    return False


def missing_required_families(signature: Iterable[str]) -> list[tuple[str, ...]]:
    """The required families that have no member present in the signature."""
    entries = list(signature)
    return [family for family in SEARCH_REQUIRED_FAMILIES if not _family_present(family, entries)]
