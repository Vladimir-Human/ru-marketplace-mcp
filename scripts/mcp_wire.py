"""Measure the real MCP wire cost of the hosted servers.

Talks MCP over stdio (``initialize`` + ``tools/list``), the exact exchange a
dsh MCP client performs on activation, and estimates the token cost of the
advertised tool schemas. The estimate is shared with the dsh bundle gate:
Cyrillic characters weigh 1/3, everything else 1/4.
"""

from __future__ import annotations

import argparse
import hashlib
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


def _snapshot(script: str, tools: list[dict], elapsed: float) -> dict[str, object]:
    rows = []
    for tool in tools:
        blob = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
        rows.append({"name": tool.get("name", ""), "tokens": estimate_tokens(blob)})
    rows.sort(key=lambda row: str(row["name"]))
    total = sum(int(row["tokens"]) for row in rows)
    schema_hash = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {
        "script": script,
        "tool_count": len(tools),
        "wire_tokens": total,
        "latency_ms": round(elapsed * 1000, 1),
        "schema_sha256": schema_hash,
        "tools": rows,
    }


def _load_baseline(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "profiles": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("profiles", {}), dict):
        raise ValueError(f"invalid baseline file: {path}")
    return value


def _check_gate(
    snapshot: dict[str, object], baseline: dict[str, object], max_regression: float, max_latency_ms: float | None
) -> dict[str, object]:
    profiles = baseline.get("profiles", {})
    old = profiles.get(snapshot["script"], {}) if isinstance(profiles, dict) else {}
    failures: list[str] = []
    token_delta = None
    latency_delta = None
    if isinstance(old, dict):
        previous = old.get("wire_tokens")
        if isinstance(previous, (int, float)) and previous > 0:
            token_delta = (snapshot["wire_tokens"] - previous) / previous * 100
            if token_delta > max_regression:
                failures.append(f"wire_tokens_regression:{token_delta:.2f}%>{max_regression:.2f}%")
        previous_latency = old.get("latency_ms")
        if isinstance(previous_latency, (int, float)) and previous_latency > 0:
            latency_delta = snapshot["latency_ms"] - previous_latency
    if max_latency_ms is not None and snapshot["latency_ms"] > max_latency_ms:
        failures.append(f"latency_ms:{snapshot['latency_ms']:.1f}>{max_latency_ms:.1f}")
    return {
        "ok": not failures,
        "failures": failures,
        "token_delta_percent": token_delta,
        "latency_delta_ms": latency_delta,
        "baseline": old,
    }


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scripts", nargs="*", help="MCP console scripts")
    parser.add_argument("--directory", "--dir", dest="directory", default=None)
    parser.add_argument("--baseline", type=Path, default=None, help="stored JSON baseline")
    parser.add_argument("--update-baseline", action="store_true", help="write measured values as baseline")
    parser.add_argument("--json-out", type=Path, default=None, help="write machine-readable report")
    parser.add_argument("--max-token-regression-percent", type=float, default=10.0)
    parser.add_argument("--max-latency-ms", type=float, default=None)
    args = parser.parse_args(argv[1:])
    root = Path(args.directory) if args.directory else DEFAULT_ROOT
    scripts = args.scripts or ["compare-mcp", "marketplace-mcp"]
    baseline = _load_baseline(args.baseline) if args.baseline else {"version": 1, "profiles": {}}
    snapshots: list[dict[str, object]] = []
    gates: dict[str, object] = {}
    for script in scripts:
        tools, elapsed = fetch_tools(root, script)
        if tools is None:
            print(f"{script}: did not answer within {elapsed:.1f}s")
            gates[script] = {"ok": False, "failures": ["no_tools_response"], "latency_ms": round(elapsed * 1000, 1)}
            continue
        snapshot = _snapshot(script, tools, elapsed)
        snapshots.append(snapshot)
        gates[script] = _check_gate(snapshot, baseline, args.max_token_regression_percent, args.max_latency_ms)
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
    report = {
        "version": 1,
        "root": str(root),
        "profiles": snapshots,
        "gates": gates,
        "ok": all(bool(g.get("ok")) for g in gates.values()) if gates else False,
    }
    if args.update_baseline:
        if args.baseline is None:
            parser.error("--update-baseline requires --baseline")
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps({"version": 1, "profiles": {s["script"]: s for s in snapshots}}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
