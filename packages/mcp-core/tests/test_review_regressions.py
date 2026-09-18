"""Regressions for defects an independent multi-model review found in R3/R4 (2026-09-18)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from compare_connector.vision_policy import resolve_image_delivery
from mcp_core.transport import browser_handoff as handoff
from mcp_core.transport import chrome_cdp
from mcp_core.transport.cdp_budget import NavigationBudget


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


async def stable_blocked(page):
    page.extracted += 1
    return {"challenge": "captcha", "products": ["same"]}


def call(**kwargs):
    return handoff.read_with_handoff(
        **{
            "url": "https://shop.test/search?q=a",
            "wait_ms": 0,
            "scope": "session-1",
            "operation": "search",
            "read": stable_blocked,
            "challenge": lambda payload: payload.get("challenge"),
            **kwargs,
        }
    )


async def test_data_changed_stays_false_when_the_caller_mutates_the_payload(browser):
    first, second = {}, {}
    payload, _ = await call(note_out=first)
    payload["_resume"] = first
    payload["_handoff_id"] = "some-handle"
    _, _ = await call(note_out=second)
    assert first["data_changed"] is None
    assert second["resumed"] is True
    assert second["data_changed"] is False


async def test_the_registry_does_not_retain_the_payload(browser):
    note: dict = {}
    payload, _ = await call(note_out=note)
    lease = next(iter(handoff._leases.values()))
    assert lease.last_digest
    assert not hasattr(lease, "last_payload")
    assert payload["products"] == ["same"]


def test_always_outranks_a_metadata_only_request():
    forced = resolve_image_delivery(policy="always", requested=False)
    permissive = resolve_image_delivery(policy="auto", requested=False)
    assert forced.deliver is True
    assert permissive.deliver is False
    assert forced.deliver != permissive.deliver


def test_a_client_refusal_still_outranks_the_deployment():
    decision = resolve_image_delivery(policy="always", requested=True, client_vision=False)
    assert decision.deliver is False
    assert decision.reason == "client_reports_no_vision"


async def test_our_own_policy_error_does_not_reset_the_hosts_refusal_record():
    budget = NavigationBudget(max_concurrent=2, breaker_threshold=2, breaker_cooldown_s=60.0)
    async with budget.slot("policy.example") as slot:
        slot.refused(403)
    permit = await budget.acquire("policy.example")
    permit.neutral()
    permit.release()
    assert budget.snapshot()["hosts"]["policy.example"]["refusals"] == 1
    async with budget.slot("policy.example") as slot:
        slot.refused(503)
    assert budget.snapshot()["hosts"]["policy.example"]["open"] is True
