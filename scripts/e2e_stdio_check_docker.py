"""End-to-end stdio MCP check through the published OCI package.

The local e2e check spawns console scripts on PATH. This one proves the exact
artifact the MCP Registry will advertise: `docker run --rm -i <image>` with the
stdio variant of the Dockerfile, defaulting to the unified marketplace server.
Uses only the standard library so it runs on a GitHub-hosted Ubuntu runner
without installing the Python MCP SDK.

Run: MCP_DOCKER_IMAGE=ghcr.io/owner/image:tag uv run python scripts/e2e_stdio_check_docker.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

EXPECTED_TOOLS = 39
TIMEOUT_S = 120.0


def send(proc: subprocess.Popen, message: object) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def read_line(proc: subprocess.Popen, deadline: float) -> dict | None:
    while time.time() < deadline:
        assert proc.stdout is not None
        raw = proc.stdout.readline()
        if not raw:
            return None
        try:
            message = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        return message
    return None


def probe(image: str) -> int:
    started = time.time()
    deadline = started + TIMEOUT_S
    proc = subprocess.Popen(
        ["docker", "run", "--rm", "-i", image],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "docker-stdio-probe", "version": "0"},
                },
            },
        )
        init = read_line(proc, deadline)
        if init is None or init.get("id") != 1:
            raise RuntimeError("initialize: no response")
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = None
        while True:
            message = read_line(proc, deadline)
            if message is None:
                raise RuntimeError("tools/list: no response")
            if message.get("id") == 2:
                listed = message
                break
        tools = (listed.get("result") or {}).get("tools", [])
        if len(tools) != EXPECTED_TOOLS:
            raise RuntimeError(f"expected {EXPECTED_TOOLS} tools, got {len(tools)}")

        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "marketplace_sources", "arguments": {}},
            },
        )
        called = None
        while True:
            message = read_line(proc, deadline)
            if message is None:
                raise RuntimeError("tools/call marketplace_sources: no response")
            if message.get("id") == 3:
                called = message
                break
        result = called.get("result") or {}
        if result.get("isError"):
            raise RuntimeError(f"marketplace_sources returned an error: {result}")
        mounted = (result.get("structuredContent") or {}).get("mounted_count") or 0
        if mounted != 14:
            raise RuntimeError(f"expected 14 mounted sources, got {mounted}")
        print(f"PASS: docker stdio MCP session, {len(tools)} tools, {mounted} sources mounted")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    image = os.environ.get("MCP_DOCKER_IMAGE")
    if not image:
        print("MCP_DOCKER_IMAGE is not set", file=sys.stderr)
        return 2
    print(f"probe {image}")
    try:
        return probe(image)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
