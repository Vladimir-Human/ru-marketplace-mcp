"""Offline tests for the CDP navigation budget.

The live failure this guards against (2026-09-11) was a fan-out over six CDP
sources crashing every navigation at once. Reproducing that in CI would mean
driving a real Chrome, so the budget's own contract is pinned here instead: how
many navigations may overlap, in what order a queued request takes its slots,
what a host's refusals do, and that diagnostics can be read without opening
anything.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp_core.transport import cdp_budget
from mcp_core.transport.cdp_budget import HostRefusingError, NavigationBudget


class Clock:
    """Controllable monotonic clock, so cooldowns pass in microseconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Tracker:
    """Counts overlapping holders and remembers the peak."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.order: list[str] = []

    async def hold(self, host: str) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.order.append(f"+{host}")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.order.append(f"-{host}")
        self.active -= 1


@pytest.fixture
def clock() -> Clock:
    return Clock()


async def test_fan_out_never_exceeds_the_global_bound(clock):
    """Six sources at once is exactly the shape that used to crash Chrome."""
    budget = NavigationBudget(max_concurrent=3, per_host=1, clock=clock)
    tracker = Tracker()

    async def nav(host: str) -> None:
        async with budget.slot(host) as slot:
            await tracker.hold(host)
            slot.ok()

    await asyncio.gather(*(nav(f"src{i}.example") for i in range(6)))

    assert tracker.peak <= 3
    assert tracker.active == 0
    assert budget.snapshot()["peak_in_flight"] <= 3


async def test_the_same_host_is_serialized_even_when_the_budget_is_free(clock):
    """Two navigations to one marketplace compete for that site's patience."""
    budget = NavigationBudget(max_concurrent=4, per_host=1, clock=clock)
    tracker = Tracker()

    async def nav() -> None:
        async with budget.slot("market.example") as slot:
            await tracker.hold("market.example")
            slot.ok()

    await asyncio.gather(nav(), nav(), nav())

    assert tracker.peak == 1
    assert tracker.order == ["+market.example", "-market.example"] * 3


async def test_different_hosts_use_the_whole_global_bound(clock):
    """The bound must not degrade a fan-out of distinct sources to serial."""
    budget = NavigationBudget(max_concurrent=3, per_host=1, clock=clock)
    tracker = Tracker()

    async def nav(host: str) -> None:
        async with budget.slot(host) as slot:
            await tracker.hold(host)
            slot.ok()

    await asyncio.gather(*(nav(h) for h in ("a.example", "b.example", "c.example")))

    assert tracker.peak == 3


async def test_a_request_waiting_for_its_host_does_not_hold_the_global_slot():
    """A busy host must not spend global capacity while its request queues.

    The defect this guards: with the global slot taken before the per-host one,
    the second request below parks on the busy host while *holding* one of the two
    global permits, so the request for a free host has nowhere to run. That free
    host is what the rest of the fan-out waits for; it must not queue behind a
    marketplace we are already waiting on.
    """
    budget = NavigationBudget(max_concurrent=2, per_host=1)

    busy_held = asyncio.Event()
    release_busy = asyncio.Event()

    async def hold_busy():
        async with budget.slot("busy.example") as slot:
            busy_held.set()
            await release_busy.wait()
            slot.ok()

    started = asyncio.Event()

    async def queue_for_busy():
        started.set()
        async with budget.slot("busy.example") as slot:
            slot.ok()

    async def use_free():
        async with budget.slot("free.example") as slot:
            slot.ok()

    holder = asyncio.create_task(hold_busy())
    await busy_held.wait()

    queued = asyncio.create_task(queue_for_busy())
    await started.wait()  # parked on the busy host's slot, and nowhere else

    free = asyncio.create_task(use_free())
    try:
        await asyncio.wait_for(free, timeout=0.2)
    finally:
        release_busy.set()
        await asyncio.gather(holder, queued)


async def test_a_host_that_keeps_refusing_is_dropped_and_says_for_how_long(clock):
    budget = NavigationBudget(max_concurrent=2, breaker_threshold=3, breaker_cooldown_s=60.0, clock=clock)

    for _ in range(3):
        async with budget.slot("wb.example") as slot:
            slot.refused(429)

    with pytest.raises(HostRefusingError) as excinfo:
        async with budget.slot("wb.example"):
            pass  # pragma: no cover - the slot never opens

    assert excinfo.value.host == "wb.example"
    assert excinfo.value.refusals == 3
    assert excinfo.value.retry_after_s == pytest.approx(60.0)
    assert "3 refusals" in str(excinfo.value)


async def test_a_refusing_host_fails_fast_instead_of_queueing(clock):
    """An open breaker must not make the rest of the fan-out wait on it."""
    budget = NavigationBudget(max_concurrent=1, breaker_threshold=1, breaker_cooldown_s=30.0, clock=clock)

    async with budget.slot("wall.example") as slot:
        slot.refused(403)

    # The single global slot is held while we try the refusing host. A check that
    # runs *after* the acquire would wait for that slot and the clock would move;
    # only a check before the wait can raise with no time spent. Without the holder
    # this test passed either way.
    async with budget.slot("busy.example"):
        started = clock()
        with pytest.raises(HostRefusingError):
            async with budget.slot("wall.example"):
                pass  # pragma: no cover
        assert clock() == started, "no time may be spent waiting on an open breaker"


async def test_a_request_queued_before_the_breaker_opened_is_refused_too(clock):
    """The turn that comes after the breaker opens is not a permission.

    The queued request below reaches ``acquire`` while the host is healthy and
    parks behind the holder. The holder then refuses — the breaker opens — and
    only after that releases. A request that gets its slot at that point must
    hear the same refusal a fresh one hears, not be handed a navigation.
    """
    budget = NavigationBudget(
        max_concurrent=1,
        per_host=1,
        breaker_threshold=1,
        breaker_cooldown_s=60.0,
        clock=clock,
    )

    holding = asyncio.Event()
    refusal = asyncio.Event()

    async def holder():
        async with budget.slot("wall.example") as slot:
            holding.set()
            await refusal.wait()
            slot.refused(403)

    started = asyncio.Event()

    async def queued():
        started.set()
        return await budget.acquire("wall.example")

    holder_task = asyncio.create_task(holder())
    await holding.wait()

    queued_task = asyncio.create_task(queued())
    await started.wait()  # parked on the host's slot while the breaker is still closed
    refusal.set()
    await holder_task

    with pytest.raises(HostRefusingError):
        await queued_task

    assert budget.snapshot()["in_flight"] == 0
    # Both permits must be back: a leaked global one (max_concurrent is 1) or a
    # leaked host one (the cooldown below reopens the host) would hang here.
    async with asyncio.timeout(0.2):
        async with budget.slot("free.example") as slot:
            slot.ok()

    clock.advance(61.0)
    async with asyncio.timeout(0.2):
        permit = await budget.acquire("wall.example")
        permit.ok()
        permit.release()


async def test_cooldown_gives_the_host_another_chance(clock):
    budget = NavigationBudget(max_concurrent=2, breaker_threshold=2, breaker_cooldown_s=15.0, clock=clock)

    for _ in range(2):
        async with budget.slot("flaky.example") as slot:
            slot.refused(503)

    # Before the cooldown ends the host must still be refused: without this the
    # test could not tell "the cooldown reopened it" from "a success cleared it".
    clock.advance(14.0)
    with pytest.raises(HostRefusingError):
        async with budget.slot("flaky.example"):
            pass  # pragma: no cover

    clock.advance(1.0)
    async with budget.slot("flaky.example") as slot:
        slot.ok()

    snap = budget.snapshot()
    assert snap["hosts"]["flaky.example"]["open"] is False
    assert snap["hosts"]["flaky.example"]["refusals"] == 0
    assert snap["hosts"]["flaky.example"]["opens"] == 1


async def test_one_success_clears_the_refusal_history(clock):
    """Bad luck must not accumulate into a wall across a healthy minute."""
    budget = NavigationBudget(max_concurrent=2, breaker_threshold=3, clock=clock)

    for _ in range(2):
        async with budget.slot("mixed.example") as slot:
            slot.refused(429)
    async with budget.slot("mixed.example") as slot:
        slot.ok()
    for _ in range(2):
        async with budget.slot("mixed.example") as slot:
            slot.refused(429)

    # Still below the threshold, because the success reset the counter.
    assert budget.snapshot()["hosts"]["mixed.example"]["refusals"] == 2
    async with budget.slot("mixed.example") as slot:
        slot.ok()


async def test_outcome_is_reported_once(clock):
    """A settled slot cannot be re-labelled by later code paths."""
    budget = NavigationBudget(max_concurrent=1, breaker_threshold=1, breaker_cooldown_s=5.0, clock=clock)

    async with budget.slot("once.example") as slot:
        slot.ok()
        slot.refused(500)

    assert budget.snapshot()["hosts"]["once.example"]["refusals"] == 0

    async with budget.slot("other.example") as slot:
        slot.refused(500)
        slot.ok()

    assert budget.snapshot()["hosts"]["other.example"]["refusals"] == 1


async def test_diagnostics_need_no_navigation(clock):
    """Read-only view: R2 asks for lease diagnostics without opening tabs."""
    budget = NavigationBudget(max_concurrent=2, per_host=1, clock=clock)
    async with budget.slot("seen.example") as slot:
        slot.refused(404)
        snap = budget.snapshot()
        assert snap["in_flight"] == 1

    snap = budget.snapshot()
    assert snap["max_concurrent"] == 2
    assert snap["per_host"] == 1
    assert snap["in_flight"] == 0
    assert snap["hosts"]["seen.example"]["refusals"] == 1
    assert snap["hosts"]["seen.example"]["open"] is False


async def test_host_keys_are_case_and_dot_insensitive(clock):
    """One marketplace is one key, however the URL spells the host."""
    budget = NavigationBudget(max_concurrent=2, breaker_threshold=1, clock=clock)

    async with budget.slot("Market.Example.") as slot:
        slot.refused(403)

    with pytest.raises(HostRefusingError):
        async with budget.slot("market.example"):
            pass  # pragma: no cover


async def test_a_long_lived_page_does_not_hold_the_host_slot(clock):
    """The permit covers the navigation, not the page's lifetime.

    Found by a hang: ``open_page`` yields a page that a caller may keep for
    minutes (a retained challenge page waits for a human), and with one slot per
    host the next navigation to the same marketplace queued behind it forever —
    in tests, that was the whole suite hanging. Release is therefore separate
    from the outcome.
    """
    budget = NavigationBudget(max_concurrent=2, per_host=1, clock=clock)

    holder = await budget.acquire("retained.example")
    holder.ok()  # navigation succeeded...
    holder.release()  # ...and the slot is already free while the page stays open

    async with asyncio.timeout(1):
        async with budget.slot("retained.example") as second:
            second.ok()


async def test_release_is_idempotent_and_never_double_counts(clock):
    budget = NavigationBudget(max_concurrent=1, per_host=1, clock=clock)

    permit = await budget.acquire("twice.example")
    permit.ok()
    permit.release()
    permit.release()

    assert budget.snapshot()["in_flight"] == 0
    assert budget.snapshot()["peak_in_flight"] == 1

    # A double release must not hand out a second slot: take one more permit and
    # count the in-flight navigations. A counter decremented twice reads 0 here.
    again = await budget.acquire("twice.example")
    assert budget.snapshot()["in_flight"] == 1, "the bound was inflated by the second release"
    again.ok()
    again.release()


async def test_cancelling_a_queued_request_gives_back_the_host_slot_it_holds():
    """A cancelled waiter must not take its host's slot to the grave.

    The request below already holds its host's per-host slot and is waiting for
    the global one when it is cancelled. Both halves must be undone: the pending
    wait must end, and the host's slot must be released, or the next navigation to
    that marketplace would queue behind a request that no longer exists.
    """
    budget = NavigationBudget(max_concurrent=1, per_host=1)

    holding = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with budget.slot("busy.example") as slot:
            holding.set()
            await release.wait()
            slot.ok()

    started = asyncio.Event()

    async def queued():
        started.set()
        async with budget.slot("queued.example") as slot:
            slot.ok()  # pragma: no cover - the block is never entered

    holder_task = asyncio.create_task(holder())
    await holding.wait()

    queued_task = asyncio.create_task(queued())
    await started.wait()  # holds queued.example's slot; waits for the global one
    queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued_task

    release.set()
    await holder_task

    # A leaked host slot would leave this waiting forever.
    async with asyncio.timeout(0.2):
        async with budget.slot("queued.example") as slot:
            slot.ok()
    assert budget.snapshot()["in_flight"] == 0


def test_environment_knobs_build_the_process_budget(monkeypatch):
    monkeypatch.setenv("CHROME_CDP_MAX_NAVIGATIONS", "5")
    monkeypatch.setenv("CHROME_CDP_PER_HOST_NAVIGATIONS", "2")
    monkeypatch.setenv("CHROME_CDP_BREAKER_4XX", "7")
    monkeypatch.setenv("CHROME_CDP_BREAKER_COOLDOWN_S", "12.5")
    monkeypatch.setattr(cdp_budget, "_BUILT", None)

    built = cdp_budget.navigation_budget()

    assert built.max_concurrent == 5
    assert built.per_host == 2
    assert built.breaker_threshold == 7
    assert built.breaker_cooldown_s == pytest.approx(12.5)
    assert cdp_budget.navigation_budget() is built


def test_bad_environment_values_fall_back_to_defaults(monkeypatch):
    """A typo in an operator's env must not disable the guard."""
    monkeypatch.setenv("CHROME_CDP_MAX_NAVIGATIONS", "zero")
    monkeypatch.setenv("CHROME_CDP_PER_HOST_NAVIGATIONS", "0")
    monkeypatch.setenv("CHROME_CDP_BREAKER_4XX", "-3")
    monkeypatch.setenv("CHROME_CDP_BREAKER_COOLDOWN_S", "soon")
    monkeypatch.setattr(cdp_budget, "_BUILT", None)

    built = cdp_budget.navigation_budget()

    assert built.max_concurrent == 3
    assert built.per_host == 1
    assert built.breaker_threshold == 10
    assert built.breaker_cooldown_s == pytest.approx(60.0)


def test_budget_snapshot_helper_reads_the_process_budget(monkeypatch):
    monkeypatch.setattr(cdp_budget, "_BUILT", None)
    snap = cdp_budget.budget_snapshot()

    assert snap["hosts"] == {}
    assert snap["in_flight"] == 0
