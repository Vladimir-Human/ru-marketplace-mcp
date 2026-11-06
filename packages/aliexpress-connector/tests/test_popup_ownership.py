"""Fake Playwright coverage for card popup identity and bounded owned cleanup."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aliexpress_connector import server
from fastmcp.exceptions import ToolError

ITEM_URL = "https://aliexpress.ru/item/100501000.html"


@pytest.fixture
def popups(monkeypatch):
    sleep = asyncio.sleep
    state = SimpleNamespace(
        pages=[],
        pending=[],
        early=False,
        cancel_at=None,
        entered=asyncio.Event(),
        close_stalls=False,
        challenge=False,
        late_registration=False,
        listeners=[],
        scans=0,
        move_to=None,
    )

    class Popup:
        def __init__(self, url=ITEM_URL, *, owned=True):
            self.url = url
            self.owned = owned
            self.closed = False
            self.close_calls = 0
            self.close_finished = asyncio.Event()
            self.reads = 0

        async def opener(self):
            return landing if self.owned else None

        async def evaluate(self, _expression):
            self.reads += 1
            if state.cancel_at == "read":
                state.entered.set()
                await asyncio.Event().wait()
            if state.challenge:
                self.url = "https://aliexpress.ru/punish?x5secdata=x"
            if state.move_to and self.owned:
                # The card redirects to another item while it is polled.
                self.url = state.move_to
            return json.dumps({"title": self.url, "price_texts": {"attached": ["100 ₽"]}, "punish": state.challenge})

        async def close(self):
            self.close_calls += 1
            try:
                if state.close_stalls and self is state.owned:
                    state.entered.set()
                    await asyncio.Event().wait()
                self.closed = True
            finally:
                self.close_finished.set()

    class Context:
        @property
        def pages(self):
            state.scans += 1
            if state.late_registration and state.scans == 2:
                # Register immediately after cleanup has taken its empty snapshot.
                asyncio.get_running_loop().call_soon(deliver)
            return state.pages

    class Landing:
        context = Context()

        def on(self, event, callback):
            assert event == "popup"
            state.listeners.append(callback)

        def remove_listener(self, event, callback):
            assert event == "popup"
            state.listeners.remove(callback)

        async def evaluate(self, _expression):
            if (state.early or state.cancel_at == "open") and not state.late_registration:
                deliver()
            if state.cancel_at == "open":
                state.entered.set()
                await asyncio.Event().wait()
            return "opened"

    landing = Landing()
    state.owned = Popup()
    state.operator = Popup(owned=False)
    state.pages = [landing, state.operator]
    state.pending = [state.owned]
    state.make = Popup

    def deliver():
        delivered = tuple(state.pending)
        state.pages.extend(delivered)
        state.pending.clear()
        for candidate in delivered:
            if candidate.owned:
                for callback in tuple(state.listeners):
                    callback(candidate)

    state.deliver = deliver

    async def tick(_seconds):
        deliver()
        if state.cancel_at == "discovery":
            state.entered.set()
            await asyncio.Event().wait()
        await sleep(0)

    @asynccontextmanager
    async def open_page(*_args, **_kwargs):
        yield landing

    monkeypatch.setattr(server, "open_page", open_page)
    monkeypatch.setattr(server, "_polite_wait", AsyncMock())
    monkeypatch.setattr(server, "_cdp_lock", asyncio.Lock())
    monkeypatch.setattr(server.asyncio, "sleep", tick)
    monkeypatch.setattr(server, "_POPUP_CLOSE_TIMEOUT_S", 0.02, raising=False)
    return state


@pytest.mark.parametrize("suffix", [".html", "", "?sku=1"])
async def test_card_popup_matches_full_item_id(popups, suffix):
    wrong = popups.make("https://aliexpress.ru/item/1005010001.html")
    popups.owned.url = "https://aliexpress.ru/item/100501000" + suffix
    popups.pending.insert(0, wrong)
    result = await server._cdp_card(ITEM_URL, None)
    assert result["title"] == popups.owned.url
    assert wrong.reads == 0 and wrong.close_calls == 0
    assert popups.owned.closed


async def test_card_popup_arriving_before_open_returns_is_owned(popups):
    popups.early = True
    result = await server._cdp_card(ITEM_URL, None)
    assert result["title"] == ITEM_URL
    assert popups.owned.closed
    assert popups.operator.close_calls == 0


async def test_card_cleanup_leaves_other_process_and_operator_tabs(popups):
    foreign = popups.make("https://aliexpress.ru/item/999.html", owned=False)
    same_item = popups.make(ITEM_URL, owned=False)
    unknown_blank = popups.make("about:blank", owned=False)
    popups.pending = [same_item, popups.owned, foreign, unknown_blank]
    await server._cdp_card(ITEM_URL, None)
    assert popups.owned.close_calls == 1
    for page in (popups.operator, foreign, same_item, unknown_blank):
        assert page.reads == 0 and page.close_calls == 0


@pytest.mark.parametrize("url", ["about:blank", "https://aliexpress.ru/punish?x5secdata=x"])
async def test_card_cleans_only_positively_owned_blank_or_challenge_popup(popups, url):
    popups.owned.url = url
    unrelated = popups.make(url, owned=False)
    popups.pending.append(unrelated)
    with pytest.raises(ToolError):
        await server._cdp_card(ITEM_URL, None)
    assert popups.owned.closed
    assert unrelated.close_calls == 0
    assert popups.operator.close_calls == 0


@pytest.mark.parametrize("stage", ["open", "discovery", "read"])
async def test_card_cancellation_cleans_owned_popup(popups, stage):
    popups.cancel_at = stage
    task = asyncio.create_task(server._cdp_card(ITEM_URL, None))
    await asyncio.wait_for(popups.entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert popups.owned.closed
    assert popups.operator.close_calls == 0
    assert not server._cdp_lock.locked()


async def test_card_matched_popup_challenge_redirect_keeps_owned_cleanup(popups, monkeypatch):
    popups.challenge = True
    monkeypatch.setattr(server._pacer, "record_refusal", lambda: None)
    with pytest.raises(ToolError):
        await server._cdp_card(ITEM_URL, None)
    assert popups.owned.closed and popups.owned.close_calls == 1
    assert popups.operator.close_calls == 0


async def test_card_popup_challenge_is_json_with_the_challenge_contract(popups, monkeypatch):
    """The x5sec verdict must reach the agent through the public card tool as the
    shared JSON challenge error, not a bare string: before the fix the challenge
    arrived with no error code and nothing telling the agent that a plain retry
    cannot pass it and a human must act (R-CHALLENGE-ACTION)."""
    popups.challenge = True
    monkeypatch.setattr(server._pacer, "record_refusal", lambda: None)

    with pytest.raises(ToolError) as excinfo:
        await server.aliexpress_card(ITEM_URL)

    error = json.loads(str(excinfo.value))
    assert error["error"] == "challenge_required"
    assert error["requires_user_action"] is True
    assert error["retryable_now"] is False
    assert error["status_code"] == 403
    assert "aliexpress.ru" in error["user_action"]
    assert "unblock aliexpress" not in error["user_action"]
    assert "challenge" in error["message"]
    assert popups.owned.closed


async def test_card_repeated_cancellation_waits_for_bounded_owned_cleanup(popups):
    popups.close_stalls = True
    task = asyncio.create_task(server._cdp_card(ITEM_URL, None))
    await asyncio.wait_for(popups.entered.wait(), timeout=1)
    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    assert not popups.owned.close_finished.is_set()
    assert server._cdp_lock.locked()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert popups.owned.close_calls == 1 and popups.owned.close_finished.is_set()
    assert popups.operator.close_calls == 0
    assert not server._cdp_lock.locked()


async def test_card_cancellation_cleans_popup_registered_after_open_cancel(popups):
    popups.cancel_at = "open"
    popups.late_registration = True
    task = asyncio.create_task(server._cdp_card(ITEM_URL, None))
    await asyncio.wait_for(popups.entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    assert popups.owned.closed
    assert popups.operator.close_calls == 0
    assert not popups.listeners and not server._cdp_lock.locked()


async def test_card_early_redirect_retains_popup_ownership(popups):
    popups.early = True
    popups.owned.url = "https://aliexpress.ru/challenge/login"
    with pytest.raises(ToolError):
        await server._cdp_card(ITEM_URL, None)
    assert popups.owned.closed
    assert popups.operator.close_calls == 0
    assert not popups.listeners


async def test_card_cancelled_cleanup_task_does_not_spin(popups, monkeypatch):
    shield = asyncio.shield
    calls = 0

    def guarded_shield(task):
        nonlocal calls
        calls += 1
        assert calls < 10, "completed child cancellation must not be retried forever"
        return shield(task)

    monkeypatch.setattr(server.asyncio, "shield", guarded_shield)
    monkeypatch.setattr(popups.owned, "close", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(server._cdp_card(ITEM_URL, None), timeout=1)
    assert not server._cdp_lock.locked()
    assert not popups.listeners


async def test_card_popup_close_is_bounded_and_releases_lock(popups):
    popups.close_stalls = True
    result = await asyncio.wait_for(server._cdp_card(ITEM_URL, None), timeout=0.5)
    assert result["title"] == ITEM_URL
    assert popups.owned.close_calls == 1
    assert popups.owned.close_finished.is_set()
    assert popups.operator.close_calls == 0
    assert not server._cdp_lock.locked()


class _RawLanding:
    """A raw-CDP landing page: no Playwright context, no popup events."""

    url = "https://aliexpress.ru/wholesale?SearchText=x"


class _RawPopup:
    def __init__(self, url):
        self.url = url
        self.reads = 0
        self.move_to = None
        self.report_url = False

    async def evaluate(self, _expression):
        self.reads += 1
        if self.move_to:
            self.url = self.move_to
        payload = {"title": self.url, "price_texts": {"attached": ["100 ₽"]}, "punish": False}
        if self.report_url:
            payload["url"] = self.url
        return json.dumps(payload)


@pytest.fixture
def raw_popup(monkeypatch):
    state = SimpleNamespace(popup=_RawPopup(ITEM_URL), calls=[], exited=False)
    landing = _RawLanding()

    @asynccontextmanager
    async def open_page(*_args, **_kwargs):
        yield landing

    @asynccontextmanager
    async def open_owned_popup(opener, url, *, allowed_hosts):
        state.calls.append((opener, url, set(allowed_hosts)))
        try:
            yield state.popup
        finally:
            state.exited = True

    monkeypatch.setattr(server, "open_page", open_page)
    # raising=False: the pre-fix module does not import open_owned_popup at all.
    monkeypatch.setattr(server, "open_owned_popup", open_owned_popup, raising=False)
    monkeypatch.setattr(server, "_polite_wait", AsyncMock())
    monkeypatch.setattr(server, "_cdp_lock", asyncio.Lock())
    state.landing = landing
    return state


async def test_card_reads_an_owned_raw_cdp_popup(raw_popup):
    """Raw CDP (Playwright attach timed out) used to refuse every card with
    transport_down; the card now opens the item from the landing page as an
    owned raw popup and reads it like the Playwright tab."""
    result = await server._cdp_card(ITEM_URL, None)

    assert result["title"] == ITEM_URL
    assert raw_popup.calls == [(raw_popup.landing, ITEM_URL, {"aliexpress.ru", "www.aliexpress.ru"})]
    assert raw_popup.popup.reads == 1
    assert raw_popup.exited
    assert not server._cdp_lock.locked()


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://aliexpress.ru/item/1005010001.html", "transport_down"),
        ("https://aliexpress.ru/punish?x5secdata=x", "challenge_required"),
    ],
)
async def test_raw_popup_on_another_item_or_a_challenge_is_not_read(raw_popup, monkeypatch, url, code):
    raw_popup.popup = _RawPopup(url)
    monkeypatch.setattr(server._pacer, "record_refusal", lambda: None)

    with pytest.raises(ToolError) as excinfo:
        await server._cdp_card(ITEM_URL, None)

    assert json.loads(str(excinfo.value))["error"] == code
    assert raw_popup.popup.reads == 0
    assert raw_popup.exited


OTHER_ITEM = "https://aliexpress.ru/item/1005010001.html"


@pytest.mark.parametrize("report_url", [False, True])
async def test_raw_popup_redirected_to_another_item_during_the_read_is_not_returned(raw_popup, report_url):
    """Review #195 P1: the item id was checked only before the 12 s poll; an A->B
    redirect on the same host returned (and cached) item B's price under item A."""
    raw_popup.popup.move_to = OTHER_ITEM
    raw_popup.popup.report_url = report_url

    with pytest.raises(ToolError) as excinfo:
        await server._cdp_card(ITEM_URL, None)

    assert json.loads(str(excinfo.value))["error"] == "transport_down"
    assert raw_popup.popup.reads >= 1
    assert raw_popup.exited


async def test_raw_popup_extractor_url_on_another_item_is_not_returned(raw_popup, monkeypatch):
    """The extractor's own location.href is checked too, not only the tab URL."""

    async def stale_tab_url(_target):
        return ITEM_URL

    monkeypatch.setattr(server, "current_page_url", stale_tab_url, raising=False)
    raw_popup.popup.move_to = OTHER_ITEM
    raw_popup.popup.report_url = True

    with pytest.raises(ToolError) as excinfo:
        await server._cdp_card(ITEM_URL, None)

    assert json.loads(str(excinfo.value))["error"] == "transport_down"


async def test_raw_popup_moving_to_a_challenge_during_the_read_is_challenge_required(raw_popup, monkeypatch):
    raw_popup.popup.move_to = "https://aliexpress.ru/punish?x5secdata=x"
    monkeypatch.setattr(server._pacer, "record_refusal", lambda: None)

    with pytest.raises(ToolError) as excinfo:
        await server._cdp_card(ITEM_URL, None)

    assert json.loads(str(excinfo.value))["error"] == "challenge_required"


async def test_playwright_popup_redirected_to_another_item_during_the_read_is_not_returned(popups):
    """The same A->B check covers the Playwright popup path."""
    popups.move_to = OTHER_ITEM

    with pytest.raises(ToolError) as excinfo:
        await server._cdp_card(ITEM_URL, None)

    assert json.loads(str(excinfo.value))["error"] == "transport_down"
    assert popups.owned.closed


async def test_card_tool_caches_nothing_when_the_item_moved(raw_popup, monkeypatch):
    """Nothing read from a moved tab reaches the cache."""
    server._cache.clear()
    raw_popup.popup.move_to = OTHER_ITEM

    with pytest.raises(ToolError):
        await server.aliexpress_card(ITEM_URL)

    assert server._cache.get(ITEM_URL) is None
