"""R-WB-SELFCHECK-DOM: doctor checks the configured transport, entirely offline.

Healthy DOM payloads reuse the dated captures in test_dom_transport, unchanged.
The card baseline is set to that capture's SKU; error-path payloads are synthetic.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastmcp.exceptions import ToolError
from mcp_core.errors import ChallengeRequiredError, ParserDriftError, TransportDownError
from wb_connector import dom_transport as D
from wb_connector import server

from .test_dom_transport import CARD_PAYLOAD, SEARCH_PAYLOAD


@pytest.fixture
def selfcheck_fakes(monkeypatch):
    monkeypatch.setattr(server._settings, "transport", "dom")
    monkeypatch.setattr(server, "_SELFCHECK_NM", 1345073040)
    fakes = SimpleNamespace(
        render_calls=[],
        http_urls=[],
        rendered={"card": CARD_PAYLOAD, "search": SEARCH_PAYLOAD},
        http_responses={"card.wb.ru": (403, "", None), "search.wb.ru": (403, "", None)},
    )

    async def render(url, js, want):
        fakes.render_calls.append((url, js, want))
        response = fakes.rendered[want]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    async def get_text(client, url):
        fakes.http_urls.append(url)
        host = urlsplit(url).hostname
        if host in fakes.http_responses:
            return fakes.http_responses[host]
        if host in server.WB_REVIEW_HOSTS:
            payload = {"feedbacks": [{"text": "ok", "productValuation": 5}]}
        elif host == "search-goods.wildberries.ru":
            payload = [server._SELFCHECK_NM]
        elif host and host.endswith(".wbbasket.ru"):
            payload = {"imt_id": server._SELFCHECK_IMT}
        else:
            raise AssertionError(f"unexpected HTTP URL: {url}")
        return 200, json.dumps(payload), None

    @asynccontextmanager
    async def client():
        yield object()

    async def no_wait():
        pass

    monkeypatch.setattr(D, "render", render)
    monkeypatch.setattr(server, "_fresh_get_text", get_text)
    monkeypatch.setattr(server, "_safe_get_text", get_text)
    monkeypatch.setattr(server, "_wb_client", client)
    monkeypatch.setattr(server, "_polite_wait", no_wait)
    return fakes


def assert_dom_calls(fakes):
    assert fakes.render_calls == [
        (D.card_url(server._SELFCHECK_NM), D.CARD_EXTRACT_JS, "card"),
        (D.search_url(server._SELFCHECK_SEARCH_QUERY, 1), D.SEARCH_EXTRACT_JS, "search"),
    ]
    hosts = {urlsplit(url).hostname for url in fakes.http_urls}
    assert "card.wb.ru" not in hosts
    assert "search.wb.ru" not in hosts
    assert hosts == {
        server.WB_REVIEW_HOSTS[0],
        "search-goods.wildberries.ru",
        server._basket_for_sku(server._SELFCHECK_NM),
    }


async def test_dom_selfcheck_uses_the_tools_parsers_and_avoids_api_hosts(monkeypatch, selfcheck_fakes):
    """R-WB-SELFCHECK-DOM: captured named/priced products prove both DOM paths."""
    parsed = []
    parse_card, parse_search = D.parse_card_page, D.parse_search_page

    def card(payload, nm):
        parsed.append(("card", payload, nm))
        return parse_card(payload, nm)

    def search(payload):
        parsed.append(("search", payload))
        return parse_search(payload)

    monkeypatch.setattr(D, "parse_card_page", card)
    monkeypatch.setattr(D, "parse_search_page", search)
    result = (await server.wb_selfcheck()).model_dump()
    assert result["status"] == "success"
    assert set(result["checks"]) == {"card", "reviews", "search_goods", "search_v9", "root_basket"}
    for key, baseline in (("card", str(server._SELFCHECK_NM)), ("search_v9", server._SELFCHECK_SEARCH_QUERY)):
        entry = result["checks"][key]
        assert entry["state"] == "healthy" and entry["ok"] is True
        assert entry["transport"] == "dom" and entry["baseline"] == baseline
    assert result["checks"]["card"]["price"] == 5063.0
    assert result["checks"]["search_v9"]["recovered_ids"] == 4
    assert result["checks"]["search_v9"]["priced"] == 4
    assert parsed == [("card", CARD_PAYLOAD, server._SELFCHECK_NM), ("search", SEARCH_PAYLOAD)]
    note = " ".join(result["checks"]["search_goods"]["notes"])
    assert "wb_search" in note and "WB_TRANSPORT=dom" in note and "not use" in note
    assert_dom_calls(selfcheck_fakes)


@pytest.mark.parametrize("want", ["card", "search"])
@pytest.mark.parametrize(
    ("error", "state", "reason"),
    [
        (ChallengeRequiredError("captcha", provider="wb"), "inconclusive", "blocked"),
        (TransportDownError("browser check did not clear", provider="wb"), "inconclusive", "transport_down"),
        (ParserDriftError("extractor returned invalid JSON", provider="wb"), "drift", "schema_drift"),
    ],
)
async def test_dom_selfcheck_maps_typed_render_errors_once(selfcheck_fakes, want, error, state, reason):
    """R-WB-SELFCHECK-DOM: codes, not error-message words, decide the verdict."""
    selfcheck_fakes.rendered[want] = ToolError(json.dumps(error.to_dict()))
    result = (await server.wb_selfcheck()).model_dump()
    entry = result["checks"]["card" if want == "card" else "search_v9"]
    assert entry["state"] == state and entry["reason"] == reason
    assert entry["ok"] is (False if state == "drift" else None)
    assert entry["transport"] == "dom" and entry["baseline"]
    assert result["status"] == ("drift_detected" if state == "drift" else "inconclusive")
    assert_dom_calls(selfcheck_fakes)


@pytest.mark.parametrize("want", ["card", "search"])
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("unexpected dependency failure"),
        ToolError("challenge_required is not a JSON error envelope"),
        ToolError('["parser_drift"]'),
        ToolError('{"error": ["transport_down"]}'),
        ToolError('{"error": "unknown", "message": "captcha"}'),
    ],
)
async def test_dom_selfcheck_unexpected_errors_are_inconclusive(selfcheck_fakes, want, error):
    """R-WB-SELFCHECK-DOM: malformed/unknown errors never escape or become drift."""
    selfcheck_fakes.rendered[want] = error
    entry = (await server.wb_selfcheck()).model_dump()["checks"]["card" if want == "card" else "search_v9"]
    assert entry["state"] == "inconclusive" and entry["reason"] == "dependency_unavailable"
    assert entry["transport"] == "dom" and entry["ok"] is None
    assert_dom_calls(selfcheck_fakes)


@pytest.mark.parametrize(
    ("payload", "state", "reason"),
    [
        ({"tiles": [], "page_says_empty": False}, "drift", "schema_drift"),
        ({"tiles": [], "page_says_empty": True}, "inconclusive", "baseline_gone"),
        ({"tiles": [{"nm": "invalid", "lines": ["100 ₽"]}]}, "drift", "schema_drift"),
        ({"tiles": [{"nm": "123", "label": "Товар", "lines": []}]}, "drift", "schema_drift"),
    ],
)
async def test_dom_selfcheck_search_requires_identified_priced_products(selfcheck_fakes, payload, state, reason):
    """R-WB-SELFCHECK-DOM: only WB's own empty notice excuses missing products."""
    selfcheck_fakes.rendered["search"] = payload
    entry = (await server.wb_selfcheck()).model_dump()["checks"]["search_v9"]
    assert entry["state"] == state and entry["reason"] == reason
    assert entry["transport"] == "dom"
    assert_dom_calls(selfcheck_fakes)


@pytest.mark.parametrize(
    ("payload", "state", "reason"),
    [
        ({"lines": []}, "drift", "schema_drift"),
        (
            {"h1": "Baseline product", "lines": ["Артикул", "1345073040", "Нет в наличии"]},
            "inconclusive",
            "baseline_gone",
        ),
        ({"h1": "Baseline product", "lines": ["Купить", "Артикул", "1345073040"]}, "inconclusive", "baseline_gone"),
        ({"lines": ["100 ₽", "Купить", "Артикул", "1345073040"]}, "drift", "schema_drift"),
    ],
)
async def test_dom_selfcheck_card_distinguishes_oos_from_missing_anchors(selfcheck_fakes, payload, state, reason):
    """R-WB-SELFCHECK-DOM: unavailable offers are baseline rot, not parser drift."""
    selfcheck_fakes.rendered["card"] = payload
    entry = (await server.wb_selfcheck()).model_dump()["checks"]["card"]
    assert entry["state"] == state and entry["reason"] == reason
    assert entry["transport"] == "dom"
    assert_dom_calls(selfcheck_fakes)


@pytest.mark.parametrize("want", ["card", "search"])
async def test_dom_selfcheck_wall_timeout_allows_the_dom_waits(monkeypatch, selfcheck_fakes, want):
    """R-WB-SELFCHECK-DOM: bound the whole subcheck without clipping page waits."""
    monkeypatch.setattr(D._settings, "dom_wait_s", 60.0)
    monkeypatch.setattr(D._settings, "dom_settle_ms", 10000)
    deadlines = []
    real_timeout = asyncio.timeout

    def timeout(delay):
        deadlines.append(delay)
        return real_timeout(0.01)

    async def render(url, js, path):
        selfcheck_fakes.render_calls.append((url, js, path))
        if path == want:
            await asyncio.Event().wait()
        return deepcopy(selfcheck_fakes.rendered[path])

    monkeypatch.setattr(server.asyncio, "timeout", timeout)
    monkeypatch.setattr(D, "render", render)
    result = (await server.wb_selfcheck()).model_dump()
    entry = result["checks"]["card" if want == "card" else "search_v9"]
    assert entry["state"] == "inconclusive" and entry["reason"] == "timeout"
    # _wait_ready may finish its last 15s evaluation after its deadline; the
    # final extractor has a separate 30s wait, after navigation/settling/gap.
    minimum = D._settings.dom_wait_s + D._settings.dom_settle_ms / 1000 + D._settings.dom_min_gap + 15 + 30
    assert deadlines[0] >= minimum and deadlines[3] >= minimum
    assert deadlines[1:3] == [45, 45] and deadlines[4] == 45
    assert_dom_calls(selfcheck_fakes)


@pytest.mark.parametrize("want", ["card", "search"])
async def test_dom_selfcheck_notes_are_bounded_and_redacted(monkeypatch, selfcheck_fakes, want):
    """R-WB-SELFCHECK-DOM: parser notes and exception diagnostics are safe to show."""
    secret = "https://user:secret@proxy.test/?token=private " + "x" * 400
    parser = D.parse_card_page if want == "card" else D.parse_search_page

    def parse(*args):
        items, _warnings = parser(*args)
        return items, [secret] * 12

    monkeypatch.setattr(D, "parse_card_page" if want == "card" else "parse_search_page", parse)
    entry = (await server.wb_selfcheck()).model_dump()["checks"]["card" if want == "card" else "search_v9"]
    assert 0 < len(entry["notes"]) <= 10
    assert all(len(note) <= 200 and "secret" not in note and "private" not in note for note in entry["notes"])
    assert_dom_calls(selfcheck_fakes)


async def test_api_selfcheck_keeps_http_behavior_and_never_renders(monkeypatch, selfcheck_fakes):
    """R-WB-SELFCHECK-DOM: API refusals and the legacy probe retain their verdicts."""
    monkeypatch.setattr(server._settings, "transport", "api")
    result = (await server.wb_selfcheck()).model_dump()
    assert result["status"] == "inconclusive"
    assert selfcheck_fakes.render_calls == []
    for key in ("card", "search_v9"):
        assert result["checks"][key]["state"] == "inconclusive"
        assert result["checks"][key]["reason"] == "transport_down"
        assert "transport" not in result["checks"][key]
    for key in ("reviews", "root_basket", "search_goods"):
        assert result["checks"][key]["state"] == "healthy"
    assert result["checks"]["search_goods"]["notes"] == []
    hosts = {urlsplit(url).hostname for url in selfcheck_fakes.http_urls}
    assert {"card.wb.ru", "search.wb.ru"} <= hosts


async def test_dom_reviews_and_root_tools_still_use_http(selfcheck_fakes):
    """R-WB-SELFCHECK-DOM: the untouched checks match the tools' HTTP transport."""
    root = await server.wb_root_info(server._SELFCHECK_NM)
    reviews = await server.wb_reviews(server._SELFCHECK_IMT)
    assert root.imt_id == server._SELFCHECK_IMT and reviews.pool_size == 1
    assert selfcheck_fakes.render_calls == []
    assert {urlsplit(url).hostname for url in selfcheck_fakes.http_urls} == {
        server._basket_for_sku(server._SELFCHECK_NM),
        server.WB_REVIEW_HOSTS[0],
    }
