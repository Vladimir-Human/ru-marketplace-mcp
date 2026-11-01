"""Offline tests for the negative cache around Chrome CDP autostart.

A failed autostart is remembered for a short window so that ``compare_prices``'
fan-out over the CDP sources makes one bounded attempt instead of paying the
full readiness wait once per source. The launch and the readiness probe are
faked and the module's clock is a controllable monotonic counter: no Chrome, no
socket and no real sleep. These tests fail on the pre-cache code, where every
caller after a failure starts another attempt.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from mcp_core.transport import chrome_cdp


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeTime:
    """Stands in for the ``time`` module inside chrome_cdp only."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return self._clock.now


@pytest.fixture
def autostart(monkeypatch):
    """A controllable autostart: one recorded attempt, a fake clock and ports."""
    clock = _Clock()
    state = {"attempts": 0, "port_open": False, "start_ok": False, "bind_ok": False}
    entered = threading.Event()
    release = threading.Event()
    release.set()

    monkeypatch.setattr(chrome_cdp, "time", _FakeTime(clock))
    monkeypatch.setattr(chrome_cdp, "_cdp_port_open", lambda: state["port_open"])
    monkeypatch.setattr(chrome_cdp, "CDP_IS_LOOPBACK", True)
    monkeypatch.setattr(chrome_cdp, "STEALTH", False)
    monkeypatch.setattr(chrome_cdp, "_autostart_failure", None)
    # The module lock binds to the first event loop that contends for it; each
    # test runs in its own loop, so each needs its own lock.
    monkeypatch.setattr(chrome_cdp, "_AUTOSTART_LOCK", asyncio.Lock())

    def fake_start(*, stealth: bool = False):
        state["attempts"] += 1
        entered.set()
        release.wait(timeout=2.0)
        if state["start_ok"]:
            return True, "started chrome"
        return False, "chrome could not start (fake)"

    async def fake_wait(_timeout_s: float) -> bool:
        return bool(state["bind_ok"])

    monkeypatch.setattr(chrome_cdp, "_start_chrome_with_cdp", fake_start)
    monkeypatch.setattr(chrome_cdp, "_wait_for_cdp_port", fake_wait)

    return {"clock": clock, "state": state, "entered": entered, "release": release}


async def test_concurrent_callers_after_a_failure_make_exactly_one_attempt(autostart):
    results = await asyncio.gather(*(chrome_cdp._ensure_cdp_running() for _ in range(5)))

    assert autostart["state"]["attempts"] == 1
    assert [ok for ok, _msg in results] == [False] * 5
    # Only the caller that made the attempt saw a plain message; the four that
    # waited on the lock get the remembered failure with its age hint.
    hinted = [msg for _ok, msg in results if "remembered autostart failure" in msg]
    assert len(hinted) == 4


async def test_a_caller_already_waiting_on_the_lock_does_not_retry(autostart):
    autostart["release"].clear()
    first = asyncio.create_task(chrome_cdp._ensure_cdp_running())
    assert await asyncio.to_thread(autostart["entered"].wait, 2.0) is True

    second = asyncio.create_task(chrome_cdp._ensure_cdp_running())
    await asyncio.sleep(0.01)  # the second caller is now parked on the autostart lock

    autostart["release"].set()
    first_result = await first
    second_result = await second

    assert autostart["state"]["attempts"] == 1
    assert first_result[0] is False
    assert second_result[0] is False
    assert "remembered autostart failure" in second_result[1]


async def test_a_caller_after_the_window_tries_again(autostart):
    await chrome_cdp._ensure_cdp_running()
    within = await chrome_cdp._ensure_cdp_running()
    assert "remembered autostart failure" in within[1]
    assert autostart["state"]["attempts"] == 1

    autostart["clock"].advance(chrome_cdp._AUTOSTART_FAILURE_TTL_S + 1)
    second = await chrome_cdp._ensure_cdp_running()

    assert second[0] is False
    assert autostart["state"]["attempts"] == 2
    assert "remembered autostart failure" not in second[1]


async def test_the_remembered_failure_keeps_the_message_and_names_the_age(autostart):
    _ok, fresh = await chrome_cdp._ensure_cdp_running()
    autostart["clock"].advance(7)
    ok, remembered = await chrome_cdp._ensure_cdp_running()

    assert ok is False
    assert remembered.startswith(fresh)
    assert "remembered autostart failure 7s ago" in remembered
    assert autostart["state"]["attempts"] == 1


async def test_get_browser_surfaces_the_age_hint_on_a_remembered_failure(autostart):
    await chrome_cdp._ensure_cdp_running()

    with pytest.raises(RuntimeError) as caught:
        async with chrome_cdp.get_browser():
            pytest.fail("a failed autostart must not yield a browser")

    message = str(caught.value)
    assert "CDP autostart failed" in message
    assert "remembered autostart failure 0s ago" in message
    assert autostart["state"]["attempts"] == 1


async def test_success_clears_the_remembered_failure(autostart):
    await chrome_cdp._ensure_cdp_running()
    assert autostart["state"]["attempts"] == 1

    # The failure is remembered ...
    _ok, remembered = await chrome_cdp._ensure_cdp_running()
    assert "remembered autostart failure" in remembered
    assert autostart["state"]["attempts"] == 1

    # ... until Chrome answers, which clears the memory.
    autostart["state"]["port_open"] = True
    ok, _msg = await chrome_cdp._ensure_cdp_running()
    assert ok is True
    assert autostart["state"]["attempts"] == 1  # an open port is not an attempt

    # It dies again inside the old window: a fresh attempt must happen.
    autostart["state"]["port_open"] = False
    autostart["clock"].advance(1)
    ok, msg = await chrome_cdp._ensure_cdp_running()

    assert ok is False
    assert autostart["state"]["attempts"] == 2
    assert "remembered autostart failure" not in msg


async def test_the_explicit_start_still_tries_while_a_failure_is_remembered(autostart):
    await chrome_cdp._ensure_cdp_running()
    _ok, remembered = await chrome_cdp._ensure_cdp_running()
    assert "remembered autostart failure" in remembered
    assert autostart["state"]["attempts"] == 1

    autostart["state"]["start_ok"] = True
    autostart["state"]["bind_ok"] = True
    report = await chrome_cdp.start_browser(visible=True)

    assert report["ok"] is True
    assert report["started"] is True
    assert autostart["state"]["attempts"] == 2

    # The successful explicit start cleared the memory: the next call starts over.
    autostart["state"]["start_ok"] = False
    autostart["state"]["bind_ok"] = False
    ok, msg = await chrome_cdp._ensure_cdp_running()

    assert ok is False
    assert autostart["state"]["attempts"] == 3
    assert "remembered autostart failure" not in msg
