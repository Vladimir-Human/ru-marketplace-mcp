"""R3 (2026-09-18): a resumed read must say what changed, and a refusal must say why.

The old behaviour returned a payload and an expiry and left the caller to guess
whether the retained page had been resumed, whether the challenge was gone, and
whether the page had moved on. The old busy error said only "busy". These tests
pin the explanation instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp_core.errors import NotFoundError
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


async def blocked(page):
    page.extracted += 1
    return {"challenge": "captcha"}


async def blocked_stable(page):
    """Still challenged, same data every time — the 'nothing changed' case."""
    page.extracted += 1
    return {"challenge": "captcha", "products": ["same"]}


async def blocked_then_clear(page):
    """Still challenged on the first read, cleared afterwards — the resume that worked."""
    page.extracted += 1
    if page.extracted == 1:
        return {"challenge": "captcha", "products": ["before"]}
    return {"products": ["after"]}


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


# ------------------------------------------------------------- what changed ----


async def test_a_first_read_says_it_resumed_nothing_and_changed_nothing_unknown(browser):
    note: dict = {}

    await call(note_out=note)

    assert note["resumed"] is False
    assert note["reads"] == 1
    assert note["challenge"] == "still_required"
    assert note["challenge_cleared"] is False
    assert note["data_changed"] is None, "nothing to compare against is not 'nothing changed'"
    assert "opened a new page" in note["summary"]


async def test_a_resumed_read_with_identical_data_says_nothing_changed(browser):
    """A lease only survives while the challenge does, so resume means still challenged."""
    first: dict = {}
    second: dict = {}

    await call(read=blocked_stable, note_out=first)
    await call(read=blocked_stable, note_out=second)

    assert first["resumed"] is False
    assert second["resumed"] is True
    assert second["reads"] == 2
    assert second["challenge"] == "still_required"
    assert second["data_changed"] is False
    assert "still present" in second["summary"]


async def test_the_resume_that_clears_the_challenge_reports_both_facts(browser):
    """The interesting case: same owned page, challenge gone, data moved on."""
    first: dict = {}
    second: dict = {}

    await call(read=blocked_then_clear, note_out=first)
    await call(read=blocked_then_clear, note_out=second)

    assert second["resumed"] is True
    assert second["challenge_cleared"] is True
    assert second["data_changed"] is True
    assert "the challenge is cleared and the data changed" in second["summary"]


async def test_the_note_is_optional(browser):
    """Existing callers keep their two-tuple; nothing is forced on them."""
    payload, expiry = await call()

    assert payload["challenge"] == "captcha"
    assert expiry is not None


# ----------------------------------------------------------- why it refused ----


async def test_a_busy_page_explains_itself_and_hints_when_to_retry(browser):
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending(page):
        started.set()
        await release.wait()
        return {"challenge": "captcha"}

    task = asyncio.create_task(call(read=pending))
    await started.wait()
    with pytest.raises(handoff.HandoffBusyError) as caught:
        await call()
    release.set()
    await task

    error = caught.value
    assert error.reason == "busy"
    assert "another operation is reading" in str(error)
    assert error.status_code == 409
    assert error.retry_after_s == pytest.approx(5.0)
    assert error.to_dict()["retry_after_s"] == pytest.approx(5.0)


async def test_a_full_registry_names_that_reason_instead_of_merely_busy(browser):
    for index in range(handoff._max_leases()):
        await call(scope=f"session-{index}")

    with pytest.raises(handoff.HandoffBusyError) as caught:
        await call(scope="overflow")

    assert caught.value.reason == "registry_full"
    assert "registry is full" in str(caught.value)


async def test_an_unknown_handle_stays_opaque_but_an_expired_one_explains(browser):
    """Explaining a caller's own expired handle leaks nothing; a foreign one stays opaque."""
    with pytest.raises(NotFoundError) as unknown:
        await handoff.snapshot_handoff(scope="session-1", handoff_id="unknown-handle-123456")
    assert "unavailable" in str(unknown.value)

    await call(read=blocked)
    lease = next(iter(handoff._leases.values()))
    handle = lease.handoff_id
    lease.deadline = 0.0  # expire the caller's own lease

    with pytest.raises(NotFoundError) as expired:
        await handoff.snapshot_handoff(scope="session-1", handoff_id=handle)

    message = str(expired.value)
    assert "expired" in message
    assert "run the operation again" in message
