"""Lamoda's refusal page is a timed wait, not a check a human passes (R-LAMODA-REFUSAL-HONEST).

The fixture is the page Lamoda served the scraping Chrome on 2026-11-16: HTTP 403,
"Проверка безопасности / Запрос отклонен", nothing to pass, gone by itself about
20 minutes later. Its heading made the old classifier answer challenge_required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from lamoda_connector import server
from mcp_core.domtest import JsdomUnavailable, run_extractor

FIXTURE = Path(__file__).parent / "fixtures" / "refusal_page_live.html"
URL = "https://www.lamoda.ru/catalogsearch/result/?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8"


def _payload() -> dict:
    """What the shipped search extractor reads from the refusal page."""
    try:
        return run_extractor(server._SEARCH_EXTRACT_JS, FIXTURE, page_url=URL)
    except JsdomUnavailable as exc:
        pytest.skip(str(exc))


@pytest.mark.req("R-LAMODA-REFUSAL-HONEST")
def test_refusal_page_is_not_a_challenge() -> None:
    payload = _payload()
    assert payload["items"] == []
    assert server._anti_bot_challenge(payload) is False


@pytest.mark.req("R-LAMODA-REFUSAL-HONEST")
async def test_search_on_the_refusal_page_answers_rate_limited_with_a_wait(monkeypatch) -> None:
    payload = _payload()

    async def fake_render(query, ctx):
        return payload

    monkeypatch.setattr(server, "_cdp_render_search", fake_render)
    with pytest.raises(ToolError) as excinfo:
        await server.lamoda_search("кроссовки")

    error = json.loads(str(excinfo.value))
    assert error["error"] == "rate_limited"
    assert error["provider"] == "lamoda"
    assert error["retry_after_s"] == server._REFUSAL_RETRY_S
    assert error.get("requires_user_action") is not True


@pytest.mark.req("R-LAMODA-REFUSAL-HONEST")
def test_a_real_challenge_is_still_a_challenge() -> None:
    payload = {"items": [], "title": "", "body_snippet": "Подтвердите, что вы не робот"}
    assert server._anti_bot_challenge(payload) is True
    assert server._refusal_page(payload) is False
