"""The redaction boundary ``run_server`` installs on every transport.

FastMCP answers any non-FastMCP exception a tool raises with
``ToolError(f"Error calling tool {name!r}: {e}")`` built from the raw exception
text. A proxy URL or a token in that text would reach the client unredacted,
because such an exception never passes through ``raise_tool_error``. The
middleware is the one place that catches it — and it must leave an error this
project raised itself, which is already redacted JSON, byte-identical.

Everything here is offline and in-memory: a real FastMCP server plus the
in-memory client, and a recording stand-in for the transport launch.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp_core import runtime
from mcp_core.errors import ChallengeRequiredError, raise_tool_error

TOKEN = "s3cret-auth-token"


def _probe_server() -> FastMCP:
    """A server whose tools fail the two ways the boundary must tell apart."""
    mcp = FastMCP("redaction-probe")

    @mcp.tool
    def unexpected() -> str:
        raise ValueError(f"proxy htp://user:{TOKEN}@proxy.test refused")

    @mcp.tool
    def challenged() -> str:
        raise_tool_error(ChallengeRequiredError("challenge wall", provider="avito.ru"))

    return mcp


# --- through the client, the way production serves a call -------------------


async def test_unexpected_exception_reaches_the_client_redacted(monkeypatch):
    """The raw ValueError carries both the env-var secret and a proxy URL; the
    agent must not see the credential, and the host stays readable."""
    monkeypatch.setenv(runtime.ENV_HTTP_AUTH_TOKEN, TOKEN)
    mcp = _probe_server()
    mcp.add_middleware(runtime.ToolErrorRedactionMiddleware())

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool("unexpected", {})

    text = str(excinfo.value)
    assert TOKEN not in text
    assert "<redacted>" in text
    assert "proxy.test" in text


async def test_json_tool_error_from_raise_tool_error_passes_through(monkeypatch):
    """An error the project raised itself is already redacted JSON, so the
    boundary must hand it to the agent byte-identical."""
    monkeypatch.setenv(runtime.ENV_HTTP_AUTH_TOKEN, TOKEN)
    mcp = _probe_server()
    mcp.add_middleware(runtime.ToolErrorRedactionMiddleware())

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool("challenged", {})

    error = json.loads(str(excinfo.value))
    assert error["error"] == "challenge_required"
    assert error["requires_user_action"] is True
    assert error["retryable_now"] is False
    assert error["message"] == "challenge wall"

    with pytest.raises(ToolError) as expected:
        raise_tool_error(ChallengeRequiredError("challenge wall", provider="avito.ru"))
    assert str(excinfo.value) == str(expected.value)


# --- the middleware contract on its own -------------------------------------


async def test_middleware_redacts_and_keeps_the_original_as_cause():
    middleware = runtime.ToolErrorRedactionMiddleware()
    original = ToolError("Bearer abc.def.ghi was refused")

    async def call_next(context):
        raise original

    with pytest.raises(ToolError) as excinfo:
        await middleware.on_call_tool(None, call_next)

    assert "abc.def.ghi" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)
    assert excinfo.value.__cause__ is original


async def test_middleware_never_cuts_a_text_the_redaction_grew(monkeypatch):
    """A secret shorter than "<redacted>" makes the text longer; bounding the
    result by the original length would cut the tail the agent needs."""
    monkeypatch.setenv(runtime.ENV_HTTP_AUTH_TOKEN, "s3cr3t")
    middleware = runtime.ToolErrorRedactionMiddleware()

    async def call_next(context):
        raise ToolError("proxy refused s3cr3t; check HTTPS_PROXY tail-marker")

    with pytest.raises(ToolError) as excinfo:
        await middleware.on_call_tool(None, call_next)

    assert str(excinfo.value) == "proxy refused <redacted>; check HTTPS_PROXY tail-marker"


async def test_middleware_passes_clean_errors_and_other_exceptions_through():
    middleware = runtime.ToolErrorRedactionMiddleware()
    clean = ToolError("nothing secret here")

    async def clean_next(context):
        raise clean

    with pytest.raises(ToolError) as excinfo:
        await middleware.on_call_tool(None, clean_next)

    # The same object, not a copy: the boundary is a no-op when the text is.
    assert excinfo.value is clean

    class Boom(RuntimeError):
        pass

    boom = Boom("not a ToolError")

    async def boom_next(context):
        raise boom

    with pytest.raises(Boom) as excinfo:
        await middleware.on_call_tool(None, boom_next)

    assert excinfo.value is boom


# --- run_server installs it on every transport ------------------------------


class _RecordingMCP:
    def __init__(self) -> None:
        self.middlewares: list[object] = []
        self.runs: list[dict] = []

    def add_middleware(self, middleware) -> None:
        self.middlewares.append(middleware)

    def run(self, **kwargs) -> None:
        self.runs.append(kwargs)


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_run_server_installs_the_redaction_boundary(monkeypatch, transport):
    monkeypatch.setenv(runtime.ENV_TRANSPORT, transport)
    monkeypatch.setenv(runtime.ENV_HTTP_HOST, "127.0.0.1")
    monkeypatch.delenv(runtime.ENV_HTTP_AUTH_TOKEN, raising=False)
    monkeypatch.delenv(runtime.ENV_HTTP_TENANT_ID, raising=False)
    server = _RecordingMCP()

    assert runtime.run_server(server, server_name="probe") == 0

    assert any(isinstance(middleware, runtime.ToolErrorRedactionMiddleware) for middleware in server.middlewares)
    assert server.runs
