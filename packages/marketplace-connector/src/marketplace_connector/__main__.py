"""Entry point for the unified marketplace MCP server.

Exposed as the ``marketplace-mcp`` console script. stdio is the default
transport; set ``MCP_TRANSPORT=http`` for HTTP — see docs/DEPLOYMENT.md.
Diagnostics go to stderr; nothing here may write to stdout.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the server, or the install/doctor CLI when a subcommand is given.

    ``marketplace-mcp`` with no arguments starts the MCP server; with
    ``install`` or ``doctor`` it runs the operator CLI and exits. Subcommands
    go to stdout freely — only the server path owns the JSON-RPC stream.
    """
    if len(sys.argv) > 1 and sys.argv[1] in ("install", "doctor", "-h", "--help"):
        from marketplace_connector.cli import main as cli_main

        return cli_main(sys.argv[1:])

    from mcp_core.runtime import run_server

    from marketplace_connector.server import mcp

    return run_server(mcp, server_name="marketplace")


if __name__ == "__main__":
    sys.exit(main())
