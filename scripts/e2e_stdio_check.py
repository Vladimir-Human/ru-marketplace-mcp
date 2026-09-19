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
import tomllib
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# script name -> how many model-facing tools it must expose. Operator-only
# *_selfcheck canaries are no longer MCP tools (they run via
# `marketplace-mcp doctor`). The unified server exposes 40 model-facing tools.
EXPECTED_TOOLS = {
    "wb-mcp": 8,
    "ozon-mcp": 3,
    "detmir-mcp": 3,
    "yandex-mcp": 2,
    "compare-mcp": 4,
    "decision-mcp": 5,  # compare tools + decision_inspect
    "avito-mcp": 3,
    "taobao-mcp": 2,
    "megamarket-mcp": 2,
    "lamoda-mcp": 2,
    "dns-mcp": 2,
    "citilink-mcp": 2,
    "aliexpress-mcp": 2,
    "cian-mcp": 2,
    "mpstats-mcp": 2,
    "marketplace-mcp": 40,  # 39 mounted + marketplace_sources
}

TIMEOUT_S = 60.0
EXPECTED_VERSION = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]
EXPECTED_MOUNTS = {
    "wildberries",
    "ozon",
    "detmir",
    "yandex",
    "compare",
    "avito",
    "taobao",
    "megamarket",
    "lamoda",
    "dns",
    "citilink",
    "aliexpress",
    "cian",
    "mpstats",
}


def validate_sources(payload: object) -> str | None:
    """Reject incomplete introspection even when tools/call itself succeeded."""
    if not isinstance(payload, dict):
        return "marketplace_sources did not return structured content"
    mounted = payload.get("mounted")
    if not isinstance(mounted, list) or not all(isinstance(name, str) for name in mounted):
        return "marketplace_sources returned an invalid mounted list"
    if set(mounted) != EXPECTED_MOUNTS or len(mounted) != len(EXPECTED_MOUNTS):
        return f"expected mounted sources {sorted(EXPECTED_MOUNTS)}, got {mounted}"
    if payload.get("mounted_count") != len(EXPECTED_MOUNTS):
        return f"incorrect mounted_count: {payload.get('mounted_count')}"
    if payload.get("skipped") != {} or payload.get("skipped_count") != 0:
        return f"sources failed to mount or skipped status is incomplete: {payload.get('skipped')}"
    if payload.get("server_version") != EXPECTED_VERSION:
        return f"marketplace_sources version must be {EXPECTED_VERSION}, got {payload.get('server_version')}"
    return None


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
            if version != EXPECTED_VERSION:
                return script, False, f"expected version {EXPECTED_VERSION}, got {version}"
            if count != expected:
                return script, False, f"expected {expected} tools, got {count}"
            if len({tool.name for tool in listed.tools}) != count:
                return script, False, "tools/list contains duplicate names"

            # One real tools/call, on a tool that touches no network.
            if script == "marketplace-mcp":
                called = await asyncio.wait_for(
                    session.call_tool("marketplace_sources", {}),
                    timeout=TIMEOUT_S,
                )
                if called.isError:
                    return script, False, f"marketplace_sources returned an error: {called.content}"
                payload = called.structuredContent
                failure = validate_sources(payload)
                if failure is not None:
                    return script, False, failure
                assert payload is not None
                mounted = payload["mounted_count"]
                detail += f", tools/call ok: {mounted} sources mounted"

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
