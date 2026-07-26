"""Tests for Yandex Market SSR extraction.

Fixtures are real pages captured live in Jul 2026, trimmed to a few products and
two reviews each so they stay reviewable in a diff (2 MB → ~60 KB). Trimming
preserves the exact nesting, so a structural change upstream still shows up here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from yandex_connector import ssr

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def search_washer() -> str:
    return load("search_washer.html")


@pytest.fixture(scope="module")
def search_iphone() -> str:
    return load("search_iphone.html")


@pytest.fixture(scope="module")
def card_washer() -> str:
    return load("card_washer.html")


@pytest.fixture(scope="module")
def card_no_rating() -> str:
    return load("card_no_rating.html")


# ------------------------------------------------------------------ search ----


def test_search_extracts_products_and_result_metadata(search_washer):
    result = ssr.parse_search(search_washer)

    assert result["status"] == ssr.ParseStatus.OK
    assert result["total"] == 1434
    assert result["page"] == 1
    assert len(result["items"]) == 3


def test_search_reports_both_prices_separately(search_washer):
    """The everyday price and the Plus price must never be conflated.

    Quoting only the subscriber price would misstate the cost for anyone without
    a Yandex Plus subscription.
    """
    item = ssr.parse_search(search_washer)["items"][0]

    assert item["price_rub"] == 22600.0
    assert item["price_with_plus"] == 16222.0
    assert item["price_with_plus"] < item["price_rub"]


def test_search_resolves_brand_and_seller_through_id_references(search_washer):
    """Brand and seller live in separate collections, joined by id."""
    item = ssr.parse_search(search_washer)["items"][0]

    assert item["brand"] == "Tuvio"
    assert item["seller"] == "Яндекс Фабрика"


def test_search_rounds_float32_ratings(search_washer):
    """Yandex serialises ratings as float32: 4.8 arrives as 4.800000190734863."""
    ratings = [item["rating"] for item in ssr.parse_search(search_washer)["items"] if item["rating"]]

    assert ratings
    for rating in ratings:
        assert rating == round(rating, 2)
        assert 1.0 <= rating <= 5.0


def test_search_builds_absolute_urls(search_iphone):
    for item in ssr.parse_search(search_iphone)["items"]:
        assert item["url"].startswith("https://")


def test_search_deduplicates_by_snippet(search_iphone):
    """One product can appear as several offers; each visible slot counts once."""
    items = ssr.parse_search(search_iphone)["items"]
    assert len(items) == len({(i["product_id"], i["seller"]) for i in items})


def test_search_distinguishes_empty_results_from_drift():
    """'Nothing found' and 'we can no longer parse this' are different answers."""
    empty = ssr.parse_search(load("search_empty.html"))
    assert empty["status"] == ssr.ParseStatus.EMPTY
    assert empty["items"] == []

    garbage = ssr.parse_search("<html><body>unrecognisable</body></html>")
    assert garbage["status"] == ssr.ParseStatus.NO_PRODUCTS_FOUND


def test_search_detects_a_real_captcha():
    challenge = '<html><body><div id="SmartCaptcha">confirm you are human</div></body></html>'
    assert ssr.parse_search(challenge)["status"] == ssr.ParseStatus.CAPTCHA


def test_captcha_placeholder_is_not_a_captcha(search_washer):
    """Every healthy page ships an empty captchaService div — not a challenge.

    Matching the bare substring 'captcha' would flag every successful request.
    """
    page = search_washer.replace("</body>", '<div id="/content/captchaService"></div></body>')
    assert not ssr.looks_like_captcha(page)
    assert ssr.parse_search(page)["status"] == ssr.ParseStatus.OK


def test_search_falls_back_to_ldjson_when_state_is_unreadable():
    """schema.org markup keeps the tool useful when the widget state moves."""
    html = """<html><body><script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[{"item":{"name":"Тестовый товар",
     "url":"https://market.yandex.ru/product/12345","sku":"999",
     "offers":{"price":1500,"priceCurrency":"RUR"},
     "aggregateRating":{"ratingValue":4.5,"ratingCount":10}}}]}
    </script></body></html>"""

    result = ssr.parse_search(html)

    assert result["status"] == ssr.ParseStatus.OK_LDJSON_ONLY
    assert result["items"][0]["title"] == "Тестовый товар"
    assert result["items"][0]["source"] == "ld+json"


# -------------------------------------------------------------------- card ----


def test_card_extracts_prices_rating_and_seller(card_washer):
    card = ssr.parse_card(card_washer)

    assert card["status"] == ssr.ParseStatus.OK
    assert card["title"].startswith("Стиральная машина")
    assert card["brand"] == "Tuvio"
    assert card["price_rub"] == 19377.0
    assert card["price_with_plus"] == 18796.0
    assert card["price_before_discount_rub"] == 26185.0
    assert card["discount_percent"] == 28


def test_card_extracts_the_star_distribution(card_washer):
    """The breakdown is the point: a 4.8 average can still hide 1-star clusters."""
    card = ssr.parse_card(card_washer)

    assert card["rating"] == 4.8
    assert card["rating_count"] == 544
    assert card["review_count"] == 209
    assert card["rating_stars"] == {1: 10, 2: 3, 3: 10, 4: 19, 5: 502}
    assert sum(card["rating_stars"].values()) == card["rating_count"]


def test_card_extracts_reviews_with_pros_cons_and_votes(card_washer):
    reviews = ssr.parse_card(card_washer)["reviews"]

    assert reviews
    first = reviews[0]
    assert first["author"]
    assert 1 <= first["rating"] <= 5
    assert first["date"]
    assert first["pros"] or first["cons"] or first["comment"]
    assert first["votes_up"] >= 0


def test_card_without_ratings_returns_none_not_zero(card_no_rating):
    """A resale/clearance offer genuinely has no rating.

    Reporting 0 would read as 'terrible product' rather than 'not rated'.
    """
    card = ssr.parse_card(card_no_rating)

    assert card["title"]
    assert card["price_rub"] is not None
    assert card["rating"] is None
    assert card["rating_stars"] == {}
    assert card["reviews"] == []


def test_card_detects_a_real_captcha():
    assert ssr.parse_card("<html><body>/showcaptcha</body></html>")["status"] == ssr.ParseStatus.CAPTCHA


def test_card_survives_an_unparseable_page():
    """A tolerant reader degrades to empty fields rather than raising."""
    card = ssr.parse_card("<html><body>nothing here</body></html>")

    assert card["status"] == ssr.ParseStatus.OK
    assert card["title"] == ""
    assert card["price_rub"] is None


# ----------------------------------------------------------------- merging ----


def test_merge_never_lets_an_empty_patch_clobber_real_data():
    """The rule that decides between reading 13 reviews and reading none.

    Yandex re-declares collection keys as empty in later patches; honouring them
    blindly discards the data an earlier patch delivered.
    """
    html = (
        '<noframes data-apiary="patch">{"collections":{"reviews":{"reviews":[{"rating":5}]}}}</noframes>'
        '<noframes data-apiary="patch">{"collections":{"reviews":{"reviews":[]}}}</noframes>'
    )

    merged = ssr.merge_card_collections(html)

    assert merged["reviews"]["reviews"] == [{"rating": 5}]


def test_merge_skips_malformed_patches():
    html = (
        '<noframes data-apiary="patch">not json at all</noframes>'
        '<noframes data-apiary="patch">{"collections":{"titleV2":{"a":{"title":"OK"}}}}</noframes>'
    )

    merged = ssr.merge_card_collections(html)

    assert merged["titleV2"]["a"]["title"] == "OK"


def test_search_collections_are_found_by_content_not_path():
    """The widget's path key is generated per render and must not be hardcoded."""
    html = (
        '<noframes data-apiary="patch">{"widgets":{"@some/Widget":'
        '{"/a/random/generated/path":{"options":{"collections":'
        '{"offer":{"o1":{}},"product":{"p1":{}},"productSnippet":{"s1":{}}}}}}}}</noframes>'
    )

    collections = ssr.find_search_collections(html)

    assert collections is not None
    assert "offer" in collections


def test_search_collections_prefers_the_richest_bundle():
    """Several widgets can carry collections; the one with products wins."""
    html = (
        '<noframes data-apiary="patch">{"widgets":{"A":{"p1":{"options":{"collections":{"offer":{}}}}}}}</noframes>'
        '<noframes data-apiary="patch">{"widgets":{"B":{"p2":{"options":{"collections":'
        '{"offer":{"o1":{},"o2":{}},"product":{"p":{}}}}}}}}</noframes>'
    )

    collections = ssr.find_search_collections(html)

    assert len(collections["offer"]) == 2


# ---------------------------------------------------------------- coercion ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (4.900000095367432, 4.9),  # float32 artefact
        ("1 234", 1234.0),  # thin-space grouping
        ("1 234", 1234.0),  # non-breaking space
        (0, None),  # zero is missing, not free
        (-5, None),  # negative is drift
        (None, None),
        ("", None),
        (True, None),  # a bool is never a price
        ("abc", None),
    ],
)
def test_number_coercion(raw, expected):
    assert ssr._to_number(raw) == expected
