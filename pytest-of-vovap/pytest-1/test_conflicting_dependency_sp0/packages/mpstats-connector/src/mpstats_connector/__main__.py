"""Entry point for the MPStats MCP server.

Exposed as the ``mpstats-mcp`` console script, so MCP client configs can spawn
the server without knowing where the package lives on disk:

    {"command": "uv", "args": ["run", "--directory", "...", "mpstats-mcp"]}

stdio is the default transport (see ``mcp_core.runtime``). Set
``MCP_TRANSPORT=http`` with optional ``MCP_HTTP_HOST``/``MCP_HTTP_PORT``/
``MCP_HTTP_PATH`` to run over HTTP for remote deployment instead. Nothing here
may write to stdout — that stream owns the JSON-RPC protocol.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the server on the transport selected by the environment (stdio default)."""
    from mcp_core.runtime import run_server

    from mpstats_connector.server import mcp

    return run_server(mcp, server_name="mpstats")


if __name__ == "__main__":
    sys.exit(main())
