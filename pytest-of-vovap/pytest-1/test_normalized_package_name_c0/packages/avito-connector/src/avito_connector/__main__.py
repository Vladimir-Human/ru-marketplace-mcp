"""Entry point for the Avito MCP server.

Exposed as the ``avito-mcp`` console script, so MCP client configs can spawn the
server without knowing where the package lives on disk:

    {"command": "uv", "args": ["run", "--directory", "/path/to/repo", "avito-mcp"]}

stdio is the default transport because that is what MCP clients speak. Set
``MCP_TRANSPORT=http`` (with optional ``MCP_HTTP_HOST``/``MCP_HTTP_PORT``/
``MCP_HTTP_PATH``) to run it over HTTP — see docs/DEPLOYMENT.md. Transport
selection lives in ``mcp_core.runtime`` and writes diagnostics to stderr;
nothing here may write to stdout, which the JSON-RPC stream owns.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the server on the transport selected by the environment (stdio default)."""
    from mcp_core.runtime import run_server

    from avito_connector.server import mcp

    return run_server(mcp, server_name="avito")


if __name__ == "__main__":
    sys.exit(main())
