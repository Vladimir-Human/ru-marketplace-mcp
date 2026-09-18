"""CDP navigation budget: bounded concurrency, per-host serialization, refusals.

Why this exists. Tab ownership (see ``chrome_cdp``) made a contended fan-out
*honest* — each source reports its own failure instead of one connector claiming
another's tab — but honest is not the same as working. Verified live 2026-09-11:
``compare_prices`` over six CDP sources crashed every navigation at once
(``ERR_ABORTED`` / ``TargetClosedError``) because six tabs were driven through
one Chrome simultaneously, while the same sources read fine one at a time. The
tab-ownership fixes did not remove that contention; they only stopped it from
being misattributed. This module removes it:

  * a **global** bound on how many CDP navigations may be in flight at once
    (default 3 — enough for a real fan-out, few enough that Chrome is not
    asked to drive more tabs than it can settle);
  * a **per-host** bound (default 1), because two navigations to the same
    marketplace compete for that site's own patience long before they compete
    for Chrome;
  * a **per-host breaker** on refusals, so a host that has started answering
    4xx is dropped for a cooldown instead of being hammered by the rest of the
    fan-out. The threshold follows the external WB observation that a burst of
    ten 4xx responses is a wall rather than bad luck.

Everything is injectable (clock, sleep, limits) so the tests exercise real
concurrency without real waiting. Nothing here knows about Playwright or CDP:
it is a budget, and ``open_page`` is the only caller that spends it.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

__all__ = [
    "HostRefusingError",
    "NavigationBudget",
    "Slot",
    "budget_snapshot",
    "navigation_budget",
]


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


class HostRefusingError(RuntimeError):
    """A host answered 4xx often enough that the breaker is open.

    Carries the host and the remaining cooldown so a connector can report
    "this source is refusing us, it was skipped on purpose" instead of a
    generic transport failure.
    """

    def __init__(self, host: str, retry_after_s: float, refusals: int) -> None:
        self.host = host
        self.retry_after_s = max(0.0, float(retry_after_s))
        self.refusals = int(refusals)
        super().__init__(f"CDP navigation to {host} is paused: {refusals} refusals in a row")


@dataclass
class _HostState:
    refusals: int = 0
    open_until: float = 0.0
    opens: int = 0


@dataclass
class Slot:
    """One navigation permit. Report the outcome so the breaker can learn."""

    host: str
    _budget: NavigationBudget = field(repr=False)
    _settled: bool = field(default=False, repr=False)
    _released: bool = field(default=False, repr=False)

    def ok(self) -> None:
        """The navigation produced a usable page: forget this host's refusals."""
        if self._settled:
            return
        self._settled = True
        self._budget._note_success(self.host)

    def refused(self, status: int | None = None) -> None:
        """The navigation was refused (4xx/5xx/block/auth or a challenge wall)."""
        if self._settled:
            return
        self._settled = True
        self._budget._note_refusal(self.host, status)

    def neutral(self) -> None:
        """The navigation ended for a reason that is neither success nor refusal.

        Our own host-policy rejection is not the host refusing us — but it is not
        proof the host is healthy either. Settling it as ``ok()`` used to wipe the
        host's accumulated refusals and close a legitimately open breaker, i.e. an
        event we caused could reset the host's record. (Independent review, 2026-09-18.)
        """
        if self._settled:
            return
        self._settled = True

    def release(self) -> None:
        """Give the slot back. Idempotent, and separate from the outcome.

        The permit covers the act of navigating, not the lifetime of the page: a
        retained challenge page lives for minutes and must not hold its host's
        slot while a human clears it.
        """
        if self._released:
            return
        self._released = True
        self._budget._release(self.host)


class NavigationBudget:
    """Global + per-host bounds for CDP navigations, with a refusal breaker.

    One instance per process (``navigation_budget()``); connectors never build
    their own, so a fan-out across sources shares one budget.
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 3,
        per_host: int = 1,
        breaker_threshold: int = 10,
        breaker_cooldown_s: float = 60.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.per_host = max(1, int(per_host))
        self.breaker_threshold = max(1, int(breaker_threshold))
        self.breaker_cooldown_s = max(0.0, float(breaker_cooldown_s))
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._global = asyncio.Semaphore(self.max_concurrent)
        self._hosts: dict[str, _HostState] = {}
        self._host_locks: dict[str, asyncio.Semaphore] = {}
        self._peak_in_flight = 0
        self._in_flight = 0
        self._waited = 0

    # ---------------------------------------------------------------- limits

    def _host_semaphore(self, host: str) -> asyncio.Semaphore:
        sem = self._host_locks.get(host)
        if sem is None:
            sem = asyncio.Semaphore(self.per_host)
            self._host_locks[host] = sem
        return sem

    def _state(self, host: str) -> _HostState:
        state = self._hosts.get(host)
        if state is None:
            state = _HostState()
            self._hosts[host] = state
        return state

    def check(self, host: str) -> None:
        """Raise when this host's breaker is open and still cooling down."""
        state = self._hosts.get(host)
        if state is None or state.open_until <= 0.0:
            return
        now = self._clock()
        if now < state.open_until:
            raise HostRefusingError(host, state.open_until - now, state.refusals)
        # Cooldown elapsed: give the host a fresh chance, keep the open count.
        state.open_until = 0.0
        state.refusals = 0

    async def acquire(self, host: str) -> Slot:
        """Take one navigation permit for ``host``.

        Order matters: the breaker is checked before waiting, so an open host
        fails fast instead of queueing behind a wall.
        """
        key = (host or "").lower().rstrip(".")
        self.check(key)
        if self._global.locked():
            self._waited += 1
        await self._global.acquire()
        try:
            await self._host_semaphore(key).acquire()
        except BaseException:  # pragma: no cover - only on cancellation
            self._global.release()
            raise
        self._in_flight += 1
        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        return Slot(host=key, _budget=self)

    def _release(self, host: str) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self._host_semaphore(host).release()
        self._global.release()

    @asynccontextmanager
    async def slot(self, host: str) -> AsyncIterator[Slot]:
        """Permit held for the whole ``async with`` block.

        Convenience for callers whose work *is* the navigation. ``open_page``
        uses acquire/release instead, because the page it yields can outlive the
        navigation by minutes — holding a host's slot for that long would queue
        every other navigation to that marketplace behind a page waiting for a
        human.
        """
        slot = await self.acquire(host)
        try:
            yield slot
        finally:
            slot.release()

    # --------------------------------------------------------------- signals

    def _note_success(self, host: str) -> None:
        state = self._state(host)
        state.refusals = 0
        state.open_until = 0.0

    def _note_refusal(self, host: str, status: int | None = None) -> None:
        state = self._state(host)
        state.refusals += 1
        if state.refusals >= self.breaker_threshold:
            state.open_until = self._clock() + self.breaker_cooldown_s
            state.opens += 1

    # ------------------------------------------------------------ diagnostics

    def snapshot(self) -> dict[str, object]:
        """Read-only view of the budget — no tab is opened to answer this."""
        now = self._clock()
        return {
            "max_concurrent": self.max_concurrent,
            "per_host": self.per_host,
            "in_flight": self._in_flight,
            "peak_in_flight": self._peak_in_flight,
            "queued_waits": self._waited,
            "breaker_threshold": self.breaker_threshold,
            "breaker_cooldown_s": self.breaker_cooldown_s,
            "hosts": {
                host: {
                    "refusals": state.refusals,
                    "open": state.open_until > now,
                    "retry_after_s": round(max(0.0, state.open_until - now), 3),
                    "opens": state.opens,
                }
                for host, state in sorted(self._hosts.items())
            },
        }

    def reset(self) -> None:
        """Forget every host's history. For tests and a deliberate session change."""
        self._hosts.clear()
        self._host_locks.clear()
        self._peak_in_flight = 0
        self._waited = 0


_BUILT: NavigationBudget | None = None


def navigation_budget() -> NavigationBudget:
    """The process-wide budget, built from the environment on first use."""
    global _BUILT
    if _BUILT is None:
        _BUILT = NavigationBudget(
            max_concurrent=_env_int("CHROME_CDP_MAX_NAVIGATIONS", 3),
            per_host=_env_int("CHROME_CDP_PER_HOST_NAVIGATIONS", 1),
            breaker_threshold=_env_int("CHROME_CDP_BREAKER_4XX", 10),
            breaker_cooldown_s=_env_float("CHROME_CDP_BREAKER_COOLDOWN_S", 60.0),
        )
    return _BUILT


def budget_snapshot() -> dict[str, object]:
    """Diagnostics for the process-wide budget (used by selfcheck-style tools)."""
    return navigation_budget().snapshot()
