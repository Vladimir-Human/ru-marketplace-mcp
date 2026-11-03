"""Prepublication fixture hygiene requirement (owner, 2026-11-03).

START_MODULE_CONTRACT
PURPOSE: Reject signed advertising/tracking query fields in retained HTML fixture URLs.
SCOPE: Local fixture data only; never print URLs/tokens, alter fixtures or contact Lamoda.
DEPENDS: pytest; stdlib html,pathlib,re,urllib.parse.
LINKS: M-LAMODA; R-LAMODA-FIXTURE-URL-HYGIENE (owner prepublication requirement).
END_MODULE_CONTRACT
START_MODULE_MAP
  _tracking_keys     extract only forbidden query key names from fixture URL spellings
  test_*            generic retained-fixture check and synthetic detection controls
END_MODULE_MAP
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FORBIDDEN_KEYS = frozenset({"ad_params", "promotion_provider_id"})

# START_BLOCK_FIXTURE_URL_HYGIENE


def _tracking_keys(text: str) -> set[str]:
    """Return key names only, never fixture URLs or their signed values."""
    normalized = html.unescape(text.replace(r"\/", "/").replace(r"\u0026", "&").replace(r"\u003d", "="))
    found = set()
    for url in re.findall(r"(?:https?://|/)[^\s\"'<>]+", normalized):
        try:
            query = urlsplit(url).query
        except ValueError:
            # HTML also contains non-URL slash expressions; never quote them.
            continue
        for key, _ in parse_qsl(query, keep_blank_values=True):
            if key in FORBIDDEN_KEYS:
                found.add(key)
    return found


def test_retained_html_fixture_urls_have_no_signed_advertising_tracking():
    """R-LAMODA-FIXTURE-URL-HYGIENE: scan all captures without exposing tokens."""
    fixtures = sorted(FIXTURES.rglob("*.html"))
    assert fixtures, "the HTML regression captures must remain present"
    assert (FIXTURES / "search_grid_live.html").is_file(), "retain the live-grid regression capture"
    for fixture in fixtures:
        forbidden = _tracking_keys(fixture.read_text(encoding="utf-8"))
        # Only the fixture name and fixed key vocabulary enter failure output.
        assert not forbidden, f"{fixture.name}: remove tracking query keys {', '.join(sorted(forbidden))}"


@pytest.mark.parametrize(
    "markup,expected",
    [
        ('<a href="https://www.lamoda.ru/p/example/?ad_params=synthetic">x</a>', {"ad_params"}),
        ('<a href="/p/example/?size=42&amp;promotion_provider_id=synthetic">x</a>', {"promotion_provider_id"}),
        (r'{"url":"https:\/\/www.lamoda.ru/p/example/?size=42\u0026ad_params=synthetic"}', {"ad_params"}),
        ('<a href="/p/example/?size=42&color=black">x</a>', set()),
        ('<a href="https://www.lamoda.ru/p/example/">x</a>', set()),
    ],
    ids=["absolute-ad-url", "relative-html-escaped", "json-escaped", "ordinary-query", "clean-url"],
)
def test_fixture_hygiene_detection_controls(markup, expected):
    """The data-only hygiene check detects poison and preserves ordinary URLs."""
    assert _tracking_keys(markup) == expected


# END_BLOCK_FIXTURE_URL_HYGIENE
