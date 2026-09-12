"""Ownership, bounded recovery and cancellation checks without real browser data."""

from __future__ import annotations

import asyncio
import gc
import weakref
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp_core.errors import NotFoundError, UpstreamTimeoutError
from mcp_core.transport import browser_handoff as handoff
from mcp_core.transport import chrome_cdp


@pytest.fixture
async def browser(monkeypatch):
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", "30")
    monkeypatch.setattr(chrome_cdp, "HEADLESS", False)
    pages = []

    @asynccontextmanager
    async def open_page(url, wait_ms=0, *, allowed_hosts=None):
        page = SimpleNamespace(url=url, closed=False, extracted=0, released=asyncio.Event())
        pages.append(page)
        try:
            yield page
        finally:
            page.closed = True
            page.released.set()

    monkeypatch.setattr(chrome_cdp, "open_page", open_page)
    monkeypatch.setattr(chrome_cdp, "reveal_owned_page", AsyncMock(return_value=True))
    monkeypatch.setattr(chrome_cdp, "_hide_chrome_windows", lambda: None)
    yield pages
    await handoff.close_handoffs()
    assert not handoff._leases
    assert not chrome_cdp._HANDOFF_VISIBILITY_GUARDS


async def blocked(page):
    page.extracted += 1
    return {"challenge": "captcha", "visit": page.extracted}


async def success(page):
    page.extracted += 1
    return {"products": ["observed"]}


def call(**kwargs):
    return handoff.read_with_handoff(
        **{
            "url": "https://shop.test/search?q=a",
            "wait_ms": 0,
            "scope": "session-1",
            "operation": "search",
            "read": blocked,
            "challenge": lambda payload: payload.get("challenge"),
            **kwargs,
        }
    )


@pytest.mark.parametrize("duration", ["0", "-1", "bad", "nan", "inf", "-inf", ""])
async def test_disabled_or_invalid_duration_preserves_short_lifecycle(browser, monkeypatch, duration):
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", duration)
    payload, expiry = await call()
    assert payload["challenge"] == "captcha"
    assert expiry is None
    assert browser[0].closed
    assert not handoff._leases


async def test_missing_scope_and_headless_disable_retention(browser, monkeypatch):
    assert (await call(scope=None))[1] is None
    monkeypatch.setattr(chrome_cdp, "HEADLESS", True)
    assert (await call())[1] is None
    assert all(page.closed for page in browser)


def test_duration_has_hard_five_minute_cap(monkeypatch):
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", "999999")
    assert handoff._duration_s() == 300


async def test_challenge_resumes_exact_page_with_new_read_and_immutable_expiry(browser):
    first, expiry = await call()
    assert expiry is not None
    assert first["visit"] == 1
    assert not browser[0].closed
    again, next_expiry = await call()
    assert again["visit"] == 2
    assert next_expiry == expiry
    payload, final_expiry = await call(read=success)
    assert payload == {"products": ["observed"]}
    assert final_expiry is None
    assert len(browser) == 1  # Opening a context is the only navigation path.
    assert browser[0].closed
    assert not handoff._leases


@pytest.mark.parametrize(
    "changes",
    [{"scope": "session-2"}, {"operation": "card"}, {"url": "https://shop.test/search?q=b"}],
)
async def test_other_session_operation_or_query_never_adopts_retained_page(browser, changes):
    await call()
    await call(**changes)
    assert len(browser) == 2
    assert all(not page.closed for page in browser)


@pytest.mark.parametrize("setting", ["CDP_URL", "SCRAPING_PROFILE"])
async def test_other_endpoint_or_profile_never_adopts_retained_page(browser, monkeypatch, setting):
    await call()
    monkeypatch.setattr(chrome_cdp, setting, "other-private-value")
    await call()
    assert len(browser) == 2


async def test_busy_initial_and_resume_never_open_duplicate(browser):
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending(page):
        started.set()
        await release.wait()
        return await blocked(page)

    for _ in range(2):
        started.clear()
        release.clear()
        task = asyncio.create_task(call(read=pending))
        await started.wait()
        with pytest.raises(handoff.HandoffBusyError) as caught:
            await call()
        assert caught.value.status_code == 409
        assert caught.value.code.retryable
        release.set()
        await task
    assert len(browser) == 1


async def test_retention_expires_and_closes_without_a_retry(browser, monkeypatch):
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", "0.05")
    await call()
    task = next(iter(handoff._leases.values())).task
    await asyncio.wait_for(browser[0].released.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=1)
    assert not handoff._leases


async def test_deadline_cancels_extractor_and_closes(browser, monkeypatch):
    monkeypatch.setenv("CHROME_CHALLENGE_HANDOFF_S", "0.05")
    cancelled = asyncio.Event()

    async def pending(page):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(UpstreamTimeoutError):
        await call(read=pending)
    assert cancelled.is_set()
    assert browser[0].closed


@pytest.mark.parametrize("resume", [False, True])
async def test_caller_cancellation_cleans_worker_and_owned_page(browser, resume):
    if resume:
        await call()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending(page):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(call(read=pending))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert browser[0].closed
    assert not handoff._leases


async def test_extractor_failure_releases_retained_page(browser):
    await call()

    async def fail(page):
        raise ValueError("extractor failed")

    with pytest.raises(ValueError, match="extractor failed"):
        await call(read=fail)
    assert browser[0].closed


async def test_moved_page_is_closed_before_new_extraction(browser):
    await call()
    browser[0].url = "https://outside.test/private"
    read = AsyncMock()
    with pytest.raises(chrome_cdp.NavigationPolicyError):
        await call(read=read)
    read.assert_not_called()
    assert browser[0].closed


async def test_new_policy_checked_on_resume_and_navigation_during_read_rejected(browser):
    await call(allowed_hosts=["shop.test", "login.shop.test"])
    browser[0].url = "https://login.shop.test/challenge"
    with pytest.raises(chrome_cdp.NavigationPolicyError):
        await call(allowed_hosts=["shop.test"])

    async def navigate(page):
        page.url = "https://outside.test/private"
        return {"products": ["should not escape"]}

    with pytest.raises(chrome_cdp.NavigationPolicyError):
        await call(read=navigate)
    assert all(page.closed for page in browser)


async def test_capacity_does_not_evict_an_existing_handoff(browser):
    for index in range(4):
        await call(scope=f"session-{index}")
    with pytest.raises(handoff.HandoffBusyError):
        await call(scope="overflow")
    assert len(browser) == 4
    await call(scope="session-0", read=success)
    await call(scope="overflow")
    assert len(browser) == 5


async def test_shutdown_releases_all_pages(browser):
    await call(scope="one")
    await call(scope="two")
    await handoff.close_handoffs()
    assert all(page.closed for page in browser)
    assert not handoff._leases


async def test_challenge_worker_does_not_hold_caller_closure_or_payload(browser):
    class Payload(dict):
        pass

    class Reader:
        async def __call__(self, page):
            return Payload(challenge="captcha")

    reader = Reader()
    reader_ref = weakref.ref(reader)
    payload, _ = await call(read=reader)
    payload_ref = weakref.ref(payload)
    del payload, reader
    # asyncio's completed shield callback lives until the next event-loop tick.
    await asyncio.sleep(0)
    gc.collect()
    assert reader_ref() is None
    assert payload_ref() is None


async def test_hide_guard_is_active_only_for_owned_workers(browser, monkeypatch):
    await call()
    assert chrome_cdp._HANDOFF_VISIBILITY_GUARDS
    monkeypatch.setattr(chrome_cdp.sys, "platform", "darwin")
    lookup = AsyncMock()
    monkeypatch.setattr(chrome_cdp, "_scraping_profile_pids", lookup)
    chrome_cdp._hide_chrome_windows()
    lookup.assert_not_called()
    await call(read=success)
    assert not chrome_cdp._HANDOFF_VISIBILITY_GUARDS


async def test_raw_current_url_refreshes_cached_navigation_without_dom_evaluation():
    page = chrome_cdp._RawCdpPage(AsyncMock(), "owned")
    page._send = AsyncMock(return_value={"frameTree": {"frame": {"url": "https://new.test/"}}})
    assert await chrome_cdp.current_page_url(page) == "https://new.test/"
    page._send.assert_awaited_once_with("Page.getFrameTree")


async def test_raw_reveal_restores_only_owned_window():
    page = chrome_cdp._RawCdpPage(AsyncMock(), "owned")
    page._send = AsyncMock(side_effect=[{"windowId": 42, "bounds": {"left": -32000}}, {}, {}, {}])
    assert await chrome_cdp.reveal_owned_page(page)
    calls = page._send.call_args_list
    assert calls[0].args == ("Browser.getWindowForTarget", {"targetId": "owned"})
    assert calls[1].args[1] == {"windowId": 42, "bounds": {"windowState": "normal"}}
    assert calls[2].args[1] == {"windowId": 42, "bounds": {"left": 80, "top": 80}}
    assert calls[3].args == ("Page.bringToFront",)


async def test_reveal_failure_is_honest_and_nonfatal():
    page = chrome_cdp._RawCdpPage(AsyncMock(), "owned")
    page._send = AsyncMock(side_effect=RuntimeError("unsupported Browser command"))
    assert await chrome_cdp.reveal_owned_page(page) is False


async def test_prestart_cancellation_releases_registry_and_pending_caller(browser):
    loop = asyncio.get_running_loop()
    key = ("scope", "operation", "url", "endpoint", "profile")
    lease = handoff._Lease(deadline=loop.time() + 30, expires_at="unused")
    future = loop.create_future()
    lease.requests.put_nowait(handoff._Request(blocked, lambda p: None, frozenset(), future))
    handoff._leases[key] = lease
    lease.task = asyncio.create_task(handoff._run(key, lease, "https://shop.test/", 0))
    # No event-loop yield between task creation and _stop cancelling it.
    await handoff._stop(lease)
    assert future.cancelled()
    assert not handoff._leases
    assert not browser


async def test_expired_retry_cleanup_cannot_create_two_replacements(browser, monkeypatch):
    await call()
    lease = next(iter(handoff._leases.values()))
    lease.deadline = asyncio.get_running_loop().time() - 1
    entered = asyncio.Event()
    release = asyncio.Event()
    original_stop = handoff._stop

    async def delayed_stop(old):
        entered.set()
        await release.wait()
        await original_stop(old)

    monkeypatch.setattr(handoff, "_stop", delayed_stop)
    first = asyncio.create_task(call())
    await entered.wait()
    with pytest.raises(handoff.HandoffBusyError):
        await call()
    release.set()
    await first
    assert len(browser) == 2
    assert browser[0].closed and not browser[1].closed


async def test_cancel_during_success_cleanup_waits_for_exact_owned_close(browser, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    @asynccontextmanager
    async def delayed_close(url, wait_ms=0, *, allowed_hosts=None):
        try:
            yield SimpleNamespace(url=url, extracted=0)
        finally:
            entered.set()
            await release.wait()
            closed.set()

    monkeypatch.setattr(chrome_cdp, "open_page", delayed_close)
    caller = asyncio.create_task(call(read=success))
    await entered.wait()
    caller.cancel()
    await asyncio.sleep(0)
    assert not closed.is_set()
    assert not caller.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert closed.is_set()
    assert not handoff._leases


async def test_idle_expiry_settles_retry_queued_before_getter_resumes(browser, monkeypatch):
    original_timeout_at = asyncio.timeout_at
    deadlines = []

    def capture_timeout(when):
        timeout = original_timeout_at(when)
        deadlines.append(timeout)
        return timeout

    monkeypatch.setattr(handoff.asyncio, "timeout_at", capture_timeout)
    await call()
    lease = next(iter(handoff._leases.values()))
    original_put = lease.requests.put_nowait
    reader = AsyncMock(return_value={"products": ["should not run"]})

    def put_at_deadline(request):
        original_put(request)
        # Deliver the real timeout cancellation after waking Queue.get, before
        # the worker can dequeue/assign ``current``. No timing sleeps required.
        deadlines[0]._on_timeout()

    monkeypatch.setattr(lease.requests, "put_nowait", put_at_deadline)
    with pytest.raises(UpstreamTimeoutError, match="deadline expired"):
        await asyncio.wait_for(call(read=reader), timeout=1)
    reader.assert_not_called()
    assert lease.requests.empty()
    assert browser[0].closed
    assert not handoff._leases


async def test_pending_lookup_includes_busy_expired_lease_without_mutation(browser, monkeypatch):
    lookup = {"scope": "session-1", "operation": "search", "url": "https://shop.test/search?q=a"}
    assert not handoff.has_pending_handoff(**lookup)
    await call()
    lease = next(iter(handoff._leases.values()))
    assert handoff.has_pending_handoff(**lookup)
    lease.busy = True
    lease.deadline = asyncio.get_running_loop().time() - 1
    assert handoff.has_pending_handoff(**lookup)
    assert next(iter(handoff._leases.values())) is lease
    for changes in ({"scope": None}, {"scope": "other"}, {"operation": "card"}, {"url": "https://shop.test/"}):
        assert not handoff.has_pending_handoff(**{**lookup, **changes})
    monkeypatch.setattr(chrome_cdp, "CDP_URL", "http://different.test:9222")
    assert not handoff.has_pending_handoff(**lookup)


async def test_second_caller_cancellation_keeps_lease_until_worker_closes(browser, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    @asynccontextmanager
    async def delayed_close(url, wait_ms=0, *, allowed_hosts=None):
        try:
            yield SimpleNamespace(url=url, extracted=0)
        finally:
            entered.set()
            await release.wait()
            closed.set()

    monkeypatch.setattr(chrome_cdp, "open_page", delayed_close)
    caller = asyncio.create_task(call(read=success))
    await entered.wait()
    lease = next(iter(handoff._leases.values()))
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not closed.is_set()
    assert next(iter(handoff._leases.values())) is lease
    assert not lease.task.done()
    assert chrome_cdp._HANDOFF_VISIBILITY_GUARDS
    release.set()
    await asyncio.wait_for(lease.task, timeout=1)
    assert closed.is_set()
    assert not handoff._leases


def snapshot_id():
    return handoff.get_handoff_id(scope="session-1", operation="search", url="https://shop.test/search?q=a")


async def test_snapshot_keeps_exact_lease_deadline_page_and_returns_sanitized_origin(browser, monkeypatch):
    _, expiry = await call()
    lease = next(iter(handoff._leases.values()))
    deadline = lease.deadline
    identifier = snapshot_id()
    assert identifier and "session-1" not in identifier
    browser[0].url = "https://shop.test:8443/private?token=must-not-escape#secret"
    capture = AsyncMock(side_effect=lambda page: {"image_data": "encoded"})
    monkeypatch.setattr(chrome_cdp, "capture_owned_viewport", capture)
    for _ in range(2):
        payload = await handoff.snapshot_handoff(scope="session-1", handoff_id=identifier)
        assert payload["page_origin"] == "https://shop.test:8443"
        assert payload["operation"] == "search"
        assert payload["handoff_expires_at"] == expiry
        assert payload["captured_at"]
        assert next(iter(handoff._leases.values())) is lease
        assert lease.deadline == deadline
        assert snapshot_id() == identifier
        assert len(browser) == 1 and not browser[0].closed
    assert all(args.args == (browser[0],) for args in capture.await_args_list)
    assert (await call(read=success))[1] is None
    assert browser[0].closed
    assert snapshot_id() is None


@pytest.mark.parametrize(
    "scenario", ["unknown", "foreign", "no-session", "expired", "cleaning", "profile", "endpoint", "unicode"]
)
async def test_unavailable_snapshot_never_opens_or_captures(browser, monkeypatch, scenario):
    await call()
    identifier = snapshot_id()
    lease = next(iter(handoff._leases.values()))
    scope = "session-1"
    if scenario == "unknown":
        identifier = "unknown"
    elif scenario == "unicode":
        identifier = "невозможный-id"
    elif scenario == "foreign":
        scope = "session-2"
    elif scenario == "no-session":
        scope = None
    elif scenario == "expired":
        lease.deadline = asyncio.get_running_loop().time() - 1
    elif scenario == "cleaning":
        lease.cleaning = True
    elif scenario == "profile":
        monkeypatch.setattr(chrome_cdp, "SCRAPING_PROFILE", "different")
    elif scenario == "endpoint":
        monkeypatch.setattr(chrome_cdp, "CDP_URL", "http://different.test")
    capture = AsyncMock()
    monkeypatch.setattr(chrome_cdp, "capture_owned_viewport", capture)
    with pytest.raises(NotFoundError):
        await handoff.snapshot_handoff(scope=scope, handoff_id=identifier)
    capture.assert_not_called()
    assert len(browser) == 1
    # The synthetic cleaning flag must not disable fixture shutdown cancellation.
    lease.cleaning = False


async def test_snapshot_and_resume_cannot_overlap(browser, monkeypatch):
    await call()
    identifier = snapshot_id()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def capture(page):
        entered.set()
        await release.wait()
        return {"image_data": "encoded"}

    monkeypatch.setattr(chrome_cdp, "capture_owned_viewport", capture)
    task = asyncio.create_task(handoff.snapshot_handoff(scope="session-1", handoff_id=identifier))
    await entered.wait()
    with pytest.raises(handoff.HandoffBusyError):
        await handoff.snapshot_handoff(scope="session-1", handoff_id=identifier)
    with pytest.raises(handoff.HandoffBusyError):
        await call(read=success)
    release.set()
    await task
    entered.clear()
    release.clear()

    async def read(page):
        entered.set()
        await release.wait()
        return await blocked(page)

    task = asyncio.create_task(call(read=read))
    await entered.wait()
    with pytest.raises(handoff.HandoffBusyError):
        await handoff.snapshot_handoff(scope="session-1", handoff_id=identifier)
    release.set()
    await task
    assert len(browser) == 1


@pytest.mark.parametrize("during", [False, True])
async def test_snapshot_rechecks_original_host_policy_before_and_after_capture(browser, monkeypatch, during):
    await call(allowed_hosts=["shop.test", "login.shop.test"])
    identifier = snapshot_id()
    browser[0].url = "https://login.shop.test/challenge"

    async def capture(page):
        page.url = "https://outside.test/private"
        return {"image_data": "must not escape"}

    mock = AsyncMock(side_effect=capture)
    monkeypatch.setattr(chrome_cdp, "capture_owned_viewport", mock)
    if not during:
        browser[0].url = "https://outside.test/private"
    with pytest.raises(chrome_cdp.NavigationPolicyError):
        await handoff.snapshot_handoff(scope="session-1", handoff_id=identifier)
    assert mock.await_count == int(during)
    assert browser[0].closed and not handoff._leases


@pytest.mark.parametrize("stop", ["cancel", "expire"])
async def test_snapshot_cancellation_or_deadline_cleans_exact_page(browser, monkeypatch, stop):
    timeout_contexts = []
    original_timeout_at = asyncio.timeout_at

    def record(when):
        timeout = original_timeout_at(when)
        timeout_contexts.append(timeout)
        return timeout

    monkeypatch.setattr(handoff.asyncio, "timeout_at", record)
    await call()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def capture(page):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(chrome_cdp, "capture_owned_viewport", capture)
    task = asyncio.create_task(handoff.snapshot_handoff(scope="session-1", handoff_id=snapshot_id()))
    await entered.wait()
    if stop == "cancel":
        task.cancel()
        error = asyncio.CancelledError
    else:
        timeout_contexts[0]._on_timeout()
        error = UpstreamTimeoutError
    with pytest.raises(error):
        await asyncio.wait_for(task, timeout=1)
    assert cancelled.is_set()
    assert len(browser) == 1 and browser[0].closed
    assert not handoff._leases


@pytest.mark.parametrize("stop", ["expire", "cancel"])
async def test_termination_settles_queued_snapshot_before_getter_resumes(browser, monkeypatch, stop):
    timeouts = []
    original_timeout_at = asyncio.timeout_at

    def record(when):
        timeout = original_timeout_at(when)
        timeouts.append(timeout)
        return timeout

    monkeypatch.setattr(handoff.asyncio, "timeout_at", record)
    await call()
    lease = next(iter(handoff._leases.values()))
    original_put = lease.requests.put_nowait
    capture = AsyncMock()
    monkeypatch.setattr(chrome_cdp, "capture_owned_viewport", capture)

    def put_at_deadline(request):
        original_put(request)
        if stop == "expire":
            timeouts[0]._on_timeout()
        else:
            lease.task.cancel()

    monkeypatch.setattr(lease.requests, "put_nowait", put_at_deadline)
    with pytest.raises(UpstreamTimeoutError if stop == "expire" else asyncio.CancelledError):
        await asyncio.wait_for(handoff.snapshot_handoff(scope="session-1", handoff_id=snapshot_id()), timeout=1)
    capture.assert_not_called()
    assert lease.requests.empty()
    assert browser[0].closed and not handoff._leases
