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
