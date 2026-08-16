"""Measure stdio startup: initialize + tools/list latency for MCP servers.

This is exactly what a dsh MCP client waits for on activation. The MCP SDK
timeout in dsh is 60 seconds per request, so this must always stay far below it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def send(proc: subprocess.Popen, obj: object) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()


def read_msg(proc: subprocess.Popen, deadline: float) -> dict | None:
    while time.time() < deadline:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        return message
    return None


def measure(root: Path, script: str, timeout: float = 180.0) -> int:
    command = ["uv", "run", "--frozen", "--directory", str(root), script]
    started = time.time()
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = started + timeout
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
                    "clientInfo": {"name": "startup-probe", "version": "0"},
                },
            },
        )
        init = read_msg(proc, deadline)
        init_elapsed = time.time() - started
        if init is None:
            stderr = proc.stderr.read(2500).decode("utf-8", "replace") if proc.stderr else ""
            print(f"{script}: initialize did not answer within {timeout}s")
            print(f"stderr: {stderr[:1200]}")
            return 1
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_message: dict | None = None
        while time.time() < deadline:
            message = read_msg(proc, deadline)
            if message is None:
                break
            if message.get("id") == 2:
                tools_message = message
                break
        tools_elapsed = time.time() - started
        if tools_message is None:
            print(f"{script}: tools/list did not answer; initialize took {init_elapsed:.1f}s")
            return 1
        tools = tools_message.get("result", {}).get("tools", [])
        print(f"===== {script} =====")
        print(f"  initialize : {init_elapsed:6.1f}s")
        print(f"  tools/list : {tools_elapsed:6.1f}s  (dsh SDK timeout = 60s per request)")
        print(f"  tools      : {len(tools)}")
        return 0
    finally:
        try:
            proc.terminate()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = DEFAULT_ROOT
    scripts = argv[1:]
    if scripts[:2] and scripts[0] in ("--directory", "--dir") and len(scripts) >= 2:
        root = Path(scripts[1])
        scripts = scripts[2:]
    scripts = scripts or ["compare-mcp", "marketplace-mcp"]
    status = 0
    for script in scripts:
        status |= measure(root, script)
        print()
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
