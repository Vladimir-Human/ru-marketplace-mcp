"""The item-path regex accepts every captured Avito path — and only ASCII ones.

The item-path guard used to accept any character except a slash inside the
slug segments, while the comment next to it promised «a plain ASCII slug».
Every Avito item path the captures hold really is a plain ASCII slug, so the
regex may require exactly that: a Cyrillic or otherwise exotic segment never
reaches the operator's logged-in browser. This file pins that property to the
captures themselves, so a future capture that needs a wider shape fails here
instead of quietly widening the guard.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import pytest
from avito_connector import server

FIXTURES = Path(__file__).parent / "fixtures"

# The narrow form the comment promises: every segment is a plain ASCII slug.
_ASCII_SLUG_RE = re.compile(r"[A-Za-z0-9_-]+")
# The item id is the numeric tail of the last segment (…_4612345678).
_ITEM_ID_TAIL_RE = re.compile(r"(?:.*_)?(\d{6,})")
_AVITO_URL_RE = re.compile(r"https?://(?:www\.)?avito\.ru(/[^\s\"'<>\\]*)")


def _captured_paths() -> set[str]:
    """Every avito.ru path the captured fixtures mention."""
    paths: set[str] = set()
    for sidecar in sorted(FIXTURES.glob("*.provenance.json")):
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        url = str(data.get("url") or "")
        if "avito.ru" in url:
            paths.add(urllib.parse.urlsplit(url).path)
    for page in sorted(FIXTURES.glob("*.html")):
        for match in _AVITO_URL_RE.finditer(page.read_text(encoding="utf-8")):
            paths.add(urllib.parse.urlsplit(match.group(0)).path)
    return paths


def test_the_capture_scan_really_finds_the_captured_paths():
    """Guard the guard: an empty scan would make the check below vacuous."""
    paths = _captured_paths()

    assert len(paths) >= 10, f"only {len(paths)} avito.ru paths found in the captures: {paths}"


def _captured_item_page_urls() -> set[str]:
    """The canonical URL of every captured item page."""
    urls: set[str] = set()
    for sidecar in sorted(FIXTURES.glob("item_*.provenance.json")):
        url = str(json.loads(sidecar.read_text(encoding="utf-8")).get("url") or "")
        if url:
            urls.add(url)
    for page in sorted(FIXTURES.glob("item_*.html")):
        text = page.read_text(encoding="utf-8")
        for match in re.finditer(r'"url"\s*:\s*"(https://www\.avito\.ru[^"]+)"', text):
            urls.add(match.group(1))
    return urls


@pytest.mark.req("R-AVITO-ITEM-PATH")
def test_every_captured_avito_path_is_a_plain_ascii_slug():
    """The captures decide how narrow the item-path regex may be."""
    offenders = []
    for path in sorted(_captured_paths()):
        for segment in (part for part in path.split("/") if part):
            if not _ASCII_SLUG_RE.fullmatch(segment):
                offenders.append((path, segment))

    assert not offenders, f"captured Avito paths that are not plain ASCII slugs: {offenders}"


@pytest.mark.req("R-AVITO-ITEM-PATH")
def test_the_captured_item_pages_carry_their_id_in_the_url_tail():
    """Every captured item page's canonical URL ends in that item's id.

    This is the property the redirect identity check reads: the URL the
    navigation lands on carries the id in the tail of its last segment.
    """
    without_id = []
    for url in sorted(_captured_item_page_urls()):
        segment = urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        if not _ITEM_ID_TAIL_RE.fullmatch(segment):
            without_id.append(url)

    assert not without_id, f"captured item page URLs without an id in the tail: {without_id}"


@pytest.mark.req("R-AVITO-ITEM-PATH")
@pytest.mark.parametrize(
    "path",
    [
        "/4612345678",
        "/noutbuk_thinkpad_x1_4612345678",
        "/moskva/fixture_9000000032",
        "/moskva/noutbuki/noutbuk_thinkpad_x1_4612345678",
        "/moskva/noutbuki/noutbuk_thinkpad_x1_4612345678/",
        "/moskva/avtomobili/bmw/x5_9000000032",
    ],
)
def test_the_item_path_regex_accepts_the_item_page_shapes(path):
    assert server._ITEM_URL_PATH_RE.fullmatch(path) is not None


@pytest.mark.req("R-AVITO-ITEM-PATH")
@pytest.mark.parametrize(
    "path",
    [
        "/moskva/ноутбуки/noutbuk_4612345678",
        "/moskva/noutbuki/noutbuk.lenovo_4612345678",
        "/moskva/noutbuki/noutbuk_4612345678/extra",
        "/moskva/noutbuki/noutbuk%2Fy_4612345678",
        "/moskva/noutbuki/noutbuk\\x_4612345678",
        "/moskva/noutbuki/noutbuk_46123",
    ],
    ids=[
        "cyrillic-category",
        "dot-in-slug",
        "path-past-the-id",
        "percent-escape",
        "backslash",
        "short-id",
    ],
)
def test_the_item_path_regex_refuses_everything_that_is_not_an_ascii_slug(path):
    """A wider shape than the captures need would let a crafted path through."""
    assert server._ITEM_URL_PATH_RE.fullmatch(path) is None
