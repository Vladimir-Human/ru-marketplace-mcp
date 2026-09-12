"""Bounded, process-local ownership of browser challenge tabs.

No browser storage or extracted content is persisted. A lease keeps its original
context manager alive; resuming never searches for tabs or navigates again.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from mcp_core.errors import TransportDownError, UpstreamTimeoutError
from mcp_core.transport import chrome_cdp
from mcp_core.transport.chrome_cdp import PageLike

Read = Callable[[PageLike], Awaitable[dict[str, Any]]]
Challenge = Callable[[dict[str, Any]], str | None]
Result = tuple[dict[str, Any], str | None]
Key = tuple[str, str, str, str, str]
_MAX_LEASES = 4


class HandoffBusyError(TransportDownError):
    """An owned tab is already being read, or the bounded registry is full."""

    def __init__(self) -> None:
        super().__init__("Browser handoff is busy; retry the same operation later", status_code=409)


def _duration_s() -> float:
    try:
        value = float(os.environ.get("CHROME_CHALLENGE_HANDOFF_S", "0"))
    except ValueError:
        return 0
    return min(value, 300) if math.isfinite(value) and value > 0 else 0


@dataclass
class _Request:
    read: Read
    challenge: Challenge
    hosts: frozenset[str]
    result: asyncio.Future[Result]


@dataclass
class _Lease:
    deadline: float
    expires_at: str
    requests: asyncio.Queue[_Request] = field(default_factory=asyncio.Queue)
    busy: bool = True
    cleaning: bool = False
    task: asyncio.Task[None] | None = None


_leases: dict[Key, _Lease] = {}


def _key(scope: str, operation: str, url: str) -> Key:
    return scope, operation, url, chrome_cdp.CDP_URL, chrome_cdp.SCRAPING_PROFILE


def has_pending_handoff(*, scope: str | None, operation: str, url: str) -> bool:
    """Whether this exact operation owns a lease, including busy/expiring cleanup."""
    if not scope:
        return False
    return _key(scope, operation, url) in _leases


async def _run(key: Key, lease: _Lease, url: str, wait_ms: int) -> None:
    current: _Request | None = None
    terminal_error: Exception = TransportDownError("Browser handoff ended; retry the operation")
    cancelled = False
    guard = object()
    chrome_cdp._HANDOFF_VISIBILITY_GUARDS.add(guard)
    try:
        current = await lease.requests.get()
        async with asyncio.timeout_at(lease.deadline):
            async with chrome_cdp.open_page(url, wait_ms, allowed_hosts=current.hosts) as page:
                try:
                    while True:
                        chrome_cdp._check_final_host(await chrome_cdp.current_page_url(page), current.hosts)
                        payload = await current.read(page)
                        # Navigation during extraction also invalidates the result.
                        chrome_cdp._check_final_host(await chrome_cdp.current_page_url(page), current.hosts)
                        blocked = current.challenge(payload)
                        if not blocked:
                            current.result.set_result((payload, None))
                            return
                        await chrome_cdp.reveal_owned_page(page)
                        lease.busy = False
                        current.result.set_result((payload, lease.expires_at))
                        # Do not retain payloads, futures, or caller closures between requests.
                        del payload
                        current = None
                        current = await lease.requests.get()
                finally:
                    lease.cleaning = True
    except TimeoutError:
        terminal_error = UpstreamTimeoutError("Browser handoff deadline expired")
        if current is not None and not current.result.done():
            current.result.set_exception(terminal_error)
    except asyncio.CancelledError:
        cancelled = True
        if current is not None and not current.result.done():
            current.result.cancel()
        raise
    except Exception as exc:
        terminal_error = exc
        if current is not None and not current.result.done():
            current.result.set_exception(exc)
    finally:
        lease.cleaning = True
        chrome_cdp._HANDOFF_VISIBILITY_GUARDS.discard(guard)
        if _leases.get(key) is lease:
            del _leases[key]
        # A retry may wake Queue.get just before deadline cancellation. In that
        # case get has not dequeued it, and ``current`` is still None. Settle
        # every queued caller on all exits, before cleanup yields again.
        while not lease.requests.empty():
            pending = lease.requests.get_nowait()
            if not pending.result.done():
                if cancelled:
                    pending.result.cancel()
                else:
                    pending.result.set_exception(terminal_error)
        if chrome_cdp.STEALTH and not chrome_cdp._HANDOFF_VISIBILITY_GUARDS:
            try:
                await asyncio.wait_for(asyncio.to_thread(chrome_cdp._hide_chrome_windows), timeout=8)
            except Exception:
                pass


async def _stop(lease: _Lease) -> None:
    if lease.task is not None:
        # A second cancellation must not interrupt the transport's bounded tab
        # close while the first cancellation (or normal success) is cleaning up.
        if not lease.cleaning and not lease.task.cancelling():
            lease.task.cancel()
        try:
            await asyncio.shield(lease.task)
        except asyncio.CancelledError:
            pass
        finally:
            # A task cancelled before its first instruction never enters _run's
            # finally block. Remove only that lease, never its replacement.
            # A second caller cancellation may interrupt this shield while
            # transport cleanup continues; _run still owns that registry slot.
            if lease.task.done():
                for key, registered in tuple(_leases.items()):
                    if registered is lease:
                        del _leases[key]
                while not lease.requests.empty():
                    pending = lease.requests.get_nowait()
                    if not pending.result.done():
                        pending.result.cancel()


async def close_handoffs() -> None:
    """Release all owned tabs during graceful server shutdown."""
    await asyncio.gather(*(_stop(lease) for lease in tuple(_leases.values())))


async def read_with_handoff(
    *,
    url: str,
    wait_ms: int,
    scope: str | None,
    operation: str,
    read: Read,
    challenge: Challenge,
    allowed_hosts: Collection[str] | None = None,
) -> Result:
    """Read once, or resume the exact same session's owned challenge tab.

    Retention requires explicit positive CHROME_CHALLENGE_HANDOFF_S, a session
    scope and a headed browser. Expiry includes initial attach/navigation and is
    never extended by retries. The returned timestamp only describes retention;
    foreground activation is best-effort on the connected browser's machine.
    """
    duration = _duration_s()
    if not duration or not scope or chrome_cdp.HEADLESS:
        async with chrome_cdp.open_page(url, wait_ms, allowed_hosts=allowed_hosts) as page:
            return await read(page), None

    key = _key(scope, operation, url)
    loop = asyncio.get_running_loop()
    lease = _leases.get(key)
    if lease is not None and lease.busy:
        raise HandoffBusyError()
    if lease is not None and lease.deadline <= loop.time():
        # Mark busy until cleanup completes: a concurrent retry must not open a duplicate.
        lease.busy = True
        await _stop(lease)
        lease = None
    if lease is None:
        if len(_leases) >= _MAX_LEASES:
            raise HandoffBusyError()
        lease = _Lease(
            deadline=loop.time() + duration,
            expires_at=(datetime.now(UTC) + timedelta(seconds=duration)).isoformat(),
        )
        _leases[key] = lease
    lease.busy = True
    hosts = frozenset(allowed_hosts or {urlsplit(url).hostname or ""})
    result: asyncio.Future[Result] = loop.create_future()
    lease.requests.put_nowait(_Request(read, challenge, hosts, result))
    if lease.task is None:
        lease.task = asyncio.create_task(_run(key, lease, url, wait_ms), name="browser-handoff")
    try:
        response = await asyncio.shield(result)
        if response[1] is None:
            await asyncio.shield(lease.task)
        return response
    except BaseException:
        await _stop(lease)
        # Consume a concurrent extractor failure if cancellation won the race.
        if result.done() and not result.cancelled():
            result.exception()
        raise
