"""Tests for request pacing and refusal backoff.

The clock and the sleep are injected, so these assert real timing decisions
without spending real seconds. Each test names the live failure it guards
against: a burst of selfcheck calls degraded Taobao and DNS in July 2026, and
nothing in the code knew a refusal from a success.
"""

from __future__ import annotations

import pytest
from mcp_core.pacing import Pacer


class FakeClock:
    """A clock that only moves when a sleep says it should."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _pacer(**kwargs) -> tuple[Pacer, FakeClock]:
    clock = FakeClock()
    return Pacer(clock=clock, sleep=clock.sleep, **kwargs), clock


# ------------------------------------------------------------------- spacing ----


async def test_the_first_request_does_not_wait():
    pacer, clock = _pacer(min_gap=3.0)

    await pacer.wait()

    assert clock.slept == []


async def test_a_second_request_waits_out_the_gap():
    pacer, clock = _pacer(min_gap=3.0)
    await pacer.wait()

    await pacer.wait()

    assert clock.slept == [3.0]


async def test_time_already_spent_counts_towards_the_gap():
    """Work between calls is not extra punishment."""
    pacer, clock = _pacer(min_gap=3.0)
    await pacer.wait()
    clock.advance(2.0)

    await pacer.wait()

    assert clock.slept == [1.0]


async def test_a_slow_caller_never_waits():
    pacer, clock = _pacer(min_gap=3.0)
    await pacer.wait()
    clock.advance(10.0)

    await pacer.wait()

    assert clock.slept == []


async def test_a_zero_gap_disables_pacing():
    """Tests and local runs set the gap to zero; it must mean zero."""
    pacer, clock = _pacer(min_gap=0.0)

    await pacer.wait()
    await pacer.wait()

    assert clock.slept == []


async def test_the_gap_can_be_overridden_per_call():
    """Connectors pass their module-level value so it stays monkeypatchable."""
    pacer, clock = _pacer(min_gap=3.0)
    await pacer.wait()

    await pacer.wait(min_gap=0.0)

    assert clock.slept == []


# ------------------------------------------------------------------- backoff ----


async def test_a_refusal_lengthens_the_next_gap():
    """The polite answer to "slow down" is to slow down."""
    pacer, clock = _pacer(min_gap=2.0)
    await pacer.wait()
    pacer.record_refusal()

    await pacer.wait()

    assert clock.slept == [4.0], "default error delay is twice the normal gap"


async def test_the_error_delay_is_configurable():
    pacer, clock = _pacer(min_gap=1.8, error_delay=5.0)
    await pacer.wait()
    pacer.record_refusal()

    await pacer.wait()

    assert clock.slept == [5.0]


async def test_the_penalty_applies_once_not_forever():
    pacer, clock = _pacer(min_gap=2.0)
    await pacer.wait()
    pacer.record_refusal()
    await pacer.wait()

    await pacer.wait()

    assert clock.slept == [4.0, 2.0]


async def test_an_overridden_gap_cannot_undercut_the_penalty():
    """A caller passing a small gap must not defeat the backoff."""
    pacer, clock = _pacer(min_gap=2.0, error_delay=6.0)
    await pacer.wait()
    pacer.record_refusal()

    await pacer.wait(min_gap=0.1)

    assert clock.slept == [6.0]


# ----------------------------------------------------------------- rotation ----


async def test_refusals_accumulate_until_they_look_like_a_wall():
    pacer, _ = _pacer(min_gap=0.0, rotation_threshold=3)

    pacer.record_refusal()
    pacer.record_refusal()
    assert not pacer.should_rotate
    assert pacer.rotation_hint() == ""

    pacer.record_refusal()

    assert pacer.should_rotate
    assert "standing block" in pacer.rotation_hint()


async def test_one_success_clears_the_count():
    """A blip must not accumulate into a false "your IP is burned"."""
    pacer, _ = _pacer(min_gap=0.0, rotation_threshold=3)
    pacer.record_refusal()
    pacer.record_refusal()

    pacer.record_success()

    assert pacer.consecutive_refusals == 0
    assert not pacer.should_rotate


async def test_a_success_also_clears_the_backoff():
    pacer, clock = _pacer(min_gap=2.0)
    await pacer.wait()
    pacer.record_refusal()
    pacer.record_success()

    await pacer.wait()

    assert clock.slept == [2.0]


async def test_the_hint_tells_the_operator_what_to_change():
    pacer, _ = _pacer(min_gap=0.0, rotation_threshold=1)
    pacer.record_refusal()

    hint = pacer.rotation_hint()

    assert "residential" in hint
    assert "session" in hint


# ------------------------------------------------------------------- guards ----


def test_a_negative_gap_is_clamped_rather_than_inverting_time():
    pacer = Pacer(min_gap=-5.0)

    assert pacer.min_gap == 0.0


def test_an_error_delay_below_the_normal_gap_is_raised_to_it():
    """A backoff shorter than the normal pace would be a speed-up."""
    pacer = Pacer(min_gap=3.0, error_delay=1.0)

    assert pacer.error_delay == 3.0


def test_a_zero_rotation_threshold_does_not_fire_on_no_refusals():
    pacer = Pacer(min_gap=0.0, rotation_threshold=0)

    assert pacer.rotation_threshold == 1
    assert not pacer.should_rotate


async def test_reset_forgets_everything():
    pacer, clock = _pacer(min_gap=2.0)
    await pacer.wait()
    pacer.record_refusal()

    pacer.reset()
    await pacer.wait()

    assert clock.slept == []
    assert pacer.consecutive_refusals == 0


async def test_concurrent_callers_do_not_both_skip_the_gap():
    """Two tool calls racing must not both decide the coast is clear."""
    import asyncio

    pacer, clock = _pacer(min_gap=2.0)
    await pacer.wait()

    await asyncio.gather(pacer.wait(), pacer.wait())

    assert len(clock.slept) == 2, "each caller waited its turn"


@pytest.mark.parametrize("bad", [float("nan")])
def test_a_nonsense_gap_does_not_crash_construction(bad):
    """max() with NaN is undefined-ish; just make sure it constructs."""
    pacer = Pacer(min_gap=bad)

    assert pacer.min_gap is not None
