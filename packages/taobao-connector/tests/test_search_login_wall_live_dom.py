"""The real extractor against the LIVE login wall captured 2026-09-10.

Taobao started serving logged-out sessions a search page that is a login wall
with an EMPTY document title: 32 links including
//login.taobao.com/member/login.jhtml and
//reg.taobao.com/member/new_register.jhtml, zero item.taobao.com anchors, the
visible text «亲，请登录 免费注册» and nothing else (provenance in
``fixtures/search_login_wall_live.provenance.json``). The title-only
``_login_wall`` of the time could not see it, so the wall read as parser
drift: taobao_search raised ParserDriftError ("zero items") and
taobao_selfcheck reported drift/parse_smoke_failed. Doctrinally a login wall
is a session event — transport_down with the log-in fix inline for the tools,
inconclusive(login_wall) for the canary, NEVER drift.

These tests run the real extractor JS over the captured markup (jsdom; skips
without it) and feed the REAL payload through the Python classifier and
through the tool and selfcheck paths. All offline — no CDP, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from mcp_core.domtest import JsdomUnavailable, run_extractor
from taobao_connector import server

FIXTURE = Path(__file__).parent / "fixtures" / "search_login_wall_live.html"
PAGE_URL = "https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA&page=1"


@pytest.fixture(autouse=True)
def _no_cache():
    server._cache._data.clear()


def _wall_payload() -> dict:
    """The real extractor's payload over the captured wall."""
    try:
        return run_extractor(server._SEARCH_EXTRACT_JS, FIXTURE, page_url=PAGE_URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


def _patch_render(monkeypatch, payload):
    async def fake_render(url, extract_js, wait_ms, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render", fake_render)


def _tool_error_payload(excinfo) -> dict:
    """raise_tool_error serializes a ConnectorError as JSON inside ToolError."""
    return json.loads(str(excinfo.value))


# ------------------------------------------------------- extractor (transport) ----


def test_extractor_surfaces_the_structural_wall_markers() -> None:
    """The JS stays dumb transport: raw anchor counts and a visible-text
    snippet, no verdicts — the wall decision is Python's."""
    payload = _wall_payload()
    assert payload["title"] == "", "this wall variant carries an EMPTY title"
    assert payload["items"] == []
    assert payload["anchors_total"] == 32
    assert payload["login_anchors"] == 2
    assert "请登录" in payload["body_snippet"]
    assert "免费注册" in payload["body_snippet"]


# ------------------------------------------------------ classifier (decision) ----


def test_the_title_less_wall_is_classified_as_a_login_wall() -> None:
    """The fix proper: with no title to look at, the structural markers —
    login/register routes plus login wording on a link-poor page — identify
    the wall. The title marker cannot fire here, and no longer has to."""
    payload = _wall_payload()
    markers = server._login_wall_markers(payload)
    assert "login_routes" in markers
    assert "body_text" in markers
    assert "title" not in markers
    assert server._login_wall(payload) is True


# -------------------------------------------------------------- tool contract ----


async def test_search_over_the_live_wall_is_transport_down_never_drift(monkeypatch) -> None:
    """The real wall payload must produce TransportDownError with the log-in
    fix inline — never ParserDriftError ("zero items"), which would conflate
    "we were refused" with "the data changed shape"."""
    _patch_render(monkeypatch, _wall_payload())

    with pytest.raises(ToolError) as excinfo:
        await server.taobao_search("手机")

    error = _tool_error_payload(excinfo)
    assert error["error"] == "transport_down"
    assert "login wall" in error["message"].lower()
    assert "log into taobao.com" in error["message"].lower()
    assert error["retryable"] is True


async def test_selfcheck_over_the_live_wall_is_inconclusive_never_drift(monkeypatch) -> None:
    """Tri-state doctrine: a session wall is inconclusive(login_wall) — the
    canary could not judge drift because it never saw the catalog."""
    _patch_render(monkeypatch, _wall_payload())

    result = await server.taobao_selfcheck()

    assert result.status == "inconclusive"
    assert result.healthy is None
    search = result.checks["search"]
    assert search.state == "inconclusive"
    assert search.reason == "login_wall", "a wall must never read as parse_smoke_failed drift"
    assert any("login wall" in note for note in search.notes)


# ------------------------------------------------------------- capture hygiene ----


def test_the_fixture_carries_no_session_data() -> None:
    """The capture came from a logged-out profile, trimmed of every
    script/style block (where all tracking config lived, with empty values).
    Pin that promise: no tokens, cookies or user identifiers in the committed
    fixture — a public login wall and nothing more."""
    text = FIXTURE.read_text(encoding="utf-8").lower()
    for pattern in (
        "_tb_token_",
        "_m_h5_tk",
        "sgcookie",
        "tracknick",
        "uidaplus",
        "exparams",
        "loginid",
        "passport",
    ):
        assert pattern not in text, f"session-like marker {pattern!r} in the fixture"
    assert "<script" not in text, "script blocks were supposed to be trimmed out"
