"""End-to-end stdio MCP check through the published OCI package.

The local e2e check spawns console scripts on PATH. This one proves the exact
artifact the MCP Registry will advertise: `docker run --rm -i <image>` with the
stdio variant of the Dockerfile, defaulting to the unified marketplace server.
Uses only the standard library so it runs on a GitHub-hosted Ubuntu runner
without installing the Python MCP SDK.

Set MCP_DOCKER_IMAGE to the image reference and MCP_EXPECTED_VERSION to the
release version, then run: python scripts/e2e_stdio_check_docker.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

from stdio_probe import StdioProbe

EXPECTED_TOOLS = 40
TIMEOUT_S = 120.0


def probe(image: str, expected_version: str) -> int:
    container = f"mcp-stdio-probe-{uuid.uuid4().hex}"
    try:
        return _probe(image, container, expected_version)
    finally:
        # Killing an attached Docker CLI does not guarantee removal of its container.
        # --rm may already have removed it after normal EOF; that is harmless.
        try:
            subprocess.run(
                ["docker", "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _probe(image: str, container: str, expected_version: str) -> int:
    deadline = time.monotonic() + TIMEOUT_S
    with StdioProbe(["docker", "run", "--rm", "--name", container, "-i", image]) as session:
        initialized = session.initialize("docker-stdio-probe", deadline)
        server_info = initialized.get("serverInfo")
        version = server_info.get("version") if isinstance(server_info, dict) else None
        if version != expected_version:
            raise RuntimeError(f"initialize server version: expected {expected_version}, got {version}")
        tools = session.list_tools(deadline)
        if len(tools) != EXPECTED_TOOLS:
            raise RuntimeError(f"expected {EXPECTED_TOOLS} tools, got {len(tools)}")
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "marketplace_sources", "arguments": {}},
            }
        )
        result = session.response(3, deadline)
        if result.get("isError"):
            raise RuntimeError(f"marketplace_sources returned an error: {result}")
        content = result.get("structuredContent") or {}
        version = content.get("server_version")
        if version != expected_version:
            raise RuntimeError(f"marketplace_sources server version: expected {expected_version}, got {version}")
        mounted = content.get("mounted_count") or 0
        if mounted != 14:
            raise RuntimeError(f"expected 14 mounted sources, got {mounted}")
        print(f"PASS: docker stdio MCP v{expected_version}, {len(tools)} tools, {mounted} sources mounted")
        return 0


def main() -> int:
    image = os.environ.get("MCP_DOCKER_IMAGE")
    if not image:
        print("MCP_DOCKER_IMAGE is not set", file=sys.stderr)
        return 2
    expected_version = os.environ.get("MCP_EXPECTED_VERSION")
    if not expected_version:
        print("MCP_EXPECTED_VERSION is not set", file=sys.stderr)
        return 2
    print(f"probe {image}")
    try:
        return probe(image, expected_version)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
