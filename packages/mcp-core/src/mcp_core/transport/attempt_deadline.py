"""A browser attempt's deadline that does not count the wait for a Chrome slot.

Why this exists. Every CDP connector bounds one browser attempt with its own
timeout (20 s by default): ``asyncio.wait_for(_attempt(), timeout=TIMEOUT)``.
``_attempt`` opens a page, and ``open_page`` first takes a navigation permit
from the shared ``NavigationBudget`` (one slot per host, three navigations at
once). In a full ``compare_prices`` fan-out about ten sources ask for a slot
at the same time, and the sources that waited long enough for their own
timeout to expire *in the queue* answered "CDP timeout after 20.0s" even
though the browser never refused them — Ozon and Avito did exactly that on
2026-11-18, while both read fine alone.

The timeout a connector means is "how long the work may take", not "how long
the work plus the queue may take". This module keeps the two apart. It runs
the attempt under one deadline and lends that deadline the seconds the
attempt spent waiting for a navigation permit — at most one timeout more, so
an attempt never runs longer than twice its timeout. The seconds come back
only if the attempt really waited: a timeout still raises ``TimeoutError``,
exactly as ``asyncio.wait_for`` does, and every caller keeps its own message.

Nothing here knows about Chrome, Playwright or the budget: it is a deadline
with a credit, and ``NavigationBudget.acquire`` is the only spender.
"""
# START_MODULE_CONTRACT
# PURPOSE: Run a browser attempt under one deadline and lend the wait for a navigation permit back to it.
# SCOPE: The attempt deadline and its queue credit; no browser, no budget, no connector state.
# DEPENDS: stdlib asyncio,collections.abc,contextvars,dataclasses,typing.
# LINKS: M-ATTEMPT-DEADLINE; M-CDP-BUDGET; R-CDP-QUEUE-CREDIT.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# AttemptCredit: Carry the attempt's deadline and the credit it may still lend out.
# current_attempt_credit: Read the credit of the attempt running in this task, if any.
# bounded_attempt: Run one browser attempt under a timeout that the queue may extend.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextvars import ContextVar, Token
from dataclasses import dataclass

__all__ = ["AttemptCredit", "bounded_attempt", "current_attempt_credit"]


@dataclass
class AttemptCredit:
    """The deadline of one attempt and the credit it may still lend out.

    ``left_s`` is the number of seconds the attempt may still add to its own
    deadline; it starts as the attempt's timeout and only ever shrinks.
    """

    timeout: asyncio.Timeout
    left_s: float

    def move_deadline(self, when: float) -> None:
        """Move the attempt's deadline, unless that deadline already fired.

        A timeout that has fired is on its way to becoming a ``TimeoutError``;
        there is no deadline left to move, and the attempt is already leaving.
        """
        try:
            self.timeout.reschedule(when)
        except RuntimeError:
            return


_CREDIT: ContextVar[AttemptCredit | None] = ContextVar("mcp_core_attempt_credit", default=None)


def current_attempt_credit() -> AttemptCredit | None:
    """The credit of the attempt running in this task, or None outside one.

    A direct tool call and a test that drives the budget by hand have no
    attempt around them, and nothing changes for them.
    """
    return _CREDIT.get()


async def bounded_attempt[T](attempt: Awaitable[T], timeout_s: float) -> T:
    """Run ``attempt`` under ``timeout_s``; a navigation wait is lent back to it.

    While the attempt waits for a navigation permit the budget may push the
    deadline out by the seconds it waited, at most ``timeout_s`` more in
    total. A timeout still raises ``TimeoutError``, exactly as
    ``asyncio.wait_for`` does, so every caller's own message stays true.
    """
    async with asyncio.timeout(timeout_s) as deadline:
        credit = AttemptCredit(timeout=deadline, left_s=float(timeout_s))
        token: Token[AttemptCredit | None] = _CREDIT.set(credit)
        try:
            return await attempt
        finally:
            _CREDIT.reset(token)
