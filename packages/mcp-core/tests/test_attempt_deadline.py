"""A browser attempt's deadline must not count the wait for a navigation permit.

Why this file exists. On 2026-11-18 a full ``compare_prices`` fan-out made
Ozon and Avito answer "CDP timeout after 20.0s" while both sources read fine
alone. Every CDP connector bounds one browser attempt with its own timeout
(20 s by default), and that attempt opens a page — which first queues for a
navigation permit from the shared ``NavigationBudget`` (one slot per host,
three navigations at once). The sources that waited longest for a free Chrome
slot spent their whole timeout *in the queue* and never reached the browser.

The timeout a connector means is "how long the work may take", not "how long
the work plus the queue may take". These tests pin that split at the deadline
itself: the queue wait is lent back to the attempt, at most one timeout more,
so the attempt never runs longer than twice its timeout and a timeout still
raises ``TimeoutError`` exactly as ``asyncio.wait_for`` does.

The public-tool side of the same requirement lives in the Avito connector's own
test (``packages/avito-connector/tests/test_cdp_queue_credit.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from mcp_core.transport.attempt_deadline import bounded_attempt
from mcp_core.transport.cdp_budget import HostRefusingError, NavigationBudget

HOST = "shop.example"
# Small enough to keep the suite quick, large enough that a loaded machine
# still measures the queue and the work in the right order.
TIMEOUT_S = 0.5


async def _hold_permit(budget: NavigationBudget, seconds: float, holding: asyncio.Event) -> None:
    """Keep the only navigation permit busy, so the next attempt has to queue."""
    async with budget.slot(HOST) as slot:
        slot.ok()
        holding.set()
        await asyncio.sleep(seconds)


async def _navigate(budget: NavigationBudget) -> str:
    """One navigation: the permit is what an attempt waits for."""
    async with budget.slot(HOST) as slot:
        slot.ok()
        return "navigated"


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_an_attempt_that_queued_longer_than_its_timeout_still_completes():
    """The queue wait must not eat the attempt's own timeout.

    The permit below is held for 1.5x the timeout: longer than the attempt's
    budget, shorter than twice it. The attempt must still finish, because the
    seconds it spent queueing are added back to its deadline.
    """
    budget = NavigationBudget(max_concurrent=1, per_host=1)
    holding = asyncio.Event()
    holder = asyncio.create_task(_hold_permit(budget, 1.5 * TIMEOUT_S, holding))
    await holding.wait()

    try:
        result = await bounded_attempt(_navigate(budget), timeout_s=TIMEOUT_S)
    finally:
        await holder

    assert result == "navigated"


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_the_queue_credit_never_exceeds_the_timeout():
    """Three timeouts of queue wait is more than the credit: the attempt fails.

    The credit is at most one timeout, so a queue that outlasts it must not
    turn into an attempt that runs for minutes.
    """
    budget = NavigationBudget(max_concurrent=1, per_host=1)
    holding = asyncio.Event()
    holder = asyncio.create_task(_hold_permit(budget, 3 * TIMEOUT_S, holding))
    await holding.wait()

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            await bounded_attempt(_navigate(budget), timeout_s=TIMEOUT_S)
        elapsed = time.monotonic() - started
    finally:
        holder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await holder

    # The deadline fires while the attempt is still queued (at twice the
    # timeout), not after the holder finally lets go.
    assert elapsed < 3 * TIMEOUT_S


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_work_time_over_the_timeout_still_fails():
    """Only the queue is lent back: an attempt that works too long still fails."""

    async def slow_attempt() -> str:
        await asyncio.sleep(2 * TIMEOUT_S)
        return "never"  # pragma: no cover - the attempt is cancelled first

    with pytest.raises(TimeoutError):
        await bounded_attempt(slow_attempt(), timeout_s=TIMEOUT_S)


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_an_attempt_never_runs_longer_than_twice_its_timeout():
    """The credit is one timeout, so the whole attempt is bounded by two."""
    budget = NavigationBudget(max_concurrent=1, per_host=1)
    holding = asyncio.Event()
    holder = asyncio.create_task(_hold_permit(budget, 1.5 * TIMEOUT_S, holding))
    await holding.wait()

    async def navigate_then_work() -> str:
        await _navigate(budget)
        await asyncio.sleep(10 * TIMEOUT_S)
        return "never"  # pragma: no cover - the attempt is cancelled first

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            await bounded_attempt(navigate_then_work(), timeout_s=TIMEOUT_S)
        elapsed = time.monotonic() - started
    finally:
        holder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await holder

    assert elapsed < 2 * TIMEOUT_S + 0.5


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_without_the_helper_a_queued_attempt_still_times_out():
    """A direct tool call has no credit: the old behaviour must stay untouched."""
    budget = NavigationBudget(max_concurrent=1, per_host=1)
    holding = asyncio.Event()
    holder = asyncio.create_task(_hold_permit(budget, 1.5 * TIMEOUT_S, holding))
    await holding.wait()

    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_navigate(budget), timeout=TIMEOUT_S)
    finally:
        holder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await holder


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_an_open_breaker_still_refuses_a_queued_attempt():
    """The credit changes the deadline, never the refusal: an open breaker
    still fails the attempt fast, without waiting for a permit at all."""
    budget = NavigationBudget(max_concurrent=1, per_host=1, breaker_threshold=1, breaker_cooldown_s=30.0)

    async with budget.slot(HOST) as slot:
        slot.refused(403)

    started = time.monotonic()
    with pytest.raises(HostRefusingError):
        await bounded_attempt(_navigate(budget), timeout_s=TIMEOUT_S)

    assert time.monotonic() - started < TIMEOUT_S


@pytest.mark.req("R-CDP-QUEUE-CREDIT")
async def test_a_credited_attempt_gives_every_permit_back():
    """A queued attempt that timed out must not leak the permits it took."""
    budget = NavigationBudget(max_concurrent=1, per_host=1)
    holding = asyncio.Event()
    holder = asyncio.create_task(_hold_permit(budget, 3 * TIMEOUT_S, holding))
    await holding.wait()

    with pytest.raises(TimeoutError):
        await bounded_attempt(_navigate(budget), timeout_s=TIMEOUT_S)

    holder.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await holder

    assert budget.snapshot()["in_flight"] == 0
    # The host slot and the global slot are free again: with max_concurrent=1
    # a leaked global permit would hang here forever.
    async with asyncio.timeout(0.5):
        permit = await budget.acquire(HOST)
        permit.ok()
        permit.release()
