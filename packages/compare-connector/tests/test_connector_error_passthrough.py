"""R20: a connector error keeps its machine fields through the generic handler.

A busy browser handoff is not a transport outage: it carries 409 and the
delay before the same operation may run again. A generic handler that wrapped
every exception in a fresh transport_down threw those fields away, so the
caller could not tell "wait five seconds" from "the site is down".

The public source tool and the compare adapter must both keep the fields, and
an unexpected failure must stay the redacted transport_down it always was.
"""

from __future__ import annotations

import json

import pytest
from compare_connector import server as compare
from fastmcp import Client
from fastmcp.exceptions import ToolError
from lamoda_connector import server as lamoda
from mcp_core.errors import UpstreamTimeoutError
from mcp_core.transport.browser_handoff import HandoffBusyError
from taobao_connector import server as taobao

# The public search tool of every source that reads through the operator's
# Chrome, with the dependency that raises the error under test.
SOURCES = [
    ("taobao", taobao, "_cdp_render", "taobao_search"),
    ("lamoda", lamoda, "_cdp_render_search", "lamoda_search"),
]

SECRET = "Bearer AUDIT_FAKE_TOKEN_123456"


async def _call_tool(owner, tool_name: str, monkeypatch, dependency: str, error: Exception) -> dict:
    """Call the public tool with the dependency raising ``error``."""

    async def producer(*args, **kwargs):
        raise error

    monkeypatch.setattr(owner, dependency, producer)
    async with Client(owner.mcp) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool(tool_name, {"query": "audit-test"})
    return json.loads(str(caught.value))


@pytest.mark.parametrize(("name", "owner", "dependency", "tool"), SOURCES)
async def test_a_busy_handoff_keeps_its_status_and_retry_delay(monkeypatch, name, owner, dependency, tool):
    """409 and the delay are what the caller acts on: they must survive."""
    payload = await _call_tool(owner, tool, monkeypatch, dependency, HandoffBusyError(retry_after_s=5))

    assert payload["status_code"] == 409
    assert payload["retry_after_s"] == 5
    assert payload["error"] == "transport_down"
    assert payload["retryable"] is True


@pytest.mark.parametrize(("name", "owner", "dependency", "tool"), SOURCES)
async def test_a_full_registry_keeps_its_own_reason(monkeypatch, name, owner, dependency, tool):
    """A full registry is a different case than a busy page, and says so."""
    payload = await _call_tool(
        owner, tool, monkeypatch, dependency, HandoffBusyError(reason="registry_full", retry_after_s=5)
    )

    assert payload["status_code"] == 409
    assert payload["retry_after_s"] == 5
    assert "registry is full" in payload["message"]


@pytest.mark.parametrize(("name", "owner", "dependency", "tool"), SOURCES)
async def test_a_deadline_error_keeps_its_code_and_provider(monkeypatch, name, owner, dependency, tool):
    """A deadline is a timeout, not an outage, and it names its source."""
    payload = await _call_tool(
        owner, tool, monkeypatch, dependency, UpstreamTimeoutError("the read deadline passed", provider=name)
    )

    assert payload["error"] == "timeout"
    assert payload["provider"] == name
    assert payload["retryable"] is True


@pytest.mark.parametrize(("name", "owner", "dependency", "tool"), SOURCES)
async def test_an_unexpected_failure_stays_a_redacted_transport_down(monkeypatch, name, owner, dependency, tool):
    """An unknown failure invents no taxonomy and leaks no credential."""
    payload = await _call_tool(owner, tool, monkeypatch, dependency, RuntimeError(f"boom {SECRET}"))

    assert payload["error"] == "transport_down"
    assert "status_code" not in payload
    assert "retry_after_s" not in payload
    assert "AUDIT_FAKE_TOKEN_123456" not in payload["message"]
    assert "<redacted>" in payload["message"]


@pytest.mark.parametrize(("name", "owner", "dependency", "tool"), SOURCES)
async def test_an_already_serialized_error_is_untouched(monkeypatch, name, owner, dependency, tool):
    """The positive control: a serialized envelope passes through as it is."""

    async def producer(*args, **kwargs):
        raise ToolError(json.dumps(HandoffBusyError(retry_after_s=5).to_dict()))

    monkeypatch.setattr(owner, dependency, producer)
    async with Client(owner.mcp) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool(tool, {"query": "audit-test"})
    payload = json.loads(str(caught.value))

    assert payload["status_code"] == 409
    assert payload["retry_after_s"] == 5


async def test_compare_keeps_the_busy_delay_of_a_source(monkeypatch):
    """Through compare, the machine fields reach the outcome as well."""

    async def producer(*args, **kwargs):
        raise HandoffBusyError(retry_after_s=5)

    monkeypatch.setattr(taobao, "_cdp_render", producer)
    outcome, offers = await compare._run_source("taobao", "audit-test", 5)

    assert offers == []
    assert outcome.error_code == "transport_down"
    assert outcome.retry_after_s == 5
    assert outcome.retryable is True
    assert outcome.status == "blocked"


async def test_compare_reports_a_deadline_as_a_timeout(monkeypatch):
    """A source that ran out of time is a timeout in the comparison too."""

    async def producer(*args, **kwargs):
        raise UpstreamTimeoutError("the read deadline passed", provider="taobao")

    monkeypatch.setattr(taobao, "_cdp_render", producer)
    outcome, _offers = await compare._run_source("taobao", "audit-test", 5)

    assert outcome.error_code == "timeout"
    assert outcome.status == "timeout"
    assert outcome.retryable is True
