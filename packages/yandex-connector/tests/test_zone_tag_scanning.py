"""A zone snippet whose attribute holds a raw '>' must still be parsed (review 2026-09-18).

The old tag regex was ``<[^>]*data-zone-name="productSnippet"[^>]*>``: ``[^>]*`` stops
at the first ``>``, and a raw ``>`` inside a quoted attribute value is legal HTML, so
such a snippet vanished from the parse without a word.
"""

from __future__ import annotations

import json

from yandex_connector.ssr import zone_snippets

PAYLOAD = {"type": "offer", "marketSku": 123, "price": 4990}


def _tag(extra: str = "") -> str:
    payload = json.dumps(PAYLOAD).replace('"', "&quot;")
    return f'<div{extra} data-zone-name="productSnippet" data-zone-data="{payload}"></div>'


def test_a_plain_snippet_is_read():
    assert zone_snippets(_tag())[0]["marketSku"] == 123


def test_a_raw_gt_before_the_zone_attribute_does_not_hide_the_snippet():
    """The regression: an earlier attribute containing '>' used to swallow the tag."""
    html = _tag(' title="дешевле > 5000" data-auto="snippet"')

    rows = zone_snippets(html)

    assert len(rows) == 1, "the snippet must not disappear because of a '>' in another attribute"
    assert rows[0]["marketSku"] == 123


def test_a_raw_gt_after_the_payload_does_not_hide_the_snippet():
    html = _tag(' aria-label="a > b"')

    assert len(zone_snippets(html)) == 1


def test_several_snippets_keep_their_document_order():
    html = _tag() + _tag(' title="x > y"')

    assert len(zone_snippets(html)) == 2


def test_non_product_zones_are_still_ignored():
    payload = json.dumps({"type": "banner", "marketSku": 1}).replace('"', "&quot;")
    html = f'<div data-zone-name="productSnippet" data-zone-data="{payload}"></div>'

    assert zone_snippets(html) == []


def test_a_quoted_angle_bracket_inside_the_payload_does_not_split_the_tag():
    """Guards the tag scanner itself: quotes must be tracked, not just '>'."""
    payload = json.dumps({"type": "offer", "title": "a > b"}).replace('"', "&quot;")
    html = f'<div data-zone-name="productSnippet" data-zone-data="{payload}"></div>'

    rows = zone_snippets(html)

    assert len(rows) == 1
    assert rows[0]["title"] == "a > b"
