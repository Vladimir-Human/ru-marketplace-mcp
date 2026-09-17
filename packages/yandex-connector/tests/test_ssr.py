"""Tests for Yandex Market SSR extraction.

Fixtures are real pages captured live in Jul 2026 (plus a Sep 2026 search
capture for the price-semantics regression), trimmed to a few products and two
reviews each so they stay reviewable in a diff (2 MB → ~60 KB). Trimming
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
def search_kettle() -> str:
    return load("search_kettle.html")


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

    Values re-read from this same Jul 2026 capture after the 2026-09-11 price
    mapping fix: the page displayed the cart price 16724 as the everyday price;
    22600 is the struck-through base price (baobabPayload.price ==
    offer.price.value) and now lives in price_old_rub.
    """
    item = ssr.parse_search(search_washer)["items"][0]

    assert item["price_rub"] == 16724.0
    assert item["price_with_plus"] == 16222.0
    assert item["price_with_plus"] < item["price_rub"]
    assert item["price_old_rub"] == 22600.0
    assert item["price_rub"] < item["price_old_rub"]


def test_search_price_rub_is_the_cart_price_never_the_strike_through(search_kettle):
    """Regression for the price-semantics bug (live check 2026-09-11).

    On a discounted SERP row ``offer.price.value`` and ``baobabPayload.price``
    both carry the struck-through ``initialPrice`` — 3698 for the Tuvio
    TKP2117S — and the parser used to quote that as ``price_rub``. The price
    any buyer pays is the snippet's cart price (``cartButton.price.valueFmt``,
    mirrored by the ``withDiscount`` additional price): 2293, cross-checked the
    same day against the product card (prices.price=2293, greenPrice=2247,
    initialPrice=3698).
    """
    items = ssr.parse_search(search_kettle)["items"]
    tuvio = next(item for item in items if item["product_id"] == "5929806453")

    assert tuvio["price_rub"] == 2293.0
    assert tuvio["price_rub"] != 3698.0  # the initialPrice must never leak into price_rub
    assert tuvio["price_with_plus"] == 2247.0
    assert tuvio["price_old_rub"] == 3698.0


def test_search_price_rub_ignores_intermediate_seller_prices(search_kettle):
    """offer.price.value is not a displayed price either.

    The Midea row's offer carries 1350 — a seller-side figure the page shows
    nowhere; the snippet displayed (and its cart button charged) 1089, with
    2999 struck through.
    """
    items = ssr.parse_search(search_kettle)["items"]
    midea = next(item for item in items if item["product_id"] == "6050907310")

    assert midea["price_rub"] == 1089.0
    assert midea["price_rub"] != 1350.0
    assert midea["price_with_plus"] == 1067.0
    assert midea["price_old_rub"] == 2999.0


def test_search_row_describes_the_serp_offer_not_the_card_default(search_kettle):
    """Documented quirk, not a bug: the SERP row and the card can name different
    offers of one family.

    The row for product 198679568 is the REDMOND KM243 offer (sku 4668084807),
    while the card for the same product id defaults to a KM245 offer at another
    price (live check 2026-09-11). The connector is SERP-faithful; reconcile
    search rows with cards by ``sku_id``, never by product URL alone.
    """
    items = ssr.parse_search(search_kettle)["items"]
    redmond = next(item for item in items if item["product_id"] == "198679568")

    assert redmond["sku_id"] == "4668084807"
    assert "КМ243" in redmond["title"]
    assert redmond["price_rub"] == 2004.0
    assert redmond["price_old_rub"] == 4999.0


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


# ------------------------------------------------------- hollow product frame ----


def test_card_empty_shell_from_the_live_capture():
    """Real 2026-09-13 capture: the product is alive, the frame is hollow.

    Yandex served /product/4315891968 to anonymous HTTP as a product page
    (pageParams pageId market:product, the id echoed back) with every product
    collection declared and empty and no schema.org Product — while the same
    product was the first hit of its own search and the same URL showed
    SmartCaptcha in a real browser. Degraded serving must be reported as the
    hollow frame it is, never as a verdict about the parsers.
    """
    card = ssr.parse_card(load("card_empty_shell.html"))

    assert card["status"] == ssr.ParseStatus.EMPTY_PRODUCT_SHELL
    assert card["product_id"] == "4315891968"  # recovered from pageParams
    assert card["title"] == ""
    assert card["price_rub"] is None
    assert card["price_with_plus"] is None
    assert card["reviews"] == []


def test_synthetic_product_frame_with_empty_collections_is_a_shell():
    html = (
        '<noframes data-apiary="patch">{"collections":{"pageParams":{"current":'
        '{"id":"current","pageId":"market:product","params":{"productId":"42"}}}}}</noframes>'
        '<noframes data-apiary="patch">{"collections":{"titleV2":{},"price":{},"allPrices":{},'
        '"offer":{},"productServiceSnippets":{},"shopInfo":{},"mediaItem":{},"reviews":{},'
        '"businessRatingStats":{}}}</noframes>'
    )

    card = ssr.parse_card(html)

    assert card["status"] == ssr.ParseStatus.EMPTY_PRODUCT_SHELL
    assert card["product_id"] == "42"


def test_shell_verdict_requires_the_product_page_id():
    """An empty state that does not claim to be a product render is just an
    unparseable page — it stays OK so the tool layer can report drift."""
    html = (
        '<noframes data-apiary="patch">{"collections":{"pageParams":{"current":'
        '{"id":"current","pageId":"market:search","params":{}}},"titleV2":{},"price":{}}}</noframes>'
    )

    assert ssr.parse_card(html)["status"] == ssr.ParseStatus.OK


def test_renamed_field_families_are_a_parser_question_not_a_shell():
    """Shape drift with populated collections must never read as hollow.

    The tri-state distinction: a field family that changed shape is drift (a
    maintainer must look); a frame that carries no product at all is degraded
    serving (inconclusive). Both pages lack a parseable title.
    """
    html = (
        '<noframes data-apiary="patch">{"collections":{"pageParams":{"current":'
        '{"id":"current","pageId":"market:product","params":{"productId":"42"}}}}}</noframes>'
        '<noframes data-apiary="patch">{"collections":{"titleV2":{"t1":{"titleRenamed":"Товар"}},'
        '"price":{"p1":{"mainPriceRenamed":{"price":{"value":100}}}}}}</noframes>'
    )

    card = ssr.parse_card(html)

    assert card["status"] == ssr.ParseStatus.OK
    assert card["title"] == ""  # the family is there but no longer understood


def test_a_real_card_without_a_rating_is_never_a_shell(card_no_rating):
    assert ssr.parse_card(card_no_rating)["status"] == ssr.ParseStatus.OK


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


def test_number_coercion_never_returns_or_raises_on_non_finite_values():
    """Yandex SSR embeds raw JSON-LD, and json.loads admits NaN/Infinity by
    default, so non-finite values reach this helper from the wire. Without a
    finiteness guard ``_to_number`` hands inf straight back (round(inf, 2) is
    still inf), and ``_to_int`` then crashes on int(inf) with OverflowError —
    one poisoned cell must degrade to no-data, not corrupt a price/rating or
    abort the whole tool. Same doctrine as coerce_price: never 0, never
    negative, never non-finite."""
    assert ssr._to_number(float("inf")) is None
    assert ssr._to_number(float("-inf")) is None
    assert ssr._to_number(float("nan")) is None
    assert ssr._to_number("1e400") is None
    assert ssr._to_number("9" * 400) is None
    assert ssr._to_int(float("inf")) is None


# ------------------------------------------------- zone payloads (SERP) ----
# Since 2026-09-12 the anonymous search page carries no collection bundle at
# all: the products moved to client-side lazy loading. The first screen of
# snippets is still server-rendered, and every productSnippet div carries its
# own analytics payload in a data-zone-data attribute — the same baobabPayload
# the collections used to deliver, minus the joins. These tests pin that path.


@pytest.fixture(scope="module")
def search_telefon_zone() -> str:
    return load("search_telefon_zone.html")


def test_zone_search_reads_the_first_screen_when_collections_are_absent(search_telefon_zone):
    """The post-2026-09-12 anonymous page: no collections, rows still there.

    Before this path existed the page fell through to the schema.org list,
    which carries the Plus price only — thin data for a page that in fact
    renders the full first screen.
    """
    result = ssr.parse_search(search_telefon_zone)

    assert result["status"] == ssr.ParseStatus.OK_ZONE
    assert len(result["items"]) == 3
    assert all(item["source"] == "zone" for item in result["items"])


def test_zone_search_never_quotes_the_plus_price_as_the_everyday_price(search_telefon_zone):
    """The core price invariant on the zone path.

    Re-read from this capture (2026-09-13, anonymous, search «телефон»): the
    Realme row printed 12450 struck through, 11329 as the everyday price
    (additionalPrices[withDiscount], the figure the card's prices.price row
    mirrors) and 11102 as the green Plus price. The Plus figure is the one
    rendered big in the snippet DOM, so it is the value a naive reader would
    quote as the price.
    """
    realme = ssr.parse_search(search_telefon_zone)["items"][0]

    assert realme["product_id"] == "4315891968"
    assert realme["price_rub"] == 11329.0
    assert realme["price_with_plus"] == 11102.0
    assert realme["price_old_rub"] == 12450.0
    assert realme["price_with_plus"] < realme["price_rub"] < realme["price_old_rub"]


def test_zone_search_takes_the_ids_from_the_offer_not_the_family(search_telefon_zone):
    """product_id is oskuId (what /card and /product accept), sku_id marketSku.

    The Samsung row proves the two differ: the row links to product 4680656365
    while the offer's marketSku is 4696420378. Collapsing them would silently
    make a card lookup address a different offer.
    """
    samsung = ssr.parse_search(search_telefon_zone)["items"][2]

    assert samsung["product_id"] == "4680656365"
    assert samsung["sku_id"] == "4696420378"
    assert samsung["product_id"] in samsung["url"]
    assert samsung["price_rub"] == 19092.0
    assert samsung["price_with_plus"] == 18710.0


def test_zone_search_reports_the_seller_only_when_the_page_ships_one(search_telefon_zone):
    """Rows without a shop signal report an empty seller, never a guessed one."""
    items = ssr.parse_search(search_telefon_zone)["items"]

    assert items[0]["seller"] == "ОНЛАЙНТРЕЙД.РУ"
    assert items[1]["seller"] == ""


def test_zone_snippets_ignore_tiles_that_are_not_offers():
    """The same zone name is reused for non-product tiles on some pages."""
    html = (
        '<div data-zone-name="productSnippet" data-zone-data="{&quot;type&quot;:&quot;banner&quot;,&quot;title&quot;:&quot;Ad&quot;}">'
        '<div data-zone-name="productSnippet" data-zone-data="{&quot;type&quot;:&quot;offer&quot;,&quot;oskuId&quot;:1,&quot;price&quot;:100}">'
    )

    payloads = ssr.zone_snippets(html)

    assert [p["oskuId"] for p in payloads] == [1]


def test_zone_snippets_skip_unparseable_payloads():
    """One malformed payload must not abort the whole page."""
    html = (
        '<div data-zone-name="productSnippet" data-zone-data="{not json">'
        '<div data-zone-name="productSnippet" data-zone-data="{&quot;type&quot;:&quot;offer&quot;,&quot;oskuId&quot;:2,&quot;price&quot;:200}">'
    )

    payloads = ssr.zone_snippets(html)

    assert [p["oskuId"] for p in payloads] == [2]


def test_zone_row_without_prices_reports_absent_not_zero():
    """A row the page renders without a price is absent data, never 0 ."""
    html = (
        '<div data-zone-name="productSnippet" data-zone-data="'
        '{&quot;type&quot;:&quot;offer&quot;,&quot;oskuId&quot;:777,&quot;title&quot;:&quot;T&quot;}">'
    )

    item = ssr.parse_zone_items(html)[0]

    assert item["price_rub"] is None
    assert item["price_with_plus"] is None
    assert item["price_old_rub"] is None
    assert item["product_id"] == "777"


def test_zone_undiscounted_row_treats_the_base_price_as_the_everyday_price():
    """With no withDiscount entry the base price is what anyone pays.

    And with nothing struck through, price_old_rub stays empty rather than
    echoing the everyday price back as a fake «old» figure.
    """
    html = (
        '<div data-zone-name="productSnippet" data-zone-data="'
        "{&quot;type&quot;:&quot;offer&quot;,&quot;oskuId&quot;:888,&quot;price&quot;:5000,"
        '&quot;additionalPrices&quot;:[{&quot;priceType&quot;:&quot;yaBank&quot;,&quot;priceValue&quot;:4900}]}">'
    )

    item = ssr.parse_zone_items(html)[0]

    assert item["price_rub"] == 5000.0
    assert item["price_with_plus"] == 4900.0
    assert item["price_old_rub"] is None


def test_parse_search_prefers_zone_rows_over_the_plus_only_fallback():
    """Cascade order: collections, then zone payloads, then schema.org.

    The schema.org list is a degraded path (Plus price only, no seller), so
    whenever the zone payloads are readable they must win.
    """
    html = (
        '<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"ListItem",'
        '"item":{"@type":"Product","@id":"https://market.yandex.ru/card/x/999","name":"LD","sku":"999",'
        '"offers":{"@type":"Offer","price":123,"priceCurrency":"RUB"}}}]}</script>'
        '<div data-zone-name="productSnippet" data-zone-data="'
        "{&quot;type&quot;:&quot;offer&quot;,&quot;oskuId&quot;:999,&quot;title&quot;:&quot;Zone&quot;,"
        '&quot;price&quot;:200,&quot;additionalPrices&quot;:[{&quot;priceType&quot;:&quot;withDiscount&quot;,&quot;priceValue&quot;:180}]}">'
    )

    result = ssr.parse_search(html)

    assert result["status"] == ssr.ParseStatus.OK_ZONE
    assert result["items"][0]["price_rub"] == 180.0
    assert result["items"][0]["source"] == "zone"


def test_parse_search_still_falls_back_to_schema_org_when_zone_is_missing():
    """The fallback must survive: pages without zone payloads still read.

    And it must stay honest about what it knows: schema.org carries the Plus
    price only, so the fallback row ships ``price_with_plus`` and NO
    ``price_rub`` key at all — it never promotes the subscriber price into the
    everyday-price slot. Re-read from this capture with the zone attributes
    renamed away (status ok_ldjson_only).
    """
    html = (
        '<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"ListItem",'
        '"item":{"@type":"Product","@id":"https://market.yandex.ru/card/x/999","name":"LD","sku":"999",'
        '"offers":{"@type":"Offer","price":123,"priceCurrency":"RUB"}}}]}</script>'
    )

    result = ssr.parse_search(html)

    assert result["status"] == ssr.ParseStatus.OK_LDJSON_ONLY
    assert result["items"][0]["source"] == "ld+json"
    assert result["items"][0]["price_with_plus"] == 123.0
    assert "price_rub" not in result["items"][0]
