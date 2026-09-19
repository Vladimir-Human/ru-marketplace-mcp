"""Measure stdio startup: initialize + tools/list latency for MCP servers.

This is exactly what a dsh MCP client waits for on activation. The MCP SDK
timeout in dsh is 60 seconds per request, so this must always stay far below it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from stdio_probe import ProbeError, StdioProbe

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def measure(root: Path, script: str, timeout: float = 180.0) -> int:
    command = ["uv", "run", "--frozen", "--directory", str(root), script]
    started = time.monotonic()
    try:
        with StdioProbe(command) as probe:
            deadline = started + timeout
            probe.initialize("startup-probe", deadline)
            init_elapsed = time.monotonic() - started
            tools = probe.list_tools(deadline)
            tools_elapsed = time.monotonic() - started
            print(f"===== {script} =====")
            print(f"  initialize : {init_elapsed:6.1f}s")
            print(f"  tools/list : {tools_elapsed:6.1f}s  (dsh SDK timeout = 60s per request)")
            print(f"  tools      : {len(tools)}")
            return 0
    except (ProbeError, OSError) as exc:
        print(f"{script}: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scripts", nargs="*", help="MCP console scripts")
    parser.add_argument("--directory", "--dir", dest="directory", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--timeout", type=float, default=180.0, help="total protocol deadline in seconds")
    args = parser.parse_args(argv[1:])
    status = 0
    for script in args.scripts or ["compare-mcp", "marketplace-mcp"]:
        status |= measure(args.directory, script, args.timeout)
        print()
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
