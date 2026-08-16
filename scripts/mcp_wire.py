"""Measure the real MCP wire cost of the hosted servers.

Talks MCP over stdio (``initialize`` + ``tools/list``), the exact exchange a
dsh MCP client performs on activation, and estimates the token cost of the
advertised tool schemas. The estimate is shared with the dsh bundle gate:
Cyrillic characters weigh 1/3, everything else 1/4.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def estimate_tokens(text: str) -> int:
    """Conservative token estimate matching the dsh budget checker."""
    cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return int(cyrillic / 3.0 + (len(text) - cyrillic) / 4.0)


def fetch_tools(root: Path, script: str, timeout: float = 180.0) -> tuple[list[dict] | None, float]:
    """Start ``uv run --directory <root> <script>`` and collect tools/list."""
    cmd = ["uv", "run", "--frozen", "--directory", str(root), script]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    started = time.time()

    def send(obj: object) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        proc.stdin.flush()

    send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "wire-probe", "version": "0"},
            },
        }
    )
    deadline = started + timeout
    tools: list[dict] | None = None
    while time.time() < deadline:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line:
            break
        try:
            message = json.loads(line.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if message.get("id") == 1:
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        elif message.get("id") == 2:
            tools = message.get("result", {}).get("tools", [])
            break

    elapsed = time.time() - started
    try:
        proc.terminate()
    except OSError:
        pass
    return tools, elapsed


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = DEFAULT_ROOT
    scripts = argv[1:]
    if scripts[:2] and scripts[0] in ("--directory", "--dir") and len(scripts) >= 2:
        root = Path(scripts[1])
        scripts = scripts[2:]
    scripts = scripts or ["compare-mcp", "marketplace-mcp"]
    for script in scripts:
        tools, elapsed = fetch_tools(root, script)
        if tools is None:
            print(f"{script}: did not answer within {elapsed:.1f}s")
            continue
        print("=" * 72)
        print(f"{script}: {len(tools)} tools, warm start to tools/list {elapsed:.1f}s")
        print("=" * 72)
        rows = []
        for tool in tools:
            blob = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
            description = tool.get("description") or ""
            input_schema = json.dumps(tool.get("inputSchema") or {}, ensure_ascii=False)
            output_schema = json.dumps(tool.get("outputSchema") or {}, ensure_ascii=False)
            rows.append(
                (
                    estimate_tokens(blob),
                    tool["name"],
                    estimate_tokens(description),
                    estimate_tokens(input_schema),
                    estimate_tokens(output_schema),
                )
            )
        rows.sort(reverse=True)
        total = sum(row[0] for row in rows)
        print(f"{'total':>7} {'desc':>6} {'in':>5} {'out':>6}  tool")
        for cost, name, desc, input_cost, output_cost in rows[:12]:
            print(f"{cost:7d} {desc:6d} {input_cost:5d} {output_cost:6d}  {name}")
        if len(rows) > 12:
            print(f"  ... {len(rows) - 12} more")
        output_total = sum(row[4] for row in rows)
        print(f"\nTOTAL: ~{total} tokens paid on every request")
        print(f"  output schema share: ~{output_total} ({100.0 * output_total / total:.0f}%)")
        print(f"  description share  : ~{sum(row[2] for row in rows)}")
        print(f"  input schema share : ~{sum(row[3] for row in rows)}")
        selfchecks = [row for row in rows if row[1].endswith("_selfcheck")]
        if selfchecks:
            cost = sum(row[0] for row in selfchecks)
            print(f"  {len(selfchecks)} *_selfcheck tools: ~{cost} ({100.0 * cost / total:.0f}%)")
        groups: dict[str, list[int]] = {}
        for row in rows:
            groups.setdefault(row[1].split("_")[0], []).append(row[0])
        if len(groups) > 1:
            print("  by source:")
            for prefix, costs in sorted(groups.items(), key=lambda item: -sum(item[1])):
                print(f"    {prefix:<12} {len(costs):2d} tools  ~{sum(costs):6d} tok.")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
