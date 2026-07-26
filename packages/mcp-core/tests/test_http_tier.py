"""Tests for the anonymous HTTP tier.

All network is served by an ``httpx.MockTransport``, so the suite is fully
offline and deterministic.
"""

from __future__ import annotations

import httpx
import pytest
from mcp_core.transport import (
    RETRYABLE_STATUSES,
    BodyTooLargeError,
    RateLimiter,
    build_client,
    get_text_with_retries,
    proxy_from_env,
)


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def test_returns_status_and_body():
    async def handler(_request):
        return httpx.Response(200, text='{"ok":true}')

    async with make_client(handler) as client:
        status, text = await get_text_with_retries(client, "https://x.test/a", max_bytes=1000)

    assert status == 200
    assert text == '{"ok":true}'


async def test_body_cap_is_enforced():
    async def handler(_request):
        return httpx.Response(200, text="x" * 5000)

    async with make_client(handler) as client:
        with pytest.raises(BodyTooLargeError):
            await get_text_with_retries(client, "https://x.test/big", max_bytes=100)


async def test_transport_error_is_retried_then_succeeds():
    attempts = []

    async def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, text="ok")

    async with make_client(handler) as client:
        status, text = await get_text_with_retries(client, "https://x.test/a", max_bytes=1000, retries=2, backoff_s=0)

    assert (status, text) == (200, "ok")
    assert len(attempts) == 3


async def test_transport_error_propagates_when_budget_exhausted():
    async def handler(request):
        raise httpx.ConnectTimeout("boom", request=request)

    async with make_client(handler) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await get_text_with_retries(client, "https://x.test/a", max_bytes=1000, retries=1, backoff_s=0)


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUSES))
async def test_gateway_statuses_are_retried(status):
    """502/503/504 are transient upstream overload — worth one more attempt."""
    attempts = []

    async def handler(_request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(status, text="gateway")
        return httpx.Response(200, text="recovered")

    async with make_client(handler) as client:
        got_status, text = await get_text_with_retries(
            client, "https://x.test/a", max_bytes=1000, retries=2, backoff_s=0
        )

    assert (got_status, text) == (200, "recovered")
    assert len(attempts) == 2


async def test_rate_limit_status_is_never_retried():
    """Retrying a 429 deepens the rate-limit hole, so it must pass straight through."""
    attempts = []

    async def handler(_request):
        attempts.append(1)
        return httpx.Response(429, text="slow down")

    async with make_client(handler) as client:
        status, _ = await get_text_with_retries(client, "https://x.test/a", max_bytes=1000, retries=3, backoff_s=0)

    assert status == 429
    assert len(attempts) == 1


async def test_client_error_is_not_retried():
    attempts = []

    async def handler(_request):
        attempts.append(1)
        return httpx.Response(404, text="nope")

    async with make_client(handler) as client:
        status, _ = await get_text_with_retries(client, "https://x.test/a", max_bytes=1000, retries=3, backoff_s=0)

    assert status == 404
    assert len(attempts) == 1


async def test_exhausted_gateway_retries_return_the_real_response():
    """After the budget runs out the caller still gets the status, not an exception."""

    async def handler(_request):
        return httpx.Response(503, text="still down")

    async with make_client(handler) as client:
        status, text = await get_text_with_retries(client, "https://x.test/a", max_bytes=1000, retries=2, backoff_s=0)

    assert status == 503
    assert text == "still down"


async def test_error_bodies_are_truncated_by_default():
    async def handler(_request):
        return httpx.Response(400, text="e" * 5000)

    async with make_client(handler) as client:
        with pytest.raises(BodyTooLargeError):
            await get_text_with_retries(client, "https://x.test/a", max_bytes=100_000, error_body_max_bytes=10)


async def test_error_body_cap_can_be_disabled():
    """Detsky Mir's search route answers 404 with a full page of real content."""

    async def handler(_request):
        return httpx.Response(404, text="p" * 5000)

    async with make_client(handler) as client:
        status, text = await get_text_with_retries(
            client, "https://x.test/a", max_bytes=100_000, error_body_max_bytes=None
        )

    assert status == 404
    assert len(text) == 5000


async def test_rate_limiter_spaces_requests(monkeypatch):
    slept: list[float] = []
    now = {"t": 0.0}

    async def fake_sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr("mcp_core.transport.http_tier.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("mcp_core.transport.http_tier.time.monotonic", lambda: now["t"])

    limiter = RateLimiter(min_gap_s=1.5)
    await limiter.wait()  # first call goes through immediately
    await limiter.wait()  # second must wait out the gap

    assert slept and slept[-1] == pytest.approx(1.5, abs=0.01)


async def test_rate_limiter_disabled_when_gap_is_zero(monkeypatch):
    async def fail_sleep(_seconds):
        raise AssertionError("a zero gap must not sleep")

    monkeypatch.setattr("mcp_core.transport.http_tier.asyncio.sleep", fail_sleep)
    limiter = RateLimiter(min_gap_s=0)
    await limiter.wait()
    await limiter.wait()


def test_proxy_from_env_prefers_connector_specific_var(monkeypatch):
    monkeypatch.setenv("WB_PROXY", "http://connector:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://generic:8080")
    assert proxy_from_env("WB_PROXY") == "http://connector:8080"


def test_proxy_from_env_falls_back_to_standard_vars(monkeypatch):
    monkeypatch.delenv("WB_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://generic:8080")
    assert proxy_from_env("WB_PROXY") == "http://generic:8080"


def test_proxy_from_env_ignores_blank_values(monkeypatch):
    for name in ("WB_PROXY", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WB_PROXY", "   ")
    assert proxy_from_env("WB_PROXY") is None


def test_build_client_does_not_follow_redirects_by_default():
    """Several RU marketplaces answer datacenter IPs with a self-referential 307."""
    client = build_client(timeout_s=5)
    assert client.follow_redirects is False
