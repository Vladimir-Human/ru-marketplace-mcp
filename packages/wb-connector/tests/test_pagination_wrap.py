"""Past the end of a result set, Wildberries repeats page 1 — silently.

Verified live on 2026-07-28: `wb_search(query="ноутбук", page=20)` returned 100
products byte-identical, in identical order, to page 1. HTTP 200, no error, no
marker of any kind. An agent walking pages therefore collects the same products
over and over while believing it is paginating, and the duplicates are
indistinguishable from real data downstream.

This is a behaviour of the upstream endpoint, first described publicly in
third-party write-ups of the WB storefront API and then reproduced here against
the live service rather than taken on trust.

Two things it is worth being precise about:

* WB's own `total` does not identify the last page. It stays far larger than the
  depth actually served, so arithmetic on it would not have caught the measured
  case. The guard remembers what page 1 looked like instead.
* The guard is therefore only armed once page 1 has been seen for that
  (query, dest) within the cache window. That covers the realistic access
  pattern — sequential pagination — and does nothing otherwise. It never invents
  a verdict from a page it has not compared.
"""

from __future__ import annotations

import pytest
from wb_connector import server


@pytest.fixture(autouse=True)
def _clear_fingerprints():
    server._page1_fingerprints.clear()
    yield
    server._page1_fingerprints.clear()


def _products(*ids: int) -> list[dict]:
    return [{"id": i, "name": f"item {i}", "sizes": [{"price": {"product": 100000, "basic": 120000}}]} for i in ids]


# ---------------------------------------------------------------------------
# The fingerprint itself
# ---------------------------------------------------------------------------


def test_fingerprint_is_order_sensitive() -> None:
    """WB re-ranks between requests; an unordered set would call two genuinely
    different pages equal and hide real products."""
    a = server._page_fingerprint(_products(1, 2, 3, 4))
    b = server._page_fingerprint(_products(4, 3, 2, 1))
    assert a and b
    assert a != b


def test_identical_pages_share_a_fingerprint() -> None:
    assert server._page_fingerprint(_products(1, 2, 3)) == server._page_fingerprint(_products(1, 2, 3))


def test_a_short_page_has_no_fingerprint() -> None:
    """Two or three ids is too little evidence to call a page a repeat."""
    assert server._page_fingerprint(_products(1, 2)) is None
    assert server._page_fingerprint([]) is None


def test_malformed_products_do_not_crash_the_fingerprint() -> None:
    assert server._page_fingerprint([None, "x", {"id": None}, {}]) is None  # type: ignore[list-item]


def test_fingerprint_uses_the_leading_ids_only() -> None:
    """A long page is identified by its head, so the check stays cheap."""
    head = _products(*range(1, 11))
    assert server._page_fingerprint(head) == server._page_fingerprint(head + _products(99, 98))


# ---------------------------------------------------------------------------
# The guard, exercised through the store the connector actually uses
# ---------------------------------------------------------------------------


def test_page_one_is_remembered() -> None:
    key = ("ноутбук", "-1257786")
    fp = server._page_fingerprint(_products(11, 22, 33))
    server._page1_fingerprints.set(key, fp)
    assert server._page1_fingerprints.get(key) == fp


def test_a_repeated_page_is_recognised() -> None:
    """The measured case: page 20 comes back as page 1."""
    key = ("ноутбук", "-1257786")
    page1 = _products(1280469586, 824935779, 497398643, 1238100110)
    server._page1_fingerprints.set(key, server._page_fingerprint(page1))
    page20 = _products(1280469586, 824935779, 497398643, 1238100110)
    assert server._page_fingerprint(page20) == server._page1_fingerprints.get(key)


def test_a_genuinely_different_page_is_not_flagged() -> None:
    """The guard must not eat real results — that would be the worse bug."""
    key = ("ноутбук", "-1257786")
    server._page1_fingerprints.set(key, server._page_fingerprint(_products(1, 2, 3, 4)))
    assert server._page_fingerprint(_products(5, 6, 7, 8)) != server._page1_fingerprints.get(key)


def test_pages_are_scoped_per_query_and_region() -> None:
    """`dest` changes both ranking and price, so a fingerprint cannot cross it."""
    moscow = ("ноутбук", "-1257786")
    spb = ("ноутбук", "-1029256")
    server._page1_fingerprints.set(moscow, server._page_fingerprint(_products(1, 2, 3)))
    assert server._page1_fingerprints.get(spb) is None


def test_an_unseen_page_one_leaves_the_guard_disarmed() -> None:
    """No comparison, no verdict. Silence is better than a guess."""
    assert server._page1_fingerprints.get(("не искали", "-1257786")) is None
