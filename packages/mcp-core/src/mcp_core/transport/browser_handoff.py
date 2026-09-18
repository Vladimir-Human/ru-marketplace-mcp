"""Bounded, process-local ownership of browser challenge tabs.

No browser storage or extracted content is persisted. A lease keeps its original
context manager alive; resuming never searches for tabs or navigates again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from mcp_core.errors import NotFoundError, TransportDownError, UpstreamTimeoutError
from mcp_core.transport import chrome_cdp
from mcp_core.transport.chrome_cdp import PageLike

Read = Callable[[PageLike], Awaitable[dict[str, Any]]]
Challenge = Callable[[dict[str, Any]], str | None]
Result = tuple[dict[str, Any], str | None]
Key = tuple[str, str, str, str, str]

# Registry size and timeouts, all operator-tunable (R2, 2026-09-18). The old
# shape — one 300 s window and four slots — was sized for a single challenge
# retry. A compare fan-out over CDP sources wants one retained page per source,
# so the registry defaults to eight, and life is now bounded two ways: a lifetime
# (how long a retained page may exist at all, never extended) and an idle bound
# (how long it may sit untouched). The idle bound only ever shortens a lease, so
# the documented "expiry is immutable and never extended by retries" guarantee
# still holds.
_DEFAULT_MAX_LEASES = 8
_DEFAULT_IDLE_S = 600.0
_MAX_LIFETIME_S = 900.0


class HandoffBusyError(TransportDownError):
    """An owned tab is already being read, or the bounded registry is full.

    R3: the two cases are explained rather than merged into "busy". A caller can
    act on the difference — one means "wait a moment", the other means "close or
    let expire one of the retained pages first".
    """

    def __init__(self, *, reason: str = "busy", retry_after_s: float | None = None) -> None:
        hints = {
            "busy": "another operation is reading the retained page right now",
            "registry_full": "the retained-page registry is full; a page must expire or be used up first",
        }
        detail = hints.get(reason, reason)
        super().__init__(
            f"Browser handoff is busy: {detail}. Retry the same operation later.",
            status_code=409,
            retry_after_s=retry_after_s,
        )
        self.reason = reason


def _env_seconds(name: str, default: float, cap: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return min(value, cap)


def _duration_s() -> float:
    """Lifetime of a retained page, in seconds (0 = retention off)."""
    try:
        value = float(os.environ.get("CHROME_CHALLENGE_HANDOFF_S", "0"))
    except ValueError:
        return 0
    return min(value, _MAX_LIFETIME_S) if math.isfinite(value) and value > 0 else 0


def _idle_s() -> float:
    """Drop a retained page that nobody touched for this long.

    Clamped to the lifetime: an idle bound longer than the page's own life would
    be a second way of saying "never", which is how the two-timeout shape gets
    misread.
    """
    lifetime = _duration_s()
    idle = _env_seconds("CHROME_CHALLENGE_HANDOFF_IDLE_S", _DEFAULT_IDLE_S, _MAX_LIFETIME_S)
    return min(idle, lifetime) if lifetime else idle


def _max_leases() -> int:
    try:
        value = int(os.environ.get("CHROME_CHALLENGE_HANDOFF_MAX", "") or _DEFAULT_MAX_LEASES)
    except ValueError:
        return _DEFAULT_MAX_LEASES
    return value if value >= 1 else _DEFAULT_MAX_LEASES


@dataclass
class _Request:
    read: Read
    challenge: Challenge
    hosts: frozenset[str]
    result: asyncio.Future[Result]
    snapshot: bool = False


@dataclass
class _Lease:
    deadline: float
    expires_at: str
    handoff_id: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    hosts: frozenset[str] = frozenset()
    requests: asyncio.Queue[_Request] = field(default_factory=asyncio.Queue)
    busy: bool = True
    cleaning: bool = False
    task: asyncio.Task[None] | None = None
    last_used: float = 0.0
    last_digest: str | None = None
    reads: int = 0


def _payload_digest(payload: dict[str, Any]) -> str:
    """A stable fingerprint of one read, so nothing has to be retained to compare.

    Found by an independent review: keeping the payload object itself compared a
    dict the *caller* then mutates (connectors attach ``_resume`` and
    ``_handoff_*`` keys to it), so every resumed read looked "changed" — and it
    also kept marketplace data alive in the registry for the whole retention
    window, which the worker explicitly forbids.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resume_note(lease: _Lease, response: Result, *, resumed: bool, url: str) -> dict[str, Any]:
    """Say what changed on this call — the question a resumed read should answer.

    ``data_changed`` is deliberately tri-state: ``True``/``False`` compare against
    the previous read of this same owned page, and ``None`` means there was
    nothing to compare with yet (a first read, not "nothing changed").
    """
    payload = response[0] if isinstance(response[0], dict) else {}
    digest = _payload_digest(payload)
    changed: bool | None = None if lease.last_digest is None else digest != lease.last_digest
    lease.last_digest = digest
    lease.reads += 1
    still_challenged = response[1] is not None
    return {
        "resumed": resumed,
        "operation_url": url,
        "reads": lease.reads,
        "challenge": "still_required" if still_challenged else "cleared",
        "challenge_cleared": not still_challenged,
        "data_changed": changed,
        "summary": _resume_summary(resumed=resumed, cleared=not still_challenged, changed=changed),
    }


def _resume_summary(*, resumed: bool, cleared: bool, changed: bool | None) -> str:
    if not resumed:
        return "opened a new page; nothing was resumed"
    if not cleared:
        return "resumed the retained page; the challenge is still present"
    if changed is True:
        return "resumed the retained page; the challenge is cleared and the data changed"
    if changed is False:
        return "resumed the retained page; the challenge is cleared and nothing changed"
    return "resumed the retained page; the challenge is cleared"


def _expired(lease: _Lease, now: float) -> bool:
    """Lifetime or idle bound reached — either one ends the retention.

    Two clocks, because they answer different questions: the lifetime says how
    long a retained page may exist at all (never extended by a retry), the idle
    bound says it has been forgotten about. The idle bound only ever shortens a
    lease, so resume/expiry semantics stay a subset of the old behaviour.
    """
    if lease.deadline <= now:
        return True
    idle = _idle_s()
    return bool(idle) and lease.last_used > 0 and lease.last_used + idle <= now


_leases: dict[Key, _Lease] = {}


def _key(scope: str, operation: str, url: str) -> Key:
    return scope, operation, url, chrome_cdp.CDP_URL, chrome_cdp.SCRAPING_PROFILE


def has_pending_handoff(*, scope: str | None, operation: str, url: str) -> bool:
    """Whether this exact operation owns a lease, including busy/expiring cleanup."""
    if not scope:
        return False
    return _key(scope, operation, url) in _leases


def get_handoff_id(*, scope: str | None, operation: str, url: str) -> str | None:
    """Return an opaque handle only for this exact live operation's lease."""
    lease = _leases.get(_key(scope, operation, url)) if scope else None
    if lease is None or lease.cleaning or lease.deadline <= asyncio.get_running_loop().time():
        return None
    return lease.handoff_id


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
                        hosts = lease.hosts if current.snapshot else current.hosts
                        chrome_cdp._check_final_host(await chrome_cdp.current_page_url(page), hosts)
                        payload = await current.read(page)
                        # Navigation during extraction also invalidates the result.
                        final_url = await chrome_cdp.current_page_url(page)
                        chrome_cdp._check_final_host(final_url, hosts)
                        if current.snapshot:
                            parsed = urlsplit(final_url)
                            host = parsed.hostname or ""
                            origin_host = f"[{host}]" if ":" in host else host
                            payload.update(
                                captured_at=datetime.now(UTC).isoformat(),
                                handoff_expires_at=lease.expires_at,
                                operation=key[1],
                                page_origin=f"{parsed.scheme}://{origin_host}"
                                + (f":{parsed.port}" if parsed.port else ""),
                            )
                            current.result.set_result((payload, lease.expires_at))
                            del payload
                            current = None
                            lease.busy = False
                            current = await lease.requests.get()
                            continue
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


def handoff_diagnostics() -> dict[str, Any]:
    """Read-only view of the lease registry — never opens, resumes or closes a tab.

    Operators (and selfcheck-style tools) need to answer "what is retained right
    now, and why would it disappear" without spending a navigation. Expiry is
    reported as two remaining budgets, because lifetime and idle end a lease for
    different reasons and an operator acts on them differently.
    """
    try:
        now = asyncio.get_running_loop().time()
    except RuntimeError:  # no running loop: fall back to the monotonic clock
        now = time.monotonic()
    idle = _idle_s()
    leases = []
    for key, lease in tuple(_leases.items()):
        scope, operation, url, endpoint, profile = key
        leases.append(
            {
                "scope": scope,
                "operation": operation,
                "url": url,
                "endpoint": endpoint,
                "profile": profile,
                "expires_at": lease.expires_at,
                "lifetime_remaining_s": round(max(0.0, lease.deadline - now), 3),
                "idle_remaining_s": round(max(0.0, lease.last_used + idle - now), 3) if idle else None,
                "busy": lease.busy,
                "cleaning": lease.cleaning,
                "expired": _expired(lease, now),
                "queued_requests": lease.requests.qsize(),
                "handoff_id": lease.handoff_id,
            }
        )
    return {
        "retention_enabled": bool(_duration_s()),
        "lifetime_s": _duration_s(),
        "idle_s": idle,
        "max_leases": _max_leases(),
        "count": len(leases),
        "leases": leases,
    }


async def snapshot_handoff(*, scope: str | None, handoff_id: str) -> dict[str, Any]:
    """Capture a retained page in this session; never open or navigate a tab."""
    if not handoff_id.isascii():
        raise NotFoundError("Browser handoff is unavailable")
    loop = asyncio.get_running_loop()
    found = next(
        (
            lease
            for key, lease in _leases.items()
            if scope
            and key[0] == scope
            and key[3:] == (chrome_cdp.CDP_URL, chrome_cdp.SCRAPING_PROFILE)
            and secrets.compare_digest(lease.handoff_id, handoff_id)
        ),
        None,
    )
    if found is None:
        # Unknown or another session's handle. Deliberately not distinguished:
        # telling a caller that a handle exists elsewhere would be an oracle.
        raise NotFoundError("Browser handoff is unavailable")
    if found.cleaning or _expired(found, loop.time()):
        # This handle is the caller's own, so saying why it is gone costs no
        # secrecy and saves a pointless retry with the same id.
        reason = "expired" if _expired(found, loop.time()) else "being released"
        raise NotFoundError(
            f"Browser handoff is no longer retained ({reason}); run the operation again to obtain a fresh page"
        )
    if found.busy:
        raise HandoffBusyError(reason="busy", retry_after_s=5.0)
    found.busy = True
    found.last_used = loop.time()
    result: asyncio.Future[Result] = loop.create_future()
    found.requests.put_nowait(_Request(chrome_cdp.capture_owned_viewport, lambda _: None, found.hosts, result, True))
    try:
        response = await asyncio.shield(result)
        return response[0]
    except BaseException:
        await _stop(found)
        if result.done() and not result.cancelled():
            result.exception()
        raise


async def read_with_handoff(
    *,
    url: str,
    wait_ms: int,
    scope: str | None,
    operation: str,
    read: Read,
    challenge: Challenge,
    allowed_hosts: Collection[str] | None = None,
    note_out: dict[str, Any] | None = None,
) -> Result:
    """Read once, or resume the exact same session's owned challenge tab.

    Retention requires explicit positive CHROME_CHALLENGE_HANDOFF_S, a session
    scope and a headed browser. Expiry includes initial attach/navigation and is
    never extended by retries. The returned timestamp only describes retention;
    foreground activation is best-effort on the connected browser's machine.

    R3: when ``note_out`` is given it is filled with what actually happened on
    this call — whether a retained page was resumed, whether the challenge is
    gone, and whether the data differs from the previous read of the same page
    (``data_changed`` is ``None`` when there is nothing to compare against, which
    is not the same as "nothing changed").
    """
    duration = _duration_s()
    if not duration or not scope or chrome_cdp.HEADLESS:
        async with chrome_cdp.open_page(url, wait_ms, allowed_hosts=allowed_hosts) as page:
            return await read(page), None

    key = _key(scope, operation, url)
    hosts = frozenset(allowed_hosts or {urlsplit(url).hostname or ""})
    loop = asyncio.get_running_loop()
    lease = _leases.get(key)
    if lease is not None and lease.busy:
        raise HandoffBusyError(reason="busy", retry_after_s=5.0)
    if lease is not None and _expired(lease, loop.time()):
        # Mark busy until cleanup completes: a concurrent retry must not open a duplicate.
        lease.busy = True
        await _stop(lease)
        lease = None
    if lease is None:
        if len(_leases) >= _max_leases():
            raise HandoffBusyError(reason="registry_full")
        now = loop.time()
        lease = _Lease(
            deadline=now + duration,
            expires_at=(datetime.now(UTC) + timedelta(seconds=duration)).isoformat(),
            hosts=hosts,
            last_used=now,
        )
        _leases[key] = lease
        resumed = False
    else:
        resumed = True
    lease.busy = True
    result: asyncio.Future[Result] = loop.create_future()
    lease.requests.put_nowait(_Request(read, challenge, hosts, result))
    if lease.task is None:
        lease.task = asyncio.create_task(_run(key, lease, url, wait_ms), name="browser-handoff")
    try:
        response = await asyncio.shield(result)
        if response[1] is None:
            await asyncio.shield(lease.task)
        # The page was just used: the idle clock restarts, the lifetime does not.
        lease.last_used = loop.time()
        if note_out is not None:
            note_out.update(_resume_note(lease, response, resumed=resumed, url=url))
        return response
    except BaseException:
        await _stop(lease)
        # Consume a concurrent extractor failure if cancellation won the race.
        if result.done() and not result.cancelled():
            result.exception()
        raise
