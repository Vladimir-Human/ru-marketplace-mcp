"""Entry point for the Ozon MCP server.

Exposed as the ``ozon-mcp`` console script, so MCP client configs can spawn the
server without knowing where the package lives on disk:

    {"command": "uvx", "args": ["--from", "ozon-connector", "ozon-mcp"]}

stdio is the default transport because that is what MCP clients speak, so the
config above keeps working untouched. Set ``MCP_TRANSPORT=http`` (with optional
``MCP_HTTP_HOST``/``MCP_HTTP_PORT``/``MCP_HTTP_PATH``) to run it over HTTP for
remote deployment instead — see docs/DEPLOYMENT.md. Transport selection lives in
``mcp_core.runtime`` and writes any diagnostics to stderr; nothing here may write
to stdout, which the JSON-RPC stream owns.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the server on the transport selected by the environment (stdio default)."""
    from mcp_core.runtime import run_server

    from ozon_connector.server import mcp

    return run_server(mcp, server_name="ozon")


if __name__ == "__main__":
    sys.exit(main())
