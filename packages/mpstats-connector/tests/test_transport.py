"""Tests for ``_post_json_budgeted`` — the connector-local POST transport.

The helper deliberately mirrors the invariants of ``mcp_core.transport.get_text_budgeted``
(the shared runtime is GET-only, so the POST path lives here). Those invariants are the
package's only nontrivial copied logic, so they are pinned here: a future edit that
breaks the byte cap, the wall-clock budget, the error classification, or the
retry-only-transport-faults rule fails loudly in this file instead of silently in production.

All network is an ``httpx.MockTransport``, so the suite stays offline and deterministic.
The polite gate is monkeypatched out — pacing has its own assertions elsewhere, and
sleeping through ``min_gap`` per retry would only slow the suite down.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from mpstats_connector import server


@pytest.fixture(autouse=True)
def no_polite_gap(monkeypatch):
    async def _instant():
        return None

    monkeypatch.setattr(server, "_polite_wait", _instant)


@pytest.fixture(autouse=True)
def token_present(monkeypatch):
    """The helper reads the cookie per call; give it one so the header is exercised."""
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=test.jwt.here")


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def test_posts_json_with_cookie_and_returns_status_text():
    seen: dict = {}

    async def handler(request):
        seen["method"] = request.method
        seen["cookie"] = request.headers.get("cookie", "")
        seen["body"] = request.content.decode()
        return httpx.Response(200, text='{"code":200,"items":{}}')

    async with make_client(handler) as client:
        status, text, err = await server._post_json_budgeted(
            client,
            "https://x.test/pluginapi",
            {"Request": "getAnalitics", "Sku": [1]},
            max_bytes=1000,
            wall_timeout_s=5,
            retries=0,
            backoff_s=0,
        )

    assert (status, err) == (200, None)
    assert text == '{"code":200,"items":{}}'
    assert seen["method"] == "POST"
    assert seen["cookie"] == "mp_auth=test.jwt.here"
    assert '"Request": "getAnalitics"' in seen["body"] or '"Request":"getAnalitics"' in seen["body"]


async def test_body_cap_returns_error_instead_of_raising():
    async def handler(_request):
        return httpx.Response(200, text="x" * 5000)

    async with make_client(handler) as client:
        status, text, err = await server._post_json_budgeted(
            client, "https://x.test/big", {}, max_bytes=100, wall_timeout_s=5, retries=0, backoff_s=0
        )

    assert status == 200
    assert text is None
    assert err is not None and "body exceeds 100 bytes" in err


async def test_transport_error_is_retried_then_succeeds():
    attempts: list[int] = []

    async def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, text="ok")

    async with make_client(handler) as client:
        status, text, err = await server._post_json_budgeted(
            client, "https://x.test/a", {}, max_bytes=1000, wall_timeout_s=30, retries=2, backoff_s=0
        )

    assert (status, text, err) == (200, "ok", None)
    assert len(attempts) == 3


async def test_http_status_is_never_retried():
    """429/5xx come back immediately: a status is an answer, not a transient fault."""
    attempts: list[int] = []

    async def handler(_request):
        attempts.append(1)
        return httpx.Response(500, text="upstream broke")

    async with make_client(handler) as client:
        status, _text, err = await server._post_json_budgeted(
            client, "https://x.test/a", {}, max_bytes=1000, wall_timeout_s=30, retries=2, backoff_s=0
        )

    assert status == 500
    assert err is None
    assert len(attempts) == 1


async def test_transport_error_is_classified_when_retries_spent():
    async def handler(request):
        raise httpx.ConnectError("refused", request=request)

    async with make_client(handler) as client:
        status, text, err = await server._post_json_budgeted(
            client, "https://x.test/a", {}, max_bytes=1000, wall_timeout_s=30, retries=1, backoff_s=0
        )

    assert (status, text) == (0, None)
    assert err is not None and err.startswith("network:")
    assert "after 2 attempts" in err


async def test_httpx_timeout_is_classified_as_timeout():
    async def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with make_client(handler) as client:
        _, _, err = await server._post_json_budgeted(
            client, "https://x.test/a", {}, max_bytes=1000, wall_timeout_s=30, retries=0, backoff_s=0
        )

    assert err is not None and err.startswith("timeout:")


async def test_wall_clock_budget_bounds_a_hung_attempt():
    """The deadline fires mid-request, so a hung upstream cannot outlast the budget."""

    async def handler(_request):
        await asyncio.sleep(10)
        return httpx.Response(200, text="never arrives")

    async with make_client(handler) as client:
        status, text, err = await server._post_json_budgeted(
            client, "https://x.test/slow", {}, max_bytes=1000, wall_timeout_s=0.05, retries=2, backoff_s=0
        )

    assert (status, text) == (0, None)
    assert err is not None and err.startswith("timeout:")


async def test_empty_token_sends_no_cookie_header():
    """No configured token must never become a ``Cookie:`` header on the wire."""
    seen: dict = {}

    async def handler(request):
        seen["has_cookie"] = "cookie" in request.headers
        return httpx.Response(200, text="{}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(server, "_cookie_header", lambda: "")
    async with make_client(handler) as client:
        await server._post_json_budgeted(
            client, "https://x.test/a", {}, max_bytes=1000, wall_timeout_s=5, retries=0, backoff_s=0
        )
    monkeypatch.undo()

    assert seen["has_cookie"] is False
