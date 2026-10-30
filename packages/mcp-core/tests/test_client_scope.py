"""``current_client_scope``: which client a tool call belongs to, per protocol era.

Browser challenge tabs are kept for the client that hit the challenge, so a
retry from that client resumes the same page and nobody else can reach it.
FastMCP 4 serves two eras. A handshake-era connection has a session; a
2026-07-28 connection has none and gets a new ``session_id`` on every request.
These tests pin the scope each era yields, the stdio ones over a real child
process speaking stdio, the way MCP clients launch these servers.
"""

from __future__ import annotations

import sys
import textwrap

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StdioTransport
from mcp_core.runtime import STDIO_CLIENT_SCOPE, current_client_scope
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

MODERN = MODERN_PROTOCOL_VERSIONS[-1]

_SERVER = textwrap.dedent(
    """
    from fastmcp import Context, FastMCP
    from mcp_core.runtime import current_client_scope

    mcp = FastMCP("scope-probe")

    @mcp.tool
    def scope(ctx: Context) -> dict:
        return {"scope": current_client_scope(ctx), "session_id": ctx.session_id}

    if __name__ == "__main__":
        mcp.run(transport="stdio", show_banner=False)
    """
)


async def _two_calls(client: Client) -> tuple[dict, dict]:
    first = (await client.call_tool("scope", {})).structured_content
    second = (await client.call_tool("scope", {})).structured_content
    assert first is not None and second is not None
    return first, second


@pytest.fixture
def stdio_server(tmp_path):
    script = tmp_path / "scope_server.py"
    script.write_text(_SERVER, encoding="utf-8")
    return lambda: StdioTransport(command=sys.executable, args=[str(script)], keep_alive=False)


async def test_stdio_handshake_client_is_scoped_to_its_stable_session(stdio_server):
    async with Client(stdio_server(), mode="legacy", timeout=60) as client:
        assert client.protocol_version not in MODERN_PROTOCOL_VERSIONS
        first, second = await _two_calls(client)
    assert first["scope"] == first["session_id"] == second["scope"] == second["session_id"]
    assert first["scope"] != STDIO_CLIENT_SCOPE


async def test_stdio_sessionless_client_is_scoped_to_the_process(stdio_server):
    async with Client(stdio_server(), mode="auto", timeout=60) as client:
        assert client.protocol_version == MODERN
        first, second = await _two_calls(client)
    # FastMCP mints a new session_id per sessionless request; the scope must not follow it.
    assert first["session_id"] != second["session_id"]
    assert first["scope"] == second["scope"] == STDIO_CLIENT_SCOPE


def _in_memory_server() -> FastMCP:
    mcp = FastMCP("scope-probe")

    @mcp.tool
    def scope(ctx: Context) -> dict:
        return {"scope": current_client_scope(ctx), "session_id": ctx.session_id}

    return mcp


async def test_in_memory_handshake_clients_get_distinct_stable_scopes():
    mcp = _in_memory_server()
    async with Client(mcp, mode="legacy") as one, Client(mcp, mode="legacy") as other:
        a1, a2 = await _two_calls(one)
        b1, _ = await _two_calls(other)
    assert a1["scope"] == a2["scope"] and a1["scope"] != b1["scope"]


@pytest.mark.parametrize("transport", [None, "streamable-http", "sse"])
async def test_sessionless_call_without_one_client_transport_has_no_scope(monkeypatch, transport):
    """Over HTTP nothing names a 2026-07-28 caller between requests: no scope."""
    monkeypatch.setattr(Context, "transport", property(lambda self: transport))
    async with Client(_in_memory_server(), mode=MODERN) as client:
        first, second = await _two_calls(client)
    assert first["scope"] is None and second["scope"] is None


def test_outside_a_request_there_is_no_scope():
    """CLI probes (marketplace-mcp doctor) call tool code with no MCP request."""
    assert current_client_scope() is None
