"""Budget-first full-profile stdio server wrapper for the private audit.

START_MODULE_CONTRACT
PURPOSE: Install all request hooks in the actual MCP child before running its server.
SCOPE: Same-ledger nonce handshake; full stdio profile; optional local target for offline tests.
DEPENDS: live_budget; marketplace_connector.server; mcp_core.runtime; stdlib.
LINKS: M-LIVE-AUDIT, R-LIVE-AUDIT.child.
START_MODULE_MAP
  main: Install hooks, attest their active ledger, then run the unified server or local test target.
END_MODULE_MAP
END_MODULE_CONTRACT
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
from pathlib import Path

import live_budget

# START_BLOCK_WRAPPED_SERVER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path)
    args = parser.parse_args(argv)
    os.environ["MCP_TRANSPORT"] = "stdio"
    os.environ["MARKETPLACE_SOURCES"] = ""
    os.environ["CHROME_CHALLENGE_HANDOFF_S"] = "0"
    budget = live_budget.install()
    active = live_budget.installed_budget()
    if active is not budget or budget.ledger.path is None:
        raise RuntimeError("audit child budget hook is not active")
    marker = Path(os.environ["LIVE_AUDIT_HOOK_FILE"])
    marker.write_text(
        json.dumps(
            {
                "installed": True,
                "ledger": str(budget.ledger.path.resolve()),
                "nonce": os.environ["LIVE_AUDIT_NONCE"],
                "pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )
    if args.target is not None:
        runpy.run_path(str(args.target), run_name="__main__")
        return 0
    from marketplace_connector.server import mcp
    from mcp_core.runtime import run_server

    return run_server(mcp, server_name="marketplace")


# END_BLOCK_WRAPPED_SERVER

if __name__ == "__main__":
    raise SystemExit(main())
