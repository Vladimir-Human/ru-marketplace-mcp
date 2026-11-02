"""The source inventory must follow the mode a source is configured in (R11).

The outside-in study found that with ``WB_TRANSPORT=dom`` (and with the default
API mode whose DOM fallback is on) the unified server's ``marketplace_sources``
did not list Wildberries in ``needs_cdp`` — while ``wb_search`` in that mode
went through CDP. An agent that reads the inventory to decide whether it needs
a browser was told the opposite of the truth.

The three questions are now separate: does the source REQUIRE the browser, MAY
it use one, and which route will it take now? These tests answer them for the
three WB modes, and prove that answering them opens no browser.
"""

from __future__ import annotations

import pytest
from marketplace_connector import server
from wb_connector import server as wb


def _wb_mode(monkeypatch: pytest.MonkeyPatch, transport: str, dom_fallback: bool) -> None:
    """Configure the WB connector the way an operator would."""
    monkeypatch.setattr(wb._settings, "transport", transport)
    monkeypatch.setattr(wb._settings, "dom_fallback", dom_fallback)


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch: pytest.MonkeyPatch):
    """The inventory is a static answer: it must not probe or open a browser."""

    def forbidden():
        raise AssertionError("the source inventory must not touch the browser")

    monkeypatch.setattr(server, "_cdp_health", forbidden)


async def test_an_api_only_wb_is_not_listed_as_needing_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control: without a browser route there is nothing to open."""
    _wb_mode(monkeypatch, "api", False)

    result = await server.marketplace_sources()
    capability = result.capabilities["wildberries"]

    assert "wildberries" not in result.needs_cdp
    assert capability["effective_route"] == "api"
    assert capability["requires_cdp"] is False
    assert capability["may_use_cdp"] is False


async def test_a_dom_wb_is_listed_as_needing_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """In DOM mode the browser is the only way in, and the inventory says so."""
    _wb_mode(monkeypatch, "dom", True)

    result = await server.marketplace_sources()
    capability = result.capabilities["wildberries"]

    assert "wildberries" in result.needs_cdp
    assert capability["effective_route"] == "dom"
    assert capability["requires_cdp"] is True
    assert capability["may_use_cdp"] is True


async def test_a_wb_with_the_dom_fallback_is_listed_as_needing_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default API mode may still fall back to the browser.

    This was the study's case: the API hosts refuse the address, the tool goes
    through CDP, and the inventory claimed no browser was involved.
    """
    _wb_mode(monkeypatch, "api", True)

    result = await server.marketplace_sources()
    capability = result.capabilities["wildberries"]

    assert "wildberries" in result.needs_cdp
    assert capability["effective_route"] == "api_then_dom"
    assert capability["requires_cdp"] is False
    assert capability["may_use_cdp"] is True


async def test_the_other_sources_keep_their_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control: the static sources are unchanged."""
    _wb_mode(monkeypatch, "api", False)

    result = await server.marketplace_sources()

    assert result.capabilities["ozon"]["effective_route"] == "http_then_cdp"
    assert result.capabilities["ozon"]["requires_cdp"] is False
    assert result.capabilities["ozon"]["may_use_cdp"] is True
    assert result.capabilities["avito"]["effective_route"] == "cdp"
    assert result.capabilities["avito"]["requires_cdp"] is True
    assert "avito" in result.needs_cdp
    assert "yandex_market" not in result.needs_cdp
