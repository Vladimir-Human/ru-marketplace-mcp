"""Tests for ``get_text_budgeted`` — the wall-clock-bounded HTTP read.

These mirror the WB connector's own transport tests, because this function
absorbed WB's logic and the whole point is that the behaviour is unchanged:
a global deadline that bounds retries, classified error strings instead of
raised exceptions, and the polite gate re-entered between attempts.

All network is an ``httpx.MockTransport``, so the suite is offline and
deterministic.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from mcp_core.transport import RateLimiter, get_text_budgeted


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def test_returns_status_text_and_no_error():
    async def handler(_request):
        return httpx.Response(200, text='{"ok":true}')

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(client, "https://x.test/a", max_bytes=1000, wall_timeout_s=5)

    assert (status, text, err) == (200, '{"ok":true}', None)


async def test_body_cap_returns_error_instead_of_raising():
    """A capped body is a value, not an exception: callers fall through to the next mirror."""

    async def handler(_request):
        return httpx.Response(200, text="x" * 5000)

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(client, "https://x.test/big", max_bytes=100, wall_timeout_s=5)

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
        status, text, err = await get_text_budgeted(
            client, "https://x.test/a", max_bytes=1000, wall_timeout_s=30, retries=2, backoff_s=0
        )

    assert (status, text, err) == (200, "ok", None)
    assert len(attempts) == 3


async def test_transport_error_is_classified_not_raised_when_budget_spent():
    async def handler(request):
        raise httpx.ConnectError("refused", request=request)

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(
            client, "https://x.test/a", max_bytes=1000, wall_timeout_s=30, retries=1, backoff_s=0
        )

    assert (status, text) == (0, None)
    assert err is not None and err.startswith("network:")
    assert "after 2 attempts" in err


async def test_httpx_timeout_is_classified_as_timeout():
    async def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with make_client(handler) as client:
        _, _, err = await get_text_budgeted(client, "https://x.test/a", max_bytes=1000, wall_timeout_s=30, retries=0)

    assert err is not None and err.startswith("timeout:")


async def test_wall_clock_budget_bounds_a_single_slow_attempt():
    """The deadline fires mid-request, so a hung upstream cannot outlast the budget."""

    async def handler(_request):
        await asyncio.sleep(10)
        return httpx.Response(200, text="never arrives")

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(
            client, "https://x.test/slow", max_bytes=1000, wall_timeout_s=0.05, retries=2
        )

    assert (status, text) == (0, None)
    assert err is not None and err.startswith("timeout:")


async def test_exhausted_budget_short_circuits_before_any_request():
    attempts: list[int] = []

    async def handler(_request):
        attempts.append(1)
        return httpx.Response(200, text="should not be reached")

    async with make_client(handler) as client:
        status, _, err = await get_text_budgeted(
            client, "https://x.test/a", max_bytes=1000, wall_timeout_s=0, retries=2
        )

    assert status == 0
    assert err == "timeout: global 0s budget exhausted"
    assert attempts == []


async def test_backoff_that_would_outlast_the_budget_is_not_taken():
    """Better to report the failure now than sleep past a deadline and report it late."""
    attempts: list[int] = []

    async def handler(request):
        attempts.append(1)
        raise httpx.ConnectTimeout("boom", request=request)

    async with make_client(handler) as client:
        status, _, err = await get_text_budgeted(
            client,
            "https://x.test/a",
            max_bytes=1000,
            wall_timeout_s=0.2,
            retries=5,
            backoff_s=10,
        )

    assert status == 0
    assert err == "timeout: global 0.2s budget exhausted"
    assert len(attempts) == 1


async def test_retry_passes_back_through_the_polite_gate():
    """Bursting a marketplace right after it faulted is how IPs get banned."""
    gate_entries: list[int] = []

    class CountingLimiter(RateLimiter):
        async def wait(self) -> None:
            gate_entries.append(1)

    attempts: list[int] = []

    async def handler(request):
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, text="ok")

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(
            client,
            "https://x.test/a",
            max_bytes=1000,
            wall_timeout_s=30,
            retries=2,
            backoff_s=0,
            limiter=CountingLimiter(min_gap_s=0.01),
        )

    assert (status, text, err) == (200, "ok", None)
    assert len(gate_entries) == 1, "the polite gate must be re-entered once, before the retry"


@pytest.mark.parametrize("status_code", [404, 429, 502, 503])
async def test_http_statuses_are_never_retried(status_code):
    """Retrying a 429 deepens a rate limit; no repeat request changes a 4xx.

    Gateway-status retries are ``get_text_with_retries``' job. This function
    hands every status straight back so the caller decides.
    """
    attempts: list[int] = []

    async def handler(_request):
        attempts.append(1)
        return httpx.Response(status_code, text="body")

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(
            client, "https://x.test/a", max_bytes=1000, wall_timeout_s=30, retries=3, backoff_s=0
        )

    assert (status, text, err) == (status_code, "body", None)
    assert len(attempts) == 1


async def test_error_bodies_are_returned_in_full_for_the_caller_to_judge():
    """Detsky Mir answers 404 while rendering a real page, so the body still matters."""

    async def handler(_request):
        return httpx.Response(404, text="p" * 5000)

    async with make_client(handler) as client:
        status, text, err = await get_text_budgeted(client, "https://x.test/a", max_bytes=100_000, wall_timeout_s=5)

    assert status == 404
    assert text is not None and len(text) == 5000
    assert err is None


async def test_headers_are_forwarded():
    seen: dict[str, str] = {}

    async def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text="ok")

    async with make_client(handler) as client:
        await get_text_budgeted(
            client,
            "https://x.test/a",
            max_bytes=1000,
            wall_timeout_s=5,
            headers={"X-Probe": "yes"},
        )

    assert seen.get("x-probe") == "yes"
