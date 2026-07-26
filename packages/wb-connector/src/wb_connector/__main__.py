"""Entry point for the Wildberries MCP server.

Exposed as the ``wb-mcp`` console script, so MCP client configs can spawn the
server without knowing where the package lives on disk:

    {"command": "uvx", "args": ["--from", "wb-connector", "wb-mcp"]}

stdio is the default transport because that is what MCP clients speak. Nothing
here may write to stdout — the JSON-RPC stream owns it.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the server on stdio until the client disconnects."""
    from wb_connector.server import mcp

    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # The client went away mid-write; that is a normal shutdown for stdio.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
