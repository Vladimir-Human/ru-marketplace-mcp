"""Entry point for the Taobao MCP server.

Exposed as the ``taobao-mcp`` console script. stdio is the default transport;
set ``MCP_TRANSPORT=http`` for HTTP — see docs/DEPLOYMENT.md. Diagnostics go to
stderr; nothing here may write to stdout, which the JSON-RPC stream owns.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the server on the transport selected by the environment (stdio default)."""
    from mcp_core.runtime import run_server

    from taobao_connector.server import mcp

    return run_server(mcp, server_name="taobao")


if __name__ == "__main__":
    sys.exit(main())
