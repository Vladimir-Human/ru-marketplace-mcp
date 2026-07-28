"""End-to-end stdio MCP check: spawn a server, speak the real protocol.

``list_tools()`` on an in-process FastMCP object proves the decorators ran. It
does not prove the console script starts, that the JSON-RPC handshake
completes, or that stdout stays clean enough to parse — which are the three
ways a stdio MCP server actually fails for a user.

This script launches each server the way a client does (console script,
stdio), performs initialize / tools/list / tools/call, and reports what it
saw. Tools that would hit the network are not called; ``marketplace_sources``
is, because it is pure local state.

Run: uv run python scripts/e2e_stdio_check.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# script name -> how many tools it must expose
EXPECTED_TOOLS = {
    "wb-mcp": 9,
    "ozon-mcp": 4,
    "detmir-mcp": 4,
    "yandex-mcp": 3,
    "compare-mcp": 2,
    "avito-mcp": 4,
    "taobao-mcp": 3,
    "megamarket-mcp": 3,
    "lamoda-mcp": 3,
    "dns-mcp": 3,
    "citilink-mcp": 3,
    "marketplace-mcp": 42,  # 41 mounted + marketplace_sources
}

TIMEOUT_S = 60.0


async def probe(script: str, expected: int) -> tuple[str, bool, str]:
    path = shutil.which(script)
    if path is None:
        return script, False, "console script not on PATH"

    params = StdioServerParameters(command=path, args=[])
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=TIMEOUT_S)
            listed = await asyncio.wait_for(session.list_tools(), timeout=TIMEOUT_S)
            count = len(listed.tools)
            server_name = init.serverInfo.name
            version = init.serverInfo.version

            detail = f"{count} tools, server={server_name} v{version}"
            if count != expected:
                return script, False, f"expected {expected} tools, got {count}"

            # One real tools/call, on a tool that touches no network.
            if script == "marketplace-mcp":
                called = await asyncio.wait_for(
                    session.call_tool("marketplace_sources", {}),
                    timeout=TIMEOUT_S,
                )
                if called.isError:
                    return script, False, f"marketplace_sources returned an error: {called.content}"
                payload = called.structuredContent or {}
                mounted = payload.get("mounted_count")
                skipped = payload.get("skipped", {})
                detail += f", tools/call ok: {mounted} sources mounted"
                if skipped:
                    return script, False, f"sources failed to mount: {skipped}"

            return script, True, detail
    except TimeoutError:
        return script, False, f"timed out after {TIMEOUT_S}s"
    except Exception as exc:
        return script, False, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    results = []
    for script, expected in EXPECTED_TOOLS.items():
        results.append(await probe(script, expected))

    width = max(len(name) for name, _, _ in results)
    failures = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{mark}  {name:<{width}}  {detail}", file=sys.stderr)

    total = len(results)
    print(f"\n{total - failures}/{total} servers completed a real MCP session", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
