"""Offline seller regressions; fixtures are immutable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from avito_connector import server
from fastmcp.exceptions import ToolError
from mcp_core.errors import NotFoundError, ParserDriftError

FIXTURES = Path(__file__).parent / "fixtures"
ITEM_ID = 7924452716
ITEM_URL = "https://www.avito.ru/moskva/telefony/iphone_15_128_gb_sim_esim_7924452716"
PROFILE_URL = "https://www.avito.ru/brands/27696b66792f17b347ec073a8d1c2d93"
NAME = "iStore | Техника Apple"
SEARCH = {"totalCount": 1, "items": [{"id": ITEM_ID, "title": "iPhone 15", "price": 39990, "uri": ITEM_URL}]}


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _card_html():
    # Compose existing captured evidence only in memory, never edit fixtures.
    product = _fixture("item_page_7924452716.html")
    block = _fixture("item_seller_block_7924452716.html")
    return product.replace("<body></body>", "<body>" + block + "</body>")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    server._cache._data.clear()
    server._pacer.reset()

    async def fake_fetch(url, ctx):
        return 200, json.dumps(SEARCH, ensure_ascii=False), "curl_cffi"

    def unexpected_transport(*args, **kwargs):
        raise AssertionError("offline test attempted an unmocked transport")

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    monkeypatch.setattr(server, "_sync_curl_get", unexpected_transport)
    monkeypatch.setattr(server, "_cdp_fetch", unexpected_transport)
    monkeypatch.setattr(server, "open_page", unexpected_transport)
    yield
    server._cache._data.clear()
    server._pacer.reset()


def _patch_page(monkeypatch, html, status=200):
    calls = []

    async def fake_fetch_item_page(url, ctx):
        calls.append(url)
        return status, html, "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)
    return calls


def test_captured_item_seller_matches_exact_ground_truth():
    assert server.parse_item_seller(_fixture("item_seller_block_7924452716.html")) == {
        "name": NAME,
        "rating_score": 5.0,
        "rating_count": 563,
        "is_company": False,
        "profile_url": PROFILE_URL,
    }


@pytest.mark.parametrize("include_seller", [True, False], ids=["actual-seller-wins", "header-is-not-seller"])
def test_item_seller_never_uses_operator_header_profile(include_seller):
    header = '<header><a href="/user/operator/profile">Мой профиль</a></header>'
    html = header + (_fixture("item_seller_block_7924452716.html") if include_seller else "")

    seller = server.parse_item_seller(html)

    assert seller["profile_url"] == (PROFILE_URL if include_seller else None)
    assert seller["name"] == (NAME if include_seller else None)
    if not include_seller:
        assert all(value is None for value in seller.values())


def test_captured_profile_matches_exact_ground_truth():
    assert server.parse_seller_profile(_fixture("seller_profile_brands_27696b66.html")) == {
        "name": NAME,
        "rating_score": 5.0,
        "rating_count": 563,
    }


def test_absent_item_seller_is_all_null_not_invented():
    assert server.parse_item_seller("<html><body>Нет блока продавца</body></html>") == {
        "name": None,
        "rating_score": None,
        "rating_count": None,
        "is_company": None,
        "profile_url": None,
    }


@pytest.mark.parametrize(
    "label, expected", [("Частное лицо", False), ("Компания", True), ("Магазин", True), ("", None)]
)
def test_item_seller_company_flag_comes_from_label_not_brands_path(label, expected):
    html = (
        '<div data-marker="seller-info/name"><a data-marker="seller-link/link" '
        'href="/brands/shop?from=item">Иван</a></div>'
        f'<div data-marker="seller-info/label">{label}</div>'
    )
    assert server.parse_item_seller(html) == {
        "name": "Иван",
        "rating_score": None,
        "rating_count": None,
        "is_company": expected,
        "profile_url": "https://www.avito.ru/brands/shop",
    }


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<title>Анна - отзывы на Авито</title>", {"name": "Анна", "rating_score": None, "rating_count": None}),
        ('<span data-marker="profile/score">4,8</span>', {"name": None, "rating_score": 4.8, "rating_count": None}),
    ],
    ids=["name-without-rating", "rating-without-name"],
)
def test_profile_accepts_partial_identity_without_inventing_zero(html, expected):
    assert server.parse_seller_profile(html) == expected


@pytest.mark.parametrize("text", ["Ошибка 404. Страница не найдена", "Страница не найдена", "Пользователь не найден"])
def test_profile_visible_russian_404_is_raw_not_found(text):
    with pytest.raises(NotFoundError):
        server.parse_seller_profile(f"<html><body><h1>{text}</h1></body></html>")


def test_item_review_count_never_includes_rating_decimal_digits():
    # The comma-decimal score and review count share a visible text container.
    # Its final score digit must not be mistaken for a count thousands group.
    for text, expected in [("563 отзыва", 563), ("1 563 отзыва", 1563), ("1\u00a0563 отзыва", 1563)]:
        html = (
            '<div data-marker="seller-info/name">Анна</div>'
            '<div class="seller-info-rating">'
            '<span data-marker="rating-caption/rating">4,8</span>'
            f"<span>{text}</span></div>"
        )
        seller = server.parse_item_seller(html)
        assert seller["rating_score"] == 4.8
        assert seller["rating_count"] == expected

    count_only = server.parse_item_seller(
        '<div data-marker="seller-info/name">Анна</div><div class="seller-info-rating">1 отзыв</div>'
    )
    assert count_only["rating_score"] is None
    assert count_only["rating_count"] == 1


def test_profile_without_name_or_score_is_raw_parser_drift():
    with pytest.raises(ParserDriftError):
        server.parse_seller_profile('<html><body><div data-marker="profile/summary">563 отзыва</div></body></html>')


async def test_card_combines_captured_product_and_seller_block(monkeypatch):
    calls = _patch_page(monkeypatch, _card_html())

    result = await server.avito_card(ITEM_URL)

    assert calls == [ITEM_URL]
    assert result.status == "success"
    assert result.item_id == ITEM_ID
    assert result.title == "iPhone 15, 128 ГБ, SIM + eSIM"
    assert result.price_rub == 39990.0
    assert result.images == 2
    assert result.seller is not None
    assert result.seller.name == NAME
    assert result.seller.rating_score == 5.0
    assert result.seller.rating_count == 563
    assert result.seller.is_company is False
    assert result.seller.profile_url == PROFILE_URL
    assert result.seller.seller_id == "27696b66792f17b347ec073a8d1c2d93"  # from the profile link
    assert result.tier_used == "cdp"


@pytest.mark.parametrize(
    "raw, expected_url",
    [
        (PROFILE_URL + "?shopId=7760306#reviews", PROFILE_URL),
        ("/brands/27696b66792f17b347ec073a8d1c2d93?from=card", PROFILE_URL),
        ("987654", "https://www.avito.ru/user/987654/profile"),
        ("27696b66792f17b347ec073a8d1c2d93", PROFILE_URL),
        ("https://avito.ru/user/987654/profile?context=1#reviews", "https://avito.ru/user/987654/profile"),
    ],
    ids=["brands-url", "brands-path", "numeric-id", "brand-hash", "user-url"],
)
async def test_seller_fetches_normalized_rendered_profile(monkeypatch, raw, expected_url):
    calls = _patch_page(monkeypatch, _fixture("seller_profile_brands_27696b66.html"))

    result = await server.avito_seller(raw)

    assert calls == [expected_url]
    assert result.status == "success"
    assert result.seller is not None
    assert result.seller.name == NAME
    assert result.seller.rating_score == 5.0
    assert result.seller.rating_count == 563
    assert result.seller.profile_url == expected_url
    assert result.seller.is_company is None
    assert result.active_items is None
    assert result.tier_used == "cdp"


@pytest.mark.parametrize(
    "bad",
    [
        "https://example.com/user/987654/profile",
        "https://avito.ru.evil.test/brands/shop",
        "ftp://www.avito.ru/user/987654/profile",
        "https://user:secret@www.avito.ru/user/987654/profile",
        "https://www.avito.ru:444/user/987654/profile",
        "https://www.avito.ru/moskva/telefony/item_7924452716",
    ],
)
async def test_seller_rejects_invalid_profile_urls_before_transport(monkeypatch, bad):
    calls = _patch_page(monkeypatch, _fixture("seller_profile_brands_27696b66.html"))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_seller(bad)

    assert "bad_request" in str(excinfo.value)
    assert calls == []


@pytest.mark.parametrize(
    "status, html, error",
    [
        (404, "<html><body>Страница не найдена</body></html>", "not_found"),
        (200, "<html><body><h1>Ошибка 404. Страница не найдена</h1></body></html>", "not_found"),
        (200, "<html><body>Обычная страница без профиля</body></html>", "parser_drift"),
    ],
    ids=["http-404", "visible-404", "html-drift"],
)
async def test_seller_maps_profile_errors_to_typed_tool_errors(monkeypatch, status, html, error):
    calls = _patch_page(monkeypatch, html, status)

    with pytest.raises(ToolError) as excinfo:
        await server.avito_seller(PROFILE_URL)

    assert error in str(excinfo.value)
    assert calls == [PROFILE_URL]


def _patch_chain(monkeypatch, *, card_html=None, seller_html=None, page_status=200, failed_stage=None):
    calls = []

    async def fake_fetch(url, ctx):
        assert "/web/1/js/items?" in url
        assert "userId=" not in url
        calls.append("search")
        return 200, json.dumps(SEARCH, ensure_ascii=False), "curl_cffi"

    async def fake_fetch_item_page(url, ctx):
        if url == ITEM_URL:
            calls.append("card")
            if failed_stage == "card":
                return (
                    page_status,
                    "" if page_status == 0 else "<html><body>too-many-requests firewall</body></html>",
                    "cdp",
                )
            return 200, _card_html() if card_html is None else card_html, "cdp"
        assert url == PROFILE_URL
        calls.append("seller")
        if failed_stage == "seller":
            return (
                page_status,
                "" if page_status == 0 else "<html><body>too-many-requests firewall</body></html>",
                "cdp",
            )
        return 200, _fixture("seller_profile_brands_27696b66.html") if seller_html is None else seller_html, "cdp"

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)
    return calls


async def test_selfcheck_chains_real_search_card_and_seller(monkeypatch):
    calls = _patch_chain(monkeypatch)

    result = await server.avito_selfcheck()

    assert calls == ["search", "card", "seller"]
    assert set(result.checks) == {"search", "card", "seller"}
    assert result.status == "success"
    assert result.healthy is True
    assert all(check.state == "healthy" for check in result.checks.values())


@pytest.mark.parametrize("stage", ["card", "seller"])
async def test_selfcheck_detects_reached_html_parser_drift(monkeypatch, stage):
    html = "<html><body>Страница без ожидаемых маркеров</body></html>"
    calls = _patch_chain(monkeypatch, **{stage + "_html": html})

    result = await server.avito_selfcheck()

    assert set(result.checks) == {"search", "card", "seller"}
    assert calls == (["search", "card"] if stage == "card" else ["search", "card", "seller"])
    assert result.status == "drift_detected"
    assert result.healthy is False
    assert result.checks["search"].state == "healthy"
    assert result.checks[stage].state == "drift"
    if stage == "card":
        assert result.checks["seller"].state == "inconclusive"


@pytest.mark.parametrize("stage", ["card", "seller"])
@pytest.mark.parametrize("status", [403, 200, 0], ids=["http-block", "html-challenge", "cdp-down"])
async def test_selfcheck_page_transport_failure_is_inconclusive_not_drift(monkeypatch, stage, status):
    calls = _patch_chain(monkeypatch, failed_stage=stage, page_status=status)

    result = await server.avito_selfcheck()

    assert set(result.checks) == {"search", "card", "seller"}
    assert calls == (["search", "card"] if stage == "card" else ["search", "card", "seller"])
    assert result.status == "inconclusive"
    assert result.healthy is None
    assert result.checks["search"].state == "healthy"
    assert result.checks[stage].state == "inconclusive"
    assert all(check.state != "drift" for check in result.checks.values())


async def test_selfcheck_without_card_seller_link_is_explicitly_inconclusive(monkeypatch):
    calls = _patch_chain(monkeypatch, card_html=_fixture("item_page_7924452716.html"))

    result = await server.avito_selfcheck()

    assert calls == ["search", "card"]
    assert set(result.checks) == {"search", "card", "seller"}
    assert result.checks["search"].state == "healthy"
    assert result.checks["card"].state == "healthy"
    assert result.checks["seller"].state == "inconclusive"
    assert result.checks["seller"].reason == "no_seller_link"
    assert result.status == "inconclusive"
    assert result.healthy is None
