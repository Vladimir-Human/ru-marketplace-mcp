"""Refuse a main-frame document request to a host outside the caller's policy.

A store page may answer with a 3xx redirect to another host. Chrome follows
the chain and the foreign page loads with the scraping profile's cookies for
it, before ``open_page``'s final-URL check can refuse the call. The call still
fails, but the request has already left the machine.

This module pauses every main-frame document request at the request stage
(CDP Fetch domain), checks each hop against the caller's host policy and
fails an off-policy hop with ``BlockedByClient`` before Chrome sends it.
Sub-frame documents and subresources are not touched, and the interception is
switched off once the main document has answered, so later loads in the same
tab are not paused.
"""
# START_MODULE_CONTRACT
# PURPOSE: Refuse a main-frame document request to a host outside the caller's policy before Chrome sends it.
# SCOPE: CDP Fetch interception of main-frame documents: enable, decide, answer, disable; both CDP transports.
# DEPENDS: mcp_core.kit url_on_hosts; mcp_core.logging log_event; mcp_core.transport.chrome_cdp NavigationPolicyError (deferred, to keep the import one-way); stdlib asyncio,collections.abc,contextlib,typing.
# LINKS: M-NAV-GUARD; M-CHROME-CDP; M-KIT; R-CDP-REDIRECT-GUARD.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# NavGuard: One navigation's interception: the paused-request decision and the CDP answers.
# playwright_guard: Attach a CDP session to a Playwright page and guard its navigation.
# raw_guard: Guard one raw-CDP navigation on the page's own socket and message loop.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from typing import Any

from mcp_core.kit import url_on_hosts
from mcp_core.logging import log_event

Send = Callable[[str, dict[str, Any]], Awaitable[Any]]

BLOCKED_BY_CLIENT = "BlockedByClient"
# Only main-frame documents, only at the request stage: the response stage
# would arrive after the foreign request had already been sent.
DOCUMENT_PATTERNS: list[dict[str, str]] = [{"resourceType": "Document", "requestStage": "Request"}]
# A CDP command that never answers must not hold a navigation open.
COMMAND_TIMEOUT_S = 10.0
# Switching the interception off and detaching runs on every exit, also after a
# navigation timed out; it must not stretch the caller's own deadline.
CLEANUP_TIMEOUT_S = 1.0


def _policy_error(url: str, allowed_hosts: Collection[str]) -> BaseException:
    """The caller's own refusal for a hop outside its host policy."""
    from mcp_core.transport.chrome_cdp import NavigationPolicyError

    return NavigationPolicyError(url, allowed_hosts)


class NavGuard:
    """The interception of one navigation's main-frame document requests.

    The guard is asked about every paused request and answers each one:
    an allowed request continues, an off-policy one is failed with
    ``BlockedByClient`` and remembered as the blocked hop.
    """

    def __init__(self, *, send: Send | None, allowed_hosts: Collection[str], reply: Send | None = None) -> None:
        self._send = send
        self._reply = reply if reply is not None else send
        self._allowed = frozenset(allowed_hosts)
        self._main_frame_id: str | None = None
        self._blocked_url: str | None = None
        self._enabled = False
        self._pending: set[asyncio.Task[Any]] = set()

    @property
    def blocked_url(self) -> str | None:
        """The off-policy hop that was failed, or None when none was."""
        return self._blocked_url

    @property
    def blocked(self) -> bool:
        """Whether a hop was refused."""
        return self._blocked_url is not None

    @property
    def enabled(self) -> bool:
        """Whether the interception is on."""
        return self._enabled

    def note_main_frame(self, frame_id: Any) -> None:
        """Remember the main frame's id; other frames' documents pass untouched."""
        if isinstance(frame_id, str) and frame_id:
            self._main_frame_id = frame_id

    async def enable(self) -> bool:
        """Pause this page's main-frame documents; False when Chrome refused."""
        if self._send is None:
            return False
        try:
            await asyncio.wait_for(
                self._send("Fetch.enable", {"patterns": [dict(pattern) for pattern in DOCUMENT_PATTERNS]}),
                timeout=COMMAND_TIMEOUT_S,
            )
        except Exception:
            self._enabled = False
            return False
        self._enabled = True
        return True

    async def disable(self) -> None:
        """Stop the interception so later loads in the tab are not paused."""
        if not self._enabled:
            return
        self._enabled = False
        sender = self._reply if self._reply is not None else self._send
        if sender is None:
            return
        try:
            # The answer is not waited for: the raw page's own loop must not
            # read an event that belongs to the navigation it just finished.
            await asyncio.wait_for(sender("Fetch.disable", {}), timeout=CLEANUP_TIMEOUT_S)
        except Exception:
            pass

    def handle_paused(self, params: Mapping[str, Any]) -> None:
        """Answer a paused request without blocking the event loop that reported it."""
        try:
            task = asyncio.create_task(self.answer_paused(params))
        except RuntimeError:
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def answer_paused(self, params: Mapping[str, Any]) -> None:
        """Continue an allowed request; fail an off-policy one with ``BlockedByClient``.

        Every paused request is answered, also when the decision itself
        failed: an unanswered request pauses the navigation until its timeout.
        """
        request_id = params.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            return
        request = params.get("request")
        request = request if isinstance(request, Mapping) else {}
        url = request.get("url")
        url = url if isinstance(url, str) else ""
        try:
            blocked = self._is_off_policy(params, url)
        except Exception:
            blocked = True  # fail closed: a hop that cannot be judged is not continued
        if blocked:
            self._blocked_url = url if self._blocked_url is None else self._blocked_url
        if self._reply is None:
            return
        try:
            if blocked:
                await asyncio.wait_for(
                    self._reply("Fetch.failRequest", {"requestId": request_id, "errorReason": BLOCKED_BY_CLIENT}),
                    timeout=COMMAND_TIMEOUT_S,
                )
            else:
                await asyncio.wait_for(
                    self._reply("Fetch.continueRequest", {"requestId": request_id}), timeout=COMMAND_TIMEOUT_S
                )
        except Exception:
            # The answer did not reach Chrome; the navigation ends by its own deadline.
            pass

    async def handle_message(self, method: str, params: Mapping[str, Any]) -> bool:
        """Answer the raw page's paused requests; True when a hop was refused."""
        if method == "Page.frameNavigated":
            frame = params.get("frame")
            if isinstance(frame, Mapping) and not frame.get("parentId"):
                self.note_main_frame(frame.get("id"))
            return False
        if method != "Fetch.requestPaused":
            return False
        await self.answer_paused(params)
        return self.blocked

    def _is_off_policy(self, params: Mapping[str, Any], url: str) -> bool:
        """Whether this paused request leaves the caller's host policy."""
        resource_type = params.get("resourceType")
        if isinstance(resource_type, str) and resource_type != "Document":
            return False
        if not self._is_main_frame(params.get("frameId")):
            return False
        return not url_on_hosts(url, self._allowed)

    def _is_main_frame(self, frame_id: Any) -> bool:
        if self._main_frame_id is None:
            # The first paused document is the main frame's: a sub-frame needs a
            # parent document, so it cannot be requested before the main one.
            self.note_main_frame(frame_id)
            return True
        return isinstance(frame_id, str) and frame_id == self._main_frame_id


@asynccontextmanager
async def playwright_guard(ctx: Any, page: Any, *, allowed_hosts: Collection[str] | None) -> AsyncIterator[NavGuard]:
    """Guard one Playwright navigation with a CDP session of its own.

    Playwright's own routing is not used: it never sees a redirect hop. The
    session pauses the page's main-frame documents and the guard answers each
    one. A refused hop ends the call with the caller's host-policy error.
    """
    hosts = frozenset(allowed_hosts or ())
    if not hosts:
        yield NavGuard(send=None, allowed_hosts=hosts)
        return
    session: Any = None
    guard = NavGuard(send=None, allowed_hosts=hosts)
    try:
        try:
            session = await ctx.new_cdp_session(page)
        except Exception as exc:
            session = None
            log_event("cdp.nav_guard.unavailable", transport="playwright", reason=type(exc).__name__)
        if session is not None:
            guard = NavGuard(send=session.send, allowed_hosts=hosts)
            try:
                tree = await session.send("Page.getFrameTree")
                frame = ((tree or {}).get("frameTree") or {}).get("frame") or {}
                guard.note_main_frame(frame.get("id"))
                session.on("Fetch.requestPaused", guard.handle_paused)
            except Exception as exc:
                log_event("cdp.nav_guard.unavailable", transport="playwright", reason=type(exc).__name__)
            if not await guard.enable():
                log_event("cdp.nav_guard.unavailable", transport="playwright", reason="fetch_enable_refused")
        yield guard
    finally:
        await guard.disable()
        if session is not None:
            try:
                session.remove_listener("Fetch.requestPaused", guard.handle_paused)
            except Exception:
                pass
            try:
                await asyncio.wait_for(session.detach(), timeout=CLEANUP_TIMEOUT_S)
            except Exception:
                pass
        if guard.blocked:
            raise _policy_error(guard.blocked_url or "", hosts)


@asynccontextmanager
async def raw_guard(page: Any, *, allowed_hosts: Collection[str] | None) -> AsyncIterator[NavGuard]:
    """Guard one raw-CDP navigation on the page's own socket.

    The page's message loop reads the paused requests and answers them
    through the guard; the guard's answers are written to the socket without
    waiting, so the loop keeps reading the navigation's own events.
    """
    hosts = frozenset(allowed_hosts or ())
    if not hosts:
        yield NavGuard(send=None, allowed_hosts=hosts)
        return
    guard = NavGuard(send=page._send, allowed_hosts=hosts, reply=page._send_nowait)
    page._nav_guard = guard
    try:
        if not await guard.enable():
            log_event("cdp.nav_guard.unavailable", transport="raw", reason="fetch_enable_refused")
        yield guard
    finally:
        page._nav_guard = None
        await guard.disable()
        if guard.blocked:
            raise _policy_error(guard.blocked_url or "", hosts)
