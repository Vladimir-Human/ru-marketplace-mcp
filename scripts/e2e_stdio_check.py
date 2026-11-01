"""End-to-end stdio MCP check: spawn a server, speak the real protocol.

``list_tools()`` on an in-process FastMCP object proves the decorators ran. It
does not prove the console script starts, that the JSON-RPC handshake
completes, or that stdout stays clean enough to parse — which are the three
ways a stdio MCP server actually fails for a user.

This script launches each server the way a client does (console script,
stdio), opens a session / tools/list / tools/call, and reports what it saw.
Every server is probed in both protocol eras FastMCP 4 serves: the handshake
era (``initialize``, what most clients speak today) and the sessionless
2026-07-28 era (``server/discover``). Tools that would hit the network are not
called; ``marketplace_sources`` is, because it is pure local state.

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
from mcp_core.sources import MOUNT_GATED_SOURCES, SOURCES

# script name -> how many model-facing tools it must expose. Operator-only
# *_selfcheck canaries are no longer MCP tools (they run via
# `marketplace-mcp doctor`), and the per-source counts are the source registry's
# `tools` facts. The check pins MPSTATS_MP_AUTH empty, so the unified server
# exposes the `marketplace` profile's 39 model-facing tools: without a token it
# does not mount the two mpstats tools (R-MPSTATS-MOUNT-GATE).
EXPECTED_TOOLS = {spec.console_script: spec.tools for spec in SOURCES}
# decision-mcp is the compare connector's second console script — the middle
# profile, not a source of its own: the four compare tools plus decision_inspect.
EXPECTED_TOOLS["decision-mcp"] = 5

TIMEOUT_S = 60.0
# How the client opens the connection in each protocol era.
ERAS = ("handshake", "sessionless")
EXPECTED_VERSION = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]


def _expected_mounts() -> set[str]:
    """The unified server's default, token-less mount.

    A real client with a token sees one more mounted source (mpstats); this
    check pins the token-less surface so it stays deterministic on a machine
    that happens to have MPSTATS_MP_AUTH set.
    """
    mounts: set[str] = set()
    for spec in SOURCES:
        if spec.mount is not None and spec.name not in MOUNT_GATED_SOURCES:
            mounts.add(spec.mount)
    return mounts


EXPECTED_MOUNTS = _expected_mounts()


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
    skipped = payload.get("skipped")
    if not isinstance(skipped, dict):
        return f"marketplace_sources returned an invalid skipped map: {skipped!r}"
    reason = skipped.get("mpstats")
    if set(skipped) != {"mpstats"} or not isinstance(reason, str) or "MPSTATS_MP_AUTH" not in reason:
        return f"expected only mpstats skipped for the missing token, got: {skipped}"
    if payload.get("skipped_count") != 1:
        return f"expected skipped_count 1 (mpstats), got {payload.get('skipped_count')}"
    if payload.get("server_version") != EXPECTED_VERSION:
        return f"marketplace_sources version must be {EXPECTED_VERSION}, got {payload.get('server_version')}"
    return None


async def probe(script: str, expected: int, era: str = "handshake") -> tuple[str, bool, str]:
    path = shutil.which(script)
    if path is None:
        return script, False, "console script not on PATH"

    # Pin the token-less default so the expected surface is the same on a
    # machine that happens to export MPSTATS_MP_AUTH (R-MPSTATS-MOUNT-GATE).
    params = StdioServerParameters(command=path, args=[], env={"MPSTATS_MP_AUTH": ""})
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            opened = session.initialize() if era == "handshake" else session.discover()
            await asyncio.wait_for(opened, timeout=TIMEOUT_S)
            listed = await asyncio.wait_for(session.list_tools(), timeout=TIMEOUT_S)
            count = len(listed.tools)
            info = session.server_info
            if info is None:
                return script, False, f"{era}: server did not identify itself"
            server_name = info.name
            version = info.version

            detail = f"{era} {session.protocol_version}: {count} tools, server={server_name} v{version}"
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
                if called.is_error:
                    return script, False, f"marketplace_sources returned an error: {called.content}"
                payload = called.structured_content
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
    for era in ERAS:
        for script, expected in EXPECTED_TOOLS.items():
            name, ok, detail = await probe(script, expected, era)
            results.append((f"{name} [{era}]", ok, detail))

    width = max(len(name) for name, _, _ in results)
    failures = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{mark}  {name:<{width}}  {detail}", file=sys.stderr)

    total = len(results)
    print(f"\n{total - failures}/{total} server sessions completed over real stdio", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
