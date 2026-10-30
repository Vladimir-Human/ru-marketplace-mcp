"""A real loopback HTTP server started by ``run_server``, probed in both protocol eras.

FastMCP 4 moved request dispatch into the MCP SDK's middleware layer and added
the sessionless 2026-07-28 era. Two things must survive that, and only a real
server shows them: the bearer token still refuses every unauthenticated call,
in either era, and the client scope over HTTP is the session on handshake-era
connections and absent on sessionless ones (nothing names the caller between
requests, so no browser tab may be kept for it). Loopback only; no network.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import McpError, ToolError
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

TOKEN = "loopback-test-token"

_SERVER = textwrap.dedent(
    """
    from fastmcp import Context, FastMCP
    from mcp_core.runtime import current_client_scope, run_server

    mcp = FastMCP("http-probe")

    @mcp.tool
    def scope(ctx: Context) -> dict:
        return {"scope": current_client_scope(ctx), "transport": ctx.transport}

    raise SystemExit(run_server(mcp, server_name="http-probe"))
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def http_url(tmp_path_factory):
    folder = tmp_path_factory.mktemp("http-probe")
    script = folder / "server.py"
    script.write_text(_SERVER, encoding="utf-8")
    # A file, not a pipe: nobody drains a pipe while tests run, and a full one
    # blocks the server's stderr logging and with it every response.
    log = (folder / "server.log").open("wb")
    port = _free_port()
    env = dict(os.environ, MCP_TRANSPORT="http", MCP_HTTP_PORT=str(port), MCP_HTTP_AUTH_TOKEN=TOKEN)
    env.pop("MCP_HTTP_HOST", None)
    env.pop("MCP_HTTP_TENANT_ID", None)
    proc = subprocess.Popen([sys.executable, str(script)], env=env, stdout=subprocess.DEVNULL, stderr=log)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    log.flush()
                    stderr = (folder / "server.log").read_text(encoding="utf-8", errors="replace")
                    pytest.fail(f"loopback server did not start: {stderr[-2000:]}")
                time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _client(url: str, mode: str, token: str | None) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return Client(StreamableHttpTransport(url, headers=headers), mode=mode, timeout=30)


@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_bearer_token_refuses_every_era_without_the_token(http_url, mode, token):
    with pytest.raises(McpError, match="HTTP bearer authentication required"):
        async with _client(http_url, mode, token) as client:
            await client.call_tool("scope", {})


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_bearer_token_is_checked_on_every_request_not_only_at_connect(http_url, mode):
    """An authenticated connection whose later requests lose the token is refused.

    Guards against a middleware that only checks initialize/discover: here the
    connection opens with the token, then the same client sends a call without it.
    """
    drop_token = False

    async def strip_authorization(request: httpx2.Request) -> None:
        if drop_token:
            request.headers.pop("Authorization", None)

    def factory(**kwargs) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(event_hooks={"request": [strip_authorization]}, **kwargs)

    transport = StreamableHttpTransport(
        http_url, headers={"Authorization": f"Bearer {TOKEN}"}, httpx_client_factory=factory
    )
    async with Client(transport, mode=mode, timeout=30) as client:
        assert (await client.call_tool("scope", {})).structured_content is not None
        drop_token = True
        # A refused tools/call comes back as a tool error, raised before the tool runs.
        with pytest.raises((ToolError, McpError), match="HTTP bearer authentication required"):
            await client.call_tool("scope", {})


async def test_handshake_http_client_is_scoped_to_its_session(http_url):
    async with _client(http_url, "legacy", TOKEN) as client:
        assert client.protocol_version not in MODERN_PROTOCOL_VERSIONS
        first = (await client.call_tool("scope", {})).structured_content
        second = (await client.call_tool("scope", {})).structured_content
    assert first is not None and second is not None
    assert first["transport"] == "streamable-http"
    assert first["scope"] and first["scope"] == second["scope"]


async def test_sessionless_http_client_has_no_scope(http_url):
    async with _client(http_url, "auto", TOKEN) as client:
        assert client.protocol_version in MODERN_PROTOCOL_VERSIONS
        first = (await client.call_tool("scope", {})).structured_content
        second = (await client.call_tool("scope", {})).structured_content
    assert first == second == {"scope": None, "transport": "streamable-http"}
