"""Offline tests for the Avito connector.

Every upstream call is monkeypatched, so the suite runs with no network and no
geo dependency. Fixtures mirror the js/items envelope shape documented by
third-party parsers and probed in July 2026 — including the failure modes that
make this endpoint easy to parse wrongly (a 403 that looks like content, a
listing with no price).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from avito_connector import server
from avito_connector.shape_reference import SEARCH_REQUIRED_FAMILIES
from fastmcp.exceptions import ToolError
from mcp_core.transport import chrome_cdp
from mcp_core.transport.cdp_budget import NavigationBudget

# ---------------------------------------------------------------- fixtures ----

SEARCH_PAYLOAD = {
    "totalCount": 137,
    "items": [
        {
            "id": 4612345678,
            "title": "Ноутбук ThinkPad X1 Carbon",
            "price": 45500,
            "uri": "/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
            "location": "Москва",
            "seller": {"name": "ИП Сидоров", "id": 987654, "isCompany": True},
            "date": "сегодня, 12:40",
            "images": [{"url": "https://img.avito.st/1.jpg"}, {"url": "https://img.avito.st/2.jpg"}],
        },
        {
            "id": 4612349999,
            "title": "Отдам даром клавиатуру",
            "uri": "/moskva/klaviatury/klaviatura_4612349999",
            "location": "Москва",
            "seller": {"name": "Анна"},
            "images": [],
        },
    ],
}

CARD_HTML = (
    '<html><head><script type="application/ld+json">'
    + json.dumps(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Product",
                    "sku": "4612345678",
                    "name": "Ноутбук ThinkPad X1 Carbon",
                    "description": "В хорошем состоянии, батарея держит 6 часов.",
                    "image": ["https://img.avito.st/1.jpg"],
                    "offers": {
                        "@type": "Offer",
                        "price": "45500",
                        "priceCurrency": "RUB",
                        "url": "https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
                    },
                }
            ],
        },
        ensure_ascii=False,
    )
    + "</script></head><body></body></html>"
)

FIREWALL_BODY = '{"too-many-requests":{"link":"ru.avito://1/firewall/captcha/show","message":"Доступ с вашего IP-адреса временно ограничен"}}'


def _ok(payload: dict):
    return 200, json.dumps(payload, ensure_ascii=False), "curl_cffi"


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """Every test starts with an empty cache: a cached body from a previous
    case would otherwise shadow its monkeypatched fetch."""
    server._cache._data.clear()


def _patch_fetch(monkeypatch, result):
    async def fake_fetch(url, ctx):
        return result

    monkeypatch.setattr(server, "_fetch", fake_fetch)


def _patch_item_page(monkeypatch, result):
    async def fake_fetch_item_page(url, ctx):
        return result

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


def _patch_item_page_recording(monkeypatch, result):
    """Patch the card fetch and report every URL the tool tried to open."""
    calls = []

    async def fake_fetch_item_page(url, ctx):
        calls.append(url)
        return result

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)
    return calls


def _patch_selfcheck_pages(monkeypatch, product_html):
    fixtures = Path(__file__).parent / "fixtures"
    block = (fixtures / "item_seller_block_7924452716.html").read_text(encoding="utf-8")
    profile = (fixtures / "seller_profile_brands_27696b66.html").read_text(encoding="utf-8")
    card = product_html.replace("<body></body>", "<body>" + block + "</body>")

    async def fake_fetch_item_page(url, ctx):
        return 200, profile if "/brands/" in url else card, "cdp"

    monkeypatch.setattr(server, "_fetch_item_page", fake_fetch_item_page)


async def _open_breaker(host: str, *, cooldown_s: float = 47.0) -> NavigationBudget:
    """Drive a real budget through its public API until its breaker opens.

    A dedicated instance with a frozen clock: two 4xx refusals with a 2-refusal
    threshold open the breaker, leaving ``cooldown_s`` seconds of pause and the
    refusal count the mapped error must report.
    """
    budget = NavigationBudget(breaker_threshold=2, breaker_cooldown_s=cooldown_s, clock=lambda: 1000.0)
    for _ in range(2):
        slot = await budget.acquire(host)
        slot.refused(403)
        slot.release()
    return budget


# --------------------------------------------------------------- avito_search ----


async def test_search_parses_items_and_total(monkeypatch):
    _patch_fetch(monkeypatch, _ok(SEARCH_PAYLOAD))

    result = await server.avito_search("thinkpad", page=1)

    assert result.status == "success"
    assert result.count == 2
    assert result.total_count == 137
    first = result.items[0]
    assert first.item_id == 4612345678
    assert first.price_rub == 45500
    assert first.url == "https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678"
    assert first.seller_name == "ИП Сидоров"
    assert first.images == 2


async def test_search_a_pricelss_listing_is_none_never_zero(monkeypatch):
    """The free keyboard carries no price field at all; it must surface as None,
    because a 0 would rank it cheapest in compare_prices."""
    _patch_fetch(monkeypatch, _ok(SEARCH_PAYLOAD))

    result = await server.avito_search("клавиатура")

    priceless = result.items[1]
    assert priceless.price_rub is None
    assert priceless.price_rub != 0


@pytest.mark.parametrize("bad_loc", ["", "moscow", "63 7640", "637640;rm -rf", "1"])
async def test_search_rejects_a_malformed_location_id(bad_loc):
    """Отказ должен быть по разбору аргумента, а не по неудачному запросу.

    Пустая строка раньше падала на дефолт 637640, уходила в сеть и зеленила
    тест на транспортной ошибке — то есть проверка проходила, не проверив
    ничего. Ошибка обязана быть bad_request, и до сети дойти не должно.
    """
    with pytest.raises(ToolError) as excinfo:
        await server.avito_search("ноутбук", location_id=bad_loc)
    assert "bad_request" in str(excinfo.value), f"ожидался отказ по валидации, получено: {excinfo.value}"


async def test_search_rejects_a_non_digit_category_id():
    with pytest.raises(ToolError):
        await server.avito_search("ноутбук", category_id="electronics")


async def test_search_maps_a_firewall_block_to_transport_down(monkeypatch):
    _patch_fetch(monkeypatch, (403, FIREWALL_BODY, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_search("ноутбук")

    assert "firewall" in str(excinfo.value).lower() or "block" in str(excinfo.value).lower()


async def test_search_maps_non_json_to_parser_drift(monkeypatch):
    """A 200 carrying the HTML block page instead of JSON is drift, not success."""
    _patch_fetch(monkeypatch, (200, "<html><body>Доступ ограничен</body></html>", "cdp"))

    with pytest.raises(ToolError):
        await server.avito_search("ноутбук")


async def test_search_reports_the_host_pause_with_its_remaining_cooldown(monkeypatch):
    """R-CDP-HOST-PAUSE: an open navigation breaker surfaces as rate_limited
    with retry_after_s, through the real _fetch path (tier-1 refusal, then
    CDP), not as a generic retryable transport failure. On the old code the
    budget's HostRefusingError was swallowed by _fetch's except Exception and
    the agent saw transport_down with no pause at all."""

    async def no_wait():
        return None

    def refused_get(url, proxy=None):
        return 403, "denied"

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_sync_curl_get", refused_get)
    budget = await _open_breaker("www.avito.ru")
    monkeypatch.setattr(chrome_cdp, "navigation_budget", lambda: budget)

    with pytest.raises(ToolError) as excinfo:
        await server.avito_search("ноутбук")

    error = json.loads(str(excinfo.value))
    assert error["error"] == "rate_limited"
    assert error["retryable"] is True
    assert error["retry_after_s"] == 47.0
    assert error["provider"] == "www.avito.ru"
    assert "paused for 47 s after 2 refusals" in error["message"]


async def test_search_warns_on_empty_items_with_nonzero_total(monkeypatch):
    payload = {"totalCount": 41, "items": []}
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_search("редкая вещь")

    assert result.count == 0
    assert result.meta.healthy is False
    assert "empty_items_with_nonzero_total" in result.meta.warnings


async def test_search_warns_when_a_required_key_family_vanishes(monkeypatch):
    """A payload that still parses but lost a whole alias family is drift the
    agent must see in the tool's own response, not only via doctor."""
    price_family = next(family for family in SEARCH_REQUIRED_FAMILIES if "items[].price" in family)
    payload = {
        **SEARCH_PAYLOAD,
        "items": [{key: value for key, value in item.items() if key != "price"} for item in SEARCH_PAYLOAD["items"]],
    }
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_search("ноутбук")

    assert result.count == 2
    assert result.meta.healthy is False
    # The warning names the WHOLE family (both envelopes' aliases, catalog
    # first), so compare against the registry's own join.
    assert f"shape_drift: avito_search: missing {'/'.join(price_family)}" in result.meta.warnings


async def test_search_a_complete_payload_carries_no_shape_drift_warning(monkeypatch):
    _patch_fetch(monkeypatch, _ok(SEARCH_PAYLOAD))

    result = await server.avito_search("thinkpad")

    assert result.meta.healthy is True
    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_search_reads_the_data_items_envelope_without_shape_drift(monkeypatch):
    """The third envelope ``_parse_search_items`` accepts: listings served only
    under data.items[] must answer healthy, not read as a page whose every key
    family vanished."""
    _patch_fetch(monkeypatch, _ok({"totalCount": 137, "data": {"items": SEARCH_PAYLOAD["items"]}}))

    result = await server.avito_search("ноутбук")

    assert result.count == 2
    assert result.meta.healthy is True
    assert not [w for w in result.meta.warnings if w.startswith("shape_drift:")]


async def test_search_flags_a_family_lost_in_the_data_items_envelope(monkeypatch):
    """The aliases make the check honest rather than silent: with the price keys
    gone, the missing family named is the price one, not the id family the old
    registry could only see."""
    price_family = next(family for family in SEARCH_REQUIRED_FAMILIES if "price" in family[0])
    payload = {
        "totalCount": 137,
        "data": {
            "items": [{key: value for key, value in item.items() if key != "price"} for item in SEARCH_PAYLOAD["items"]]
        },
    }
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_search("ноутбук")

    assert result.count == 2
    assert result.meta.healthy is False
    assert f"shape_drift: avito_search: missing {'/'.join(price_family)}" in result.meta.warnings


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        pytest.param(json.loads(FIREWALL_BODY), "challenge_required", id="firewall-captcha"),
        pytest.param({"too-many-requests": {"message": "Try again later"}}, "rate_limited", id="rate-key"),
        pytest.param({"error": {"code": 429, "message": "Request limit exceeded"}}, "rate_limited", id="rate-code"),
        pytest.param({"error": {"code": "CAPTCHA_REQUIRED"}}, "challenge_required", id="captcha-code"),
        pytest.param({"status": "error", "message": "Service unavailable"}, "transport_down", id="status-error"),
        pytest.param({"success": False, "data": {"items": []}}, "transport_down", id="success-false"),
        pytest.param({"error": "Unknown upstream failure", "items": []}, "transport_down", id="error-with-items"),
        pytest.param({"errors": [{"message": "Service unavailable"}]}, "transport_down", id="errors-list"),
    ],
)
async def test_search_rejects_http200_json_error_envelopes(monkeypatch, payload, error_code):
    """An upstream refusal is not a healthy zero-result classifieds search."""
    monkeypatch.setattr(server, "_pacer", server.Pacer(0))
    _patch_fetch(monkeypatch, _ok(payload))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_search("ноутбук")

    error = json.loads(str(excinfo.value))
    assert error["error"] == error_code
    assert error["provider"] == "avito"
    assert error["retryable"] is True
    assert server._pacer.consecutive_refusals == (0 if error_code == "transport_down" else 1)
    if error_code == "rate_limited":
        assert error["status_code"] == 429
    if error_code == "challenge_required":
        assert error["status_code"] == 403
        assert error["requires_user_action"] is True
        assert error["challenge_type"] == "captcha"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="absent-envelope"),
        pytest.param({"totalCount": 0}, id="absent-items-zero-total"),
        pytest.param({"catalog": {"listings": []}}, id="renamed-catalog-items"),
        pytest.param({"data": {"listings": []}}, id="renamed-data-items"),
        pytest.param({"items": None}, id="null-items"),
        pytest.param({"items": {}}, id="object-items"),
        pytest.param({"items": "[]"}, id="string-items"),
        pytest.param({"items": False}, id="boolean-items"),
        pytest.param({"catalog": None}, id="null-catalog"),
        pytest.param({"catalog": {"items": None}}, id="null-catalog-items"),
        pytest.param({"data": {"items": {}}}, id="object-data-items"),
    ],
)
async def test_search_rejects_missing_or_invalid_items_container(monkeypatch, payload):
    _patch_fetch(monkeypatch, _ok(payload))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_search("ноутбук")

    error = json.loads(str(excinfo.value))
    assert error["error"] == "parser_drift"
    assert error["provider"] == "avito"
    assert error["retryable"] is False


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"totalCount": 0, "items": []}, id="top-level"),
        pytest.param({"totalCount": 0, "catalog": {"items": []}}, id="catalog"),
        pytest.param({"totalCount": 0, "data": {"items": []}}, id="data"),
        pytest.param({"items": [], "error": None, "errors": [], "success": True}, id="no-error-markers"),
        pytest.param({"items": [], "error": False}, id="false-error"),
        pytest.param({"items": [], "error": ""}, id="blank-error"),
    ],
)
async def test_search_accepts_explicit_empty_items_container(monkeypatch, payload):
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_search("несуществующее объявление")

    assert result.status == "success"
    assert result.count == 0
    assert result.items == []
    assert result.meta.healthy is True
    assert result.meta.warnings == []


async def test_search_error_detection_ignores_listing_content(monkeypatch):
    _patch_fetch(
        monkeypatch,
        _ok({"items": [{"id": 4612345678, "title": "captcha firewall too-many-requests software", "price": 100}]}),
    )

    result = await server.avito_search("software")

    assert result.count == 1
    assert result.items[0].title == "captcha firewall too-many-requests software"
    assert result.meta.healthy is True


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        pytest.param(json.loads(FIREWALL_BODY), "challenge_required", id="firewall-captcha"),
        pytest.param({"error": {"code": 429}}, "rate_limited", id="rate-limit"),
        pytest.param({"error": "Service unavailable"}, "transport_down", id="outage"),
    ],
)
async def test_selfcheck_http200_json_errors_are_inconclusive(monkeypatch, payload, error_code):
    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    assert result.checks["search"].state == "inconclusive"
    assert result.checks["search"].reason == error_code


# ----------------------------------------------------------------- avito_card ----


async def test_card_parses_the_item_page(monkeypatch):
    _patch_item_page(monkeypatch, (200, CARD_HTML, "cdp"))

    result = await server.avito_card("4612345678")

    assert result.item_id == 4612345678
    assert result.title == "Ноутбук ThinkPad X1 Carbon"
    assert result.price_rub == 45500
    assert result.images == 1
    assert result.description == "В хорошем состоянии, батарея держит 6 часов."
    assert result.location is None
    assert result.posted_at is None
    assert result.views is None
    assert result.seller is None


async def test_card_accepts_a_slug_url(monkeypatch):
    _patch_item_page(monkeypatch, (200, CARD_HTML, "cdp"))

    result = await server.avito_card("https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678")

    assert result.item_id == 4612345678


@pytest.mark.parametrize(
    "bad",
    ["", "not-an-item", "https://example.com/1234567890", "123", "avito.ru/moskva"],
)
async def test_card_rejects_input_without_an_item_id(bad):
    with pytest.raises(ToolError):
        await server.avito_card(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "https://www.avito.ru/profile/x_1234567890",
        "https://avito.ru/account/x_1234567890",
        "https://www.avito.ru/orders/x_1234567890",
        "https://www.avito.ru/favorites/x_1234567890",
        "https://www.avito.ru/profile/favorites/saved/x_1234567890",
        "https://www.avito.ru/x_1234567890/extra",
        "https://www.avito.ru/api/1/anything",
        "https://www.avito.ru/api/personal/x_1234567890",
        "https://www.avito.ru/profile/items/x_1234567890",
        "https://www.avito.ru/profile",
        "https://www.avito.ru/account",
        "https://www.avito.ru/orders",
        "https://www.avito.ru/favorites",
    ],
    ids=[
        "profile-item-tail",
        "account-item-tail",
        "orders-item-tail",
        "favorites-item-tail",
        "deep-personal-path",
        "item-tail-then-path",
        "api-path",
        "api-prefix-item-shape",
        "profile-prefix-item-shape",
        "profile",
        "account",
        "orders",
        "favorites",
    ],
)
async def test_card_refuses_non_item_page_paths_before_transport(monkeypatch, bad):
    """R-INPUT-TRUST: the card opens pages in the operator's logged-in Chrome,
    so a full URL is fetched only when its path is an item-page shape. A
    personal prefix with an item-like tail (/profile/x_1234567890 passes the
    bare item-id regex) must be refused as bad_request before any navigation,
    not fetched and later misclassified."""
    calls = _patch_item_page_recording(monkeypatch, (200, CARD_HTML, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(bad)

    assert "bad_request" in str(excinfo.value)
    assert calls == []


@pytest.mark.parametrize(
    "bad",
    [
        "https://www.avito.ru/profile\\x_1234567890",
        "https://www.avito.ru/profile%2Fx_1234567890",
        "https://www.avito.ru/profile%2fx_1234567890",
        "https://www.avito.ru/profile%5Cx_1234567890",
        "https://www.avito.ru/profile%5cx_1234567890",
    ],
    ids=["backslash", "percent-2F", "percent-2f", "percent-5C", "percent-5c"],
)
async def test_card_refuses_separator_escapes_before_transport(monkeypatch, bad):
    """R-AVITO-ITEM-PATH: an item path is a plain ASCII slug, so a backslash or
    a percent escape is refused outright. Both make the browser read a path the
    check never saw — Chromium treats "\\" as "/" in http(s) URLs, and a server
    decodes %2F/%5C into a separator — so /profile\\x_1234567890 and
    /profile%2Fx_1234567890 would open the personal route this check exists to
    refuse. On the pre-fix code each of these was fetched, not refused."""
    calls = _patch_item_page_recording(monkeypatch, (200, CARD_HTML, "cdp"))

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card(bad)

    assert "bad_request" in str(excinfo.value)
    assert calls == []


async def test_card_still_opens_a_plain_city_category_slug_path(monkeypatch):
    """The same guard must not touch ordinary slugs: plain ASCII item paths keep
    working, fetched exactly as validated."""
    calls = _patch_item_page_recording(monkeypatch, (200, CARD_HTML, "cdp"))

    result = await server.avito_card("https://www.avito.ru/moskva/telefony/iphone_15_4612345678")

    assert calls == ["https://www.avito.ru/moskva/telefony/iphone_15_4612345678"]
    assert result.item_id == 4612345678


@pytest.mark.parametrize(
    ("raw", "expected_url"),
    [
        (
            "https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
            "https://www.avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
        ),
        (
            "http://avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
            "http://avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
        ),
        (
            "https://www.avito.ru/noutbuk_thinkpad_x1_carbon_4612345678/",
            "https://www.avito.ru/noutbuk_thinkpad_x1_carbon_4612345678/",
        ),
        (
            "https://avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678?context=1#details",
            "https://avito.ru/moskva/noutbuki/noutbuk_thinkpad_x1_carbon_4612345678",
        ),
    ],
    ids=["www-https", "no-www-http", "slug-with-trailing-slash", "query-fragment-dropped"],
)
async def test_card_keeps_opening_real_item_urls_normalized(monkeypatch, raw, expected_url):
    """Real item URLs keep working: city/category slug segments, with or
    without www, http or https, a trailing slash and a query string — the
    validated path is opened unchanged, the query/fragment dropped."""
    calls = _patch_item_page_recording(monkeypatch, (200, CARD_HTML, "cdp"))

    result = await server.avito_card(raw)

    assert calls == [expected_url]
    assert result.item_id == 4612345678


async def test_card_accepts_a_bare_id_item_url(monkeypatch):
    """/<id> is the item-page shape the bare-id branch itself navigates to."""
    calls = _patch_item_page_recording(monkeypatch, (200, CARD_HTML, "cdp"))

    result = await server.avito_card("https://www.avito.ru/4612345678")

    assert calls == ["https://www.avito.ru/4612345678"]
    assert result.item_id == 4612345678


async def test_card_maps_404_to_not_found(monkeypatch):
    _patch_item_page(monkeypatch, (404, "<html><body>Объявление не найдено</body></html>", "cdp"))

    with pytest.raises(ToolError):
        await server.avito_card("4612345678")


async def test_card_reports_the_host_pause_with_its_remaining_cooldown(monkeypatch):
    """R-CDP-HOST-PAUSE: same contract through _fetch_item_page, whose broad
    except Exception used to swallow the pause into a generic CDP failure."""

    async def no_wait():
        return None

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    budget = await _open_breaker("www.avito.ru")
    monkeypatch.setattr(chrome_cdp, "navigation_budget", lambda: budget)

    with pytest.raises(ToolError) as excinfo:
        await server.avito_card("4612345678")

    error = json.loads(str(excinfo.value))
    assert error["error"] == "rate_limited"
    assert error["retryable"] is True
    assert error["retry_after_s"] == 47.0
    assert error["provider"] == "www.avito.ru"
    assert "paused for 47 s after 2 refusals" in error["message"]


# --------------------------------------------------------------- avito_seller ----


async def test_seller_parses_reputation_and_active_count(monkeypatch):
    html = (
        "<title>ИП Сидоров - отзывы на Авито</title>"
        '<span data-marker="profile/score">4,8</span>'
        '<span data-marker="profile/summary">152 отзыва</span>'
    )
    _patch_item_page(monkeypatch, (200, html, "cdp"))

    result = await server.avito_seller("987654")

    assert result.seller is not None
    assert result.seller.rating_score == 4.8
    assert result.seller.rating_count == 152
    # Rendered reputation does not expose a trustworthy active-listing total.
    assert result.active_items is None


async def test_seller_warns_when_identity_is_missing(monkeypatch):
    _patch_item_page(monkeypatch, (200, '<span data-marker="profile/score">4,8</span>', "cdp"))

    result = await server.avito_seller("987654")

    assert result.seller.name is None
    assert "seller_identity_missing" in result.meta.warnings


# ------------------------------------------------------------ avito_selfcheck ----


async def test_selfcheck_reports_healthy_when_probes_parse(monkeypatch):
    _patch_fetch(monkeypatch, _ok(SEARCH_PAYLOAD))
    _patch_selfcheck_pages(monkeypatch, CARD_HTML)

    result = await server.avito_selfcheck()

    assert result.status == "success"
    assert result.healthy is True
    assert result.checks["search"].state == "healthy"


async def test_selfcheck_maps_a_block_to_inconclusive_never_drift(monkeypatch):
    """The firewall 403 is the expected datacenter state — it must not be read
    as parser drift."""
    _patch_fetch(monkeypatch, (403, FIREWALL_BODY, "curl_cffi"))

    result = await server.avito_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    assert all(c.state == "inconclusive" for c in result.checks.values())


async def test_selfcheck_flags_drift_when_a_200_fails_the_parse_smoke(monkeypatch):
    _patch_fetch(monkeypatch, _ok({"unexpected": "envelope"}))

    result = await server.avito_selfcheck()

    assert result.status == "drift_detected"
    assert result.healthy is False


async def test_selfcheck_accepts_the_captured_live_payload(monkeypatch):
    """The real captured js/items payload must read as healthy through the
    whole selfcheck — registry comparison included."""
    payload = json.loads((Path(__file__).parent / "fixtures" / "js_items_live.json").read_text(encoding="utf-8"))
    _patch_fetch(monkeypatch, _ok(payload))
    # This search capture is a different ad from the item-page capture. Adjust
    # only the inline synthetic Product, never either immutable fixture.
    _patch_selfcheck_pages(monkeypatch, CARD_HTML.replace("4612345678", "8105179339"))

    result = await server.avito_selfcheck()

    assert result.checks["search"].state == "healthy"
    assert result.status == "success"
    assert set(result.checks) == {"search", "card", "seller"}


async def test_selfcheck_flags_drift_when_a_key_family_vanishes(monkeypatch):
    """'id' renamed past every alias while the titles survive: the parse smoke
    would happily serve id-less items; the shape comparison must cry drift and
    name the lost family."""
    payload = json.loads((Path(__file__).parent / "fixtures" / "js_items_live.json").read_text(encoding="utf-8"))
    for item in payload["catalog"]["items"]:
        item["idRenamed"] = item.pop("id")

    _patch_fetch(monkeypatch, _ok(payload))

    result = await server.avito_selfcheck()

    assert result.status == "drift_detected"
    entry = result.checks["search"]
    assert entry.state == "drift"
    assert any("items[].id" in note for note in entry.notes)


# ------------------------------------------------------------------- helpers ----


def test_extract_item_id_handles_every_accepted_shape():
    assert server._extract_item_id("4612345678") == 4612345678
    assert server._extract_item_id("/moskva/noutbuki/x_4612345678") == 4612345678
    assert server._extract_item_id("https://www.avito.ru/moskva/x_4612345678?context=1") == 4612345678
    assert server._extract_item_id("ноутбук") is None
    assert server._extract_item_id("123") is None


def test_search_url_carries_query_page_location():
    url = server._build_search_url("ноутбук сsd", 2, "637640", "4")
    assert "q=%D0%BD%D0%BE%D1%83%D1%82%D0%B1%D1%83%D0%BA" in url or "q=" in url
    assert "p=2" in url
    assert "locationId=637640" in url
    assert "categoryId=4" in url
    assert url.startswith("https://www.avito.ru/web/1/js/items?")


# ------------------------------------------------------------ refusal backoff ----
#
# Avito bans by IP reputation and request rate, so a refusal must slow the next
# call down and a run of them must tell the operator what to change. Verified
# live July 2026: a burst of selfcheck calls turned a working source into an
# HTTP 439/429-class refusal.


def test_a_block_is_reported_to_the_pacer(monkeypatch):
    server._pacer.reset()

    with pytest.raises(ToolError):
        server._raise_for_fetch_failure(429, "too-many-requests", "curl_cffi", "search")

    assert server._pacer.consecutive_refusals == 1


def test_a_not_found_is_not_counted_as_a_refusal():
    """404 means the ad is gone. Slowing down does not bring it back."""
    server._pacer.reset()

    with pytest.raises(ToolError):
        server._raise_for_fetch_failure(404, "", "curl_cffi", "card")

    assert server._pacer.consecutive_refusals == 0


def test_a_standing_block_tells_the_operator_what_to_change():
    server._pacer.reset()
    for _ in range(server._pacer.rotation_threshold):
        server._pacer.record_refusal()

    message = str(server._blocked_error("curl_cffi"))

    assert "standing block" in message
    assert "residential" in message
    server._pacer.reset()


def test_a_single_block_does_not_cry_wolf():
    server._pacer.reset()
    server._pacer.record_refusal()

    assert "standing block" not in str(server._blocked_error("curl_cffi"))
    server._pacer.reset()


# ---------------------------------------------------------- debug redaction ----
#
# The tier-1 fallback tells the client it is trying CDP and logs the exception
# that knocked tier 1 out. A connect failure embeds the proxy URL — including
# credentials — into the exception text, so that debug line must pass through
# the same redaction as every other error path in this connector.


async def test_fetch_debug_never_leaks_tier1_exception_secrets(monkeypatch):
    async def no_wait():
        return None

    def exploding_get(url, proxy=None):
        raise ConnectionError("failed to connect to http://user:p/ss@proxy.example:3128")

    async def fake_cdp_fetch(url, ctx):
        return 200, '{"items": []}'

    monkeypatch.setattr(server, "_polite_wait", no_wait)
    monkeypatch.setattr(server, "_sync_curl_get", exploding_get)
    monkeypatch.setattr(server, "_cdp_fetch", fake_cdp_fetch)

    class RecordingCtx:
        def __init__(self):
            self.messages: list[str] = []

        async def debug(self, message):
            self.messages.append(str(message))

        async def info(self, message):
            self.messages.append(str(message))

    ctx = RecordingCtx()
    await server._fetch("https://www.avito.ru/web/1/js/items?q=x", ctx)

    debugged = "\n".join(ctx.messages)
    assert "p/ss" not in debugged, f"proxy password leaked into ctx.debug: {debugged}"
    assert "proxy.example:3128" in debugged, "redaction ate the host — the diagnosis must survive"
