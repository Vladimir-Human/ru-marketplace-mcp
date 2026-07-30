"""Tests for ``_call`` — the RPC envelope every tool shares.

``test_transport.py`` pins the POST helper underneath and ``test_server.py`` pins
the tools above, but both stub ``_call`` itself, and the layer they skip is where
this connector's least obvious rule lives: **HTTP status is not the verdict**.
MPStats answers ``{"code": 403}`` for a rejected token, and its 5xx equivalents,
all behind HTTP 200. A test that mocks ``_call`` and asserts the tool surfaces
``auth_missing`` proves only that its own stub raised — it never touches the
mapping that would be wrong in production.

So the whole ladder runs here against ``httpx.MockTransport``: real ``_call``,
real ``_post_json_budgeted``, real cache. Offline and deterministic.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp_core.errors import ToolError
from mpstats_connector import server


@pytest.fixture(autouse=True)
def no_polite_gap(monkeypatch):
    async def _instant():
        return None

    monkeypatch.setattr(server, "_polite_wait", _instant)


@pytest.fixture(autouse=True)
def token_present(monkeypatch):
    monkeypatch.setattr(server, "_cookie_header", lambda: "mp_auth=test.jwt.here")


@pytest.fixture(autouse=True)
def empty_cache():
    """A shared module-level cache would leak state between these tests."""
    server._cache.clear()
    yield
    server._cache.clear()


def wire(monkeypatch, handler):
    """Point ``_call``'s client at a mock transport, counting the requests made."""
    calls = {"n": 0}

    async def counting(request):
        calls["n"] += 1
        return await handler(request) if callable(handler) else handler

    def _client():
        return httpx.AsyncClient(transport=httpx.MockTransport(counting), timeout=5.0)

    monkeypatch.setattr(server, "_client", _client)
    return calls


def payload(excinfo) -> dict:
    return json.loads(str(excinfo.value))


# --------------------------------------------------------------------------- #
# The inner verdict
# --------------------------------------------------------------------------- #


async def test_inner_403_behind_http_200_is_auth_missing(monkeypatch):
    """The rule the whole file exists for: HTTP 200 + code 403 is an auth failure."""

    async def handler(request):
        return httpx.Response(200, text='{"code":403,"message":"Unauthorized"}')

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "auth_missing"


async def test_inner_500_behind_http_200_is_transport_down(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text='{"code":500,"message":"boom"}')

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "transport_down"


async def test_inner_200_returns_the_parsed_object(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text='{"code":200,"items":{"1":{"Sku":1}}}')

    wire(monkeypatch, handler)
    data = await server._call({"Request": "test"}, label="probe")
    assert data["items"]["1"]["Sku"] == 1


async def test_missing_inner_code_is_accepted(monkeypatch):
    """Absent ``code`` is not a failure — only a present non-200 one is."""

    async def handler(request):
        return httpx.Response(200, text='{"items":{}}')

    wire(monkeypatch, handler)
    assert await server._call({"Request": "test"}, label="probe") == {"items": {}}


# --------------------------------------------------------------------------- #
# The ladder above the verdict
# --------------------------------------------------------------------------- #


async def test_http_429_is_rate_limited(monkeypatch):
    async def handler(request):
        return httpx.Response(429, text="slow down")

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "rate_limited"


async def test_html_body_reads_as_a_block_not_as_drift(monkeypatch):
    """A challenge page is a transport problem; calling it drift sends the
    maintainer hunting for a schema change that never happened."""

    async def handler(request):
        return httpx.Response(200, text="<!DOCTYPE html><html><body>captcha</body></html>")

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "transport_down"


async def test_non_json_body_is_parser_drift(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text="not json at all")

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "parser_drift"


async def test_json_array_instead_of_object_is_parser_drift(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text="[1,2,3]")

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "parser_drift"


async def test_no_token_fails_before_any_request(monkeypatch):
    """The auth gate must short-circuit ahead of the network, not after it."""
    monkeypatch.setattr(server, "_cookie_header", lambda: "")

    def exploding_client():
        raise AssertionError("_call built a client without a token")

    monkeypatch.setattr(server, "_client", exploding_client)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert payload(excinfo)["error"] == "auth_missing"


# --------------------------------------------------------------------------- #
# What the cache is allowed to keep
# --------------------------------------------------------------------------- #


async def test_success_is_cached(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text='{"code":200,"items":{}}')

    calls = wire(monkeypatch, handler)
    body = {"Request": "cached"}
    await server._call(body, label="probe")
    await server._call(body, label="probe")
    assert calls["n"] == 1, "a second identical call should be served from cache"


async def test_auth_failure_is_not_cached(monkeypatch):
    """A rejected token behind HTTP 200 must not be replayed for the whole TTL:
    the user pastes a fresh cookie and the next call has to reach the network."""

    async def handler(request):
        return httpx.Response(200, text='{"code":403,"message":"Unauthorized"}')

    calls = wire(monkeypatch, handler)
    body = {"Request": "denied"}
    for _ in range(2):
        with pytest.raises(ToolError):
            await server._call(body, label="probe")
    assert calls["n"] == 2, "an auth failure was served from cache"


async def test_inner_error_is_not_cached(monkeypatch):
    """Same for a transient upstream fault, which also arrives behind HTTP 200."""

    async def handler(request):
        return httpx.Response(200, text='{"code":500,"message":"boom"}')

    calls = wire(monkeypatch, handler)
    body = {"Request": "broken"}
    for _ in range(2):
        with pytest.raises(ToolError):
            await server._call(body, label="probe")
    assert calls["n"] == 2, "an upstream error was served from cache"


# --------------------------------------------------------------------------- #
# The secret must not ride out on an error
# --------------------------------------------------------------------------- #

_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwYWlkLXVzZXIifQ.sekretsignature12345"


async def test_a_transport_exception_does_not_carry_the_token(monkeypatch):
    """httpx puts the offending header into the exception text when a token is
    malformed — a trailing newline off a copy-paste is enough. That text becomes
    the ToolError the model reads and the client transcript keeps, so it has to
    be redacted on the way out, not only on the way to the log."""
    monkeypatch.setattr(server, "_cookie_header", lambda: f"mp_auth={_TOKEN}")

    async def handler(request):
        raise httpx.ConnectError(f"failed sending header Cookie: mp_auth={_TOKEN}")

    wire(monkeypatch, handler)
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    body = str(excinfo.value)
    assert payload(excinfo)["error"] == "transport_down"
    assert _TOKEN not in body
    assert "eyJ" not in body


async def test_a_transport_error_string_does_not_carry_the_token(monkeypatch):
    """Same for the classified-error branch, which reports a string rather than
    raising: `_post_json_budgeted` returns `err` and `_call` re-raises it."""
    monkeypatch.setattr(server, "_cookie_header", lambda: f"mp_auth={_TOKEN}")

    async def fake_post(*args, **kwargs):
        return None, None, f"network: refused while sending mp_auth={_TOKEN}"

    monkeypatch.setattr(server, "_post_json_budgeted", fake_post)
    monkeypatch.setattr(
        server, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    with pytest.raises(ToolError) as excinfo:
        await server._call({"Request": "test"}, label="probe")
    assert _TOKEN not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Pacing: a refusal has to slow the next call down
# --------------------------------------------------------------------------- #


async def test_a_refusal_lengthens_the_gap(monkeypatch):
    """MPStats' offer makes a rate above one request per five seconds grounds for
    blocking the account, and a blocked account is not refunded — so walking back
    into a refusal at the same speed costs the user money, not us. The shared
    pacer backs off; a hand-rolled sleep would not."""
    server._pacer.reset()

    async def handler(request):
        return httpx.Response(429, text="slow down")

    wire(monkeypatch, handler)
    with pytest.raises(ToolError):
        await server._call({"Request": "denied"}, label="probe")
    assert server._pacer.consecutive_refusals == 1
    server._pacer.reset()


async def test_a_good_call_clears_the_backoff(monkeypatch):
    server._pacer.reset()
    server._pacer.record_refusal()

    async def handler(request):
        return httpx.Response(200, text='{"code":200,"items":{}}')

    wire(monkeypatch, handler)
    await server._call({"Request": "ok"}, label="probe")
    assert server._pacer.consecutive_refusals == 0
