"""Request pacing and refusal backoff, shared by every connector.

Each connector used to carry its own three-line `_polite_wait`: a lock, a
monotonic timestamp, a sleep to keep requests `min_gap` apart. Eight copies of
the same idea, and none of them knew the difference between a request that
worked and one that got refused.

That gap is not academic. Verified live in July 2026: Taobao and DNS both went
healthy and then degraded to a parse failure and a transport refusal after a
burst of back-to-back selfcheck calls. Nothing was wrong with the parsers — the
marketplaces simply stopped answering a client that hammered them. Every open
implementation of these sources converges on the same three habits, so they
live here once:

  * a floor on the gap between requests;
  * a longer gap after a refusal, because the polite response to "slow down"
    is to slow down;
  * a count of consecutive refusals, so a caller can tell a blip from a wall
    and say so instead of retrying into a ban.

Pure and dependency-free: the clock and the sleep are injectable, so the tests
run in microseconds rather than really waiting.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

__all__ = ["Pacer"]


class Pacer:
    """Keeps one source's requests spaced out, and backs off when refused.

    One instance per marketplace, not per request: the point is to remember
    when this source was last touched. Safe to share across concurrent tool
    calls — the gap is enforced under a lock, so two callers cannot both decide
    that enough time has passed.
    """

    def __init__(
        self,
        min_gap: float,
        *,
        error_delay: float | None = None,
        rotation_threshold: int = 5,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Configure the pace for one source.

        ``error_delay`` defaults to twice ``min_gap``. The public parsers that
        survive on these marketplaces use roughly that ratio — mmparser waits
        1.8 s normally and 5 s after an error — and doubling keeps one knob
        instead of two for callers who do not care.

        ``rotation_threshold`` is the number of consecutive refusals after
        which ``should_rotate`` turns true. It changes no behaviour on its own;
        it exists so a connector can tell the operator "this is not a blip,
        change the IP or refresh the session" rather than silently retrying.
        """
        self.min_gap = max(0.0, float(min_gap))
        self.error_delay = max(self.min_gap, float(error_delay)) if error_delay is not None else self.min_gap * 2
        self.rotation_threshold = max(1, int(rotation_threshold))
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._consecutive_refusals = 0
        self._penalised = False

    async def wait(self, min_gap: float | None = None) -> None:
        """Block until enough time has passed since the previous request.

        ``min_gap`` overrides the configured gap for this call. Connectors pass
        their module-level value so an operator (or a test) can retune the pace
        at runtime without rebuilding the pacer.
        """
        gap = self.min_gap if min_gap is None else max(0.0, float(min_gap))
        if self._penalised:
            gap = max(gap, self.error_delay)
        async with self._lock:
            elapsed = self._clock() - self._last_request_at
            if elapsed < gap:
                await self._sleep(gap - elapsed)
            self._last_request_at = self._clock()
            self._penalised = False

    def record_success(self) -> None:
        """A request came back with data. Forget the refusals before it."""
        self._consecutive_refusals = 0
        self._penalised = False

    def record_refusal(self) -> None:
        """A request was refused — a 401, 403, 429, or an anti-bot wall.

        Only count what the marketplace actively refused. A parse failure or a
        timeout is our problem or the network's, and slowing down does not fix
        either; miscounting them would make the pacer crawl for no reason.
        """
        self._consecutive_refusals += 1
        self._penalised = True

    @property
    def consecutive_refusals(self) -> int:
        return self._consecutive_refusals

    @property
    def should_rotate(self) -> bool:
        """Whether refusals have stopped looking like bad luck."""
        return self._consecutive_refusals >= self.rotation_threshold

    def rotation_hint(self) -> str:
        """A sentence for the operator, empty until rotation is warranted.

        Connectors append this to the error they already raise. It is the
        difference between "blocked, try again" and "blocked five times in a
        row — this address or session is burned".
        """
        if not self.should_rotate:
            return ""
        return (
            f"{self._consecutive_refusals} refusals in a row — this is a standing block, not a blip. "
            "Change the IP (a Russian residential or mobile address works where a datacenter one does not), "
            "or refresh the logged-in session in the scraping profile."
        )

    def reset(self) -> None:
        """Forget everything. For tests and for a deliberate session change."""
        self._last_request_at = 0.0
        self._consecutive_refusals = 0
        self._penalised = False
