"""Focused edge cases for the one shared outgoing error boundary."""
# START_MODULE_CONTRACT
# PURPOSE: Prove additive error recovery, parameter naming, redaction and installation.
# SCOPE: Real in-memory MCP calls, fake local tool bodies and loaded module pacing state; no network.
# DEPENDS: pytest, FastMCP, mcp_core.errors/runtime/resources/sources.
# LINKS: M-ERRORS; M-RUNTIME; M-SOURCES; M-RESOURCES; R-ERROR-NEXT-STEP.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_installation_is_shared_and_idempotent: Install the same boundary only once.
# test_schema_names_bad_url_parameter: Name the actual URL parameter rather than guessing from values.
# test_metadata_and_new_guidance_are_redacted: Preserve taxonomy/retry fields and redact new strings.
# test_non_connector_json_stays_non_connector: Do not classify arbitrary SDK JSON as a connector error.
# test_a_standalone_server_names_no_tool_it_does_not_list: Never send the agent to a tool the server lacks.
# test_bad_request_without_a_named_parameter_guesses_none: No parameter named when the message names none.
# test_error_reference_describes_outgoing_fields: Publish the new fields and generated code guidance.
# END_MODULE_MAP

from __future__ import annotations

import json

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp_core import runtime
from mcp_core.errors import BadRequestError, ConnectorError, ErrorCode, PermissionDeniedError, raise_tool_error
from mcp_core.resources import render_error_reference

# START_BLOCK_BOUNDARY_EDGE_CASES


@pytest.mark.req("R-ERROR-NEXT-STEP.boundary")
def test_installation_is_shared_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FastMCP("install-probe")
    runtime.install_tool_error_middleware(server)
    monkeypatch.setattr(server, "run", lambda **kwargs: None)
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    assert runtime.run_server(server, server_name="probe") == 0
    assert sum(isinstance(item, runtime.ToolErrorRedactionMiddleware) for item in server.middleware) == 1


@pytest.mark.req("R-ERROR-NEXT-STEP.guidance")
async def test_schema_names_bad_url_parameter() -> None:
    server = FastMCP("parameter-probe")

    @server.tool
    def probe(item_id_or_url: str) -> str:
        raise_tool_error(BadRequestError("invalid item URL"))

    runtime.install_tool_error_middleware(server)
    async with Client(server) as client:
        result = await client.call_tool("probe", {"item_id_or_url": "bad"}, raise_on_error=False)
    error = json.loads(result.content[0].text)
    assert error["next_step"] == "Correct the item_id_or_url parameter using `probe`'s schema, then call `probe` again."
    assert error["error"] == "bad_request"


@pytest.mark.req("R-ERROR-NEXT-STEP.boundary", "R-ERROR-NEXT-STEP.guidance")
@pytest.mark.parametrize("detail", ["Private catalog refused", "Private catalog refused " + "x" * 450])
async def test_metadata_and_new_guidance_are_redacted(monkeypatch: pytest.MonkeyPatch, detail: str) -> None:
    monkeypatch.setenv("MCP_HTTP_AUTH_TOKEN", "secret-fixture-token")
    server = FastMCP("redaction-probe")

    @server.tool
    def denied() -> str:
        raise_tool_error(PermissionDeniedError(f"{detail}. Bearer secret-fixture-token is not allowed."))

    @server.tool
    def paused() -> str:
        error = ConnectorError(ErrorCode.TRANSPORT_DOWN, "busy", provider="chrome", retry_after_s=17.5, status_code=409)
        payload = error.to_dict()
        payload["retryable_now"] = False
        raise ToolError(json.dumps(payload))

    runtime.install_tool_error_middleware(server)
    async with Client(server) as client:
        denied_error = json.loads((await client.call_tool("denied", {}, raise_on_error=False)).content[0].text)
        paused_error = json.loads((await client.call_tool("paused", {}, raise_on_error=False)).content[0].text)
    assert "secret-fixture-token" not in json.dumps(denied_error)
    assert "Private catalog refused" in denied_error["next_step"]
    assert "retry will not help" in denied_error["next_step"]
    assert denied_error["next_step"].endswith(".")
    assert "tool" in denied_error
    assert {key: value for key, value in paused_error.items() if key not in {"tool", "next_step", "old_build"}} == {
        "error": "transport_down",
        "message": "busy",
        "retryable": True,
        "provider": "chrome",
        "retry_after_s": 17.5,
        "status_code": 409,
        "retryable_now": False,
    }


@pytest.mark.req("R-ERROR-NEXT-STEP.boundary")
@pytest.mark.parametrize(
    "original",
    ['{"error": "unknown_sdk_error", "message": "unchanged"}', '{"error": ["sdk_error"], "message": "unchanged"}'],
)
async def test_non_connector_json_stays_non_connector(original: str) -> None:
    server = FastMCP("sdk-error-probe")

    @server.tool
    def fail() -> str:
        raise ToolError(original)

    runtime.install_tool_error_middleware(server)
    async with Client(server) as client:
        result = await client.call_tool("fail", {}, raise_on_error=False)
    assert result.content[0].text == original


@pytest.mark.req("R-ERROR-NEXT-STEP.guidance")
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ErrorCode.RATE_LIMITED, "Do not retry `citilink_card` now; tell the user this source is limited."),
        (ErrorCode.PARSER_DRIFT, "Do not retry; this source is unavailable until a fix."),
        (ErrorCode.NOT_FOUND, "Check the id or URL; this item is not available from this source."),
    ],
)
async def test_a_standalone_server_names_no_tool_it_does_not_list(code: ErrorCode, expected: str) -> None:
    """A one-source server has no compare_prices, marketplace_health or (here) search tool to send the agent to."""
    server = FastMCP("standalone-probe")

    @server.tool
    def citilink_card(url: str) -> str:
        raise_tool_error(ConnectorError(code, "fixture failure", provider="citilink", retry_after_s=30))

    runtime.install_tool_error_middleware(server)
    async with Client(server) as client:
        result = await client.call_tool("citilink_card", {"url": "x"}, raise_on_error=False)
    error = json.loads(result.content[0].text)
    assert error["next_step"] == expected
    assert error["tool"] == "citilink_card"


@pytest.mark.req("R-ERROR-NEXT-STEP.guidance")
async def test_bad_request_without_a_named_parameter_guesses_none() -> None:
    server = FastMCP("no-guess-probe")

    @server.tool
    def probe(item_id_or_url: str, limit: int = 10) -> str:
        raise_tool_error(BadRequestError("too many filters"))

    runtime.install_tool_error_middleware(server)
    async with Client(server) as client:
        result = await client.call_tool("probe", {"item_id_or_url": "x"}, raise_on_error=False)
    error = json.loads(result.content[0].text)
    assert error["next_step"] == (
        "Correct the arguments the message describes, using `probe`'s schema, then call `probe` again."
    )


# END_BLOCK_BOUNDARY_EDGE_CASES
# START_BLOCK_ERROR_REFERENCE


@pytest.mark.req("R-ERROR-NEXT-STEP.reference")
def test_error_reference_describes_outgoing_fields() -> None:
    reference = render_error_reference()
    assert "tool, next_step" in reference
    assert "always present" in reference and "pacing floor" in reference
    assert "MPSTATS_MP_AUTH" in reference
    assert "detmir_category" in reference
    for code in ErrorCode:
        assert f"`{code.value}`" in reference
    assert "next_step example" in reference


# END_BLOCK_ERROR_REFERENCE
