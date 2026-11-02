"""Measure the real MCP wire cost of the hosted servers.

Talks MCP over stdio (``initialize`` + ``tools/list``), the exact exchange a
dsh MCP client performs on activation, and estimates the token cost of the
advertised tool schemas. The estimate is shared with the dsh bundle gate:
Cyrillic characters weigh 1/3, everything else 1/4.

Every profile carries two figures: ``wire_tokens`` for the whole ``tools/list``
as the wire sends it, and ``model_tokens`` for the part a provider request
actually carries to the model -- each tool's ``name``, ``description`` and
``input_schema``. ``outputSchema``, ``annotations`` and the rest stay in the
client, so without the second figure a growing description can hide behind a
shrinking output schema.

Every budget, ceiling and threshold is read as a number that can bound
something: a finite value above zero. ``1e999`` parses as ``inf``, and an
infinite budget bounds nothing -- the regression delta becomes ``nan``, no
comparison exceeds the limit, and the check passes whatever grew. ``True`` is
not a number either, although Python says it is an int, and a string is not a
number at all. Such a value is refused with a clear message and a failing
exit code instead of quietly switching the gate off.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

from stdio_probe import ProbeError, StdioProbe

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# The relative budget the gate uses when the caller names none.
DEFAULT_MAX_TOKEN_REGRESSION_PERCENT = 10.0


def estimate_tokens(text: str) -> int:
    """Conservative token estimate matching the dsh budget checker."""
    cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    return int(cyrillic / 3.0 + (len(text) - cyrillic) / 4.0)


def fetch_profile(root: Path, script: str, timeout: float = 180.0) -> tuple[list[dict] | None, str, float]:
    """Start ``uv run --directory <root> <script>`` and collect tools and instructions.

    The same ``initialize`` + ``tools/list`` exchange a client performs on
    activation. ``instructions`` come back in the initialize result and are paid
    once per session, so they are measured next to the tool schemas rather than
    assumed to be free.
    """
    command = ["uv", "run", "--frozen", "--directory", str(root), script]
    started = time.monotonic()
    try:
        with StdioProbe(command) as probe:
            deadline = started + timeout
            handshake = probe.initialize("wire-probe", deadline)
            instructions = handshake.get("instructions")
            tools = probe.list_tools(deadline)
            elapsed = time.monotonic() - started
        return tools, instructions if isinstance(instructions, str) else "", elapsed
    except (ProbeError, OSError) as exc:
        print(f"{script}: {exc}", file=sys.stderr)
        return None, "", time.monotonic() - started


def fetch_tools(root: Path, script: str, timeout: float = 180.0) -> tuple[list[dict] | None, float]:
    """Start the server and collect ``tools/list``; see :func:`fetch_profile`."""
    tools, _instructions, elapsed = fetch_profile(root, script, timeout)
    return tools, elapsed


def tool_tokens(tool: dict) -> int:
    """The estimate for one advertised tool: its compact JSON, as the wire carries it."""
    return estimate_tokens(json.dumps(tool, ensure_ascii=False, separators=(",", ":")))


def model_tool_tokens(tool: dict) -> int:
    """The estimate for one tool as the model pays for it.

    A provider request carries only the name, the description and the input
    schema of each advertised tool; ``outputSchema``, ``annotations`` and
    ``_meta`` stay in the client. Counting the whole tool hides a description
    that grows while an output schema shrinks by the same amount.
    """
    payload = {
        "name": tool.get("name", ""),
        "description": tool.get("description") or "",
        "input_schema": tool.get("inputSchema") or {},
    }
    return estimate_tokens(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def wire_tokens(tools: list[dict]) -> int:
    """The estimate for a whole tools/list: the sum over its tools."""
    return sum(tool_tokens(tool) for tool in tools)


def model_tokens(tools: list[dict]) -> int:
    """The estimate for a whole tools/list as the model pays for it: the sum over its tools."""
    return sum(model_tool_tokens(tool) for tool in tools)


def _snapshot(script: str, tools: list[dict], elapsed: float, instructions: str = "") -> dict[str, object]:
    """One profile's measurement: the whole wire and the part the model pays for."""
    rows = []
    for tool in tools:
        rows.append(
            {"name": tool.get("name", ""), "tokens": tool_tokens(tool), "model_tokens": model_tool_tokens(tool)}
        )
    rows.sort(key=lambda row: str(row["name"]))
    total = sum(int(row["tokens"]) for row in rows)
    model_total = sum(int(row["model_tokens"]) for row in rows)
    schema_hash = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {
        "script": script,
        "tool_count": len(tools),
        "wire_tokens": total,
        "model_tokens": model_total,
        "instructions_tokens": estimate_tokens(instructions),
        "latency_ms": round(elapsed * 1000, 1),
        "schema_sha256": schema_hash,
        "tools": rows,
    }


def total_tokens(snapshot: dict[str, object]) -> int:
    """Everything one profile costs per session: ``tools/list`` plus ``instructions``."""
    tools = snapshot.get("wire_tokens")
    instructions = snapshot.get("instructions_tokens")
    return int(tools or 0) + int(instructions or 0)


def _load_baseline(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "profiles": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("profiles", {}), dict):
        raise ValueError(f"invalid baseline file: {path}")
    return value


def _finite_number(value: object) -> float | None:
    """The value as a finite float, or None when it cannot serve as a limit.

    A budget is only a budget when it is a number that bounds something.
    ``True`` is not one (Python says it is an int), ``inf`` bounds nothing and
    ``nan`` compares false against everything, and a string is not a number at
    all. ``1e999`` parses as ``inf``, which is how an unbounded baseline once
    slipped through the old ``> 0`` check.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _positive_limit(value: object) -> float | None:
    """The value as a limit: a finite number above zero, or None."""
    number = _finite_number(value)
    if number is None or number <= 0:
        return None
    return number


def _describe(value: object) -> str:
    """A short name for a rejected limit, for the error message."""
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, str):
        return f"the string {value!r}"
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return repr(value)


def _threshold(raw: str | float | None, label: str, default: float | None) -> float | None:
    """One threshold option: a finite number above zero, or its default.

    ``nan`` and ``inf`` reach argparse's ``float``, and a comparison against
    them never fires: the gate would pass every snapshot. They are refused
    here, with the option named.
    """
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a finite positive number, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite positive number, got {raw!r}")
    return value


def _invalid_baseline_limits(baseline: dict[str, object]) -> list[str]:
    """Every budget and ceiling in a baseline that is not a finite positive number."""
    problems: list[str] = []
    profiles = baseline.get("profiles", {})
    if isinstance(profiles, dict):
        for script, entry in profiles.items():
            if not isinstance(entry, dict):
                problems.append(f"profile {script}: not an object")
                continue
            for measure in ("wire_tokens", "model_tokens"):
                value = entry.get(measure)
                if value is None:
                    continue
                if _positive_limit(value) is None:
                    problems.append(f"{measure} budget of {script}: {_describe(value)} is not a finite positive number")
    ceilings = baseline.get("ceilings", {})
    if isinstance(ceilings, dict):
        for script, value in ceilings.items():
            if value is None:
                continue
            if _positive_limit(value) is None:
                problems.append(f"ceiling of {script}: {_describe(value)} is not a finite positive number")
    return problems


def _ceiling_for(baseline: dict[str, object], script: str) -> object:
    """The raw ceiling a baseline declares for a profile, or None."""
    ceilings = baseline.get("ceilings")
    if not isinstance(ceilings, dict):
        return None
    return ceilings.get(script)


def _ceiling_failure(snapshot: dict[str, object], baseline: dict[str, object]) -> str | None:
    """Compare one profile's whole cost with its absolute ceiling, if it has one.

    The relative gate only sees a change since the last committed baseline, and
    that baseline is rewritten by hand with ``--update-baseline``: a profile can
    grow by just under the limit every release and never trip it. The ceiling is
    the part that does not move on its own. A ceiling that is absent or not a
    finite positive number fails closed in gate mode, where the caller checks
    the baseline shape; here it simply does not constrain.
    """
    ceiling = _positive_limit(_ceiling_for(baseline, str(snapshot["script"])))
    if ceiling is None:
        return None
    total = total_tokens(snapshot)
    if total > ceiling:
        return f"ceiling_exceeded:{total}>{int(ceiling)}"
    return None


def _check_gate(
    snapshot: dict[str, object],
    baseline: dict[str, object],
    max_regression: float,
    max_latency_ms: float | None,
    gate: bool = False,
) -> dict[str, object]:
    """Compare one snapshot with its baseline entry.

    ``gate`` is the CI check mode (``--baseline`` without ``--update-baseline``).
    There a missing profile, a missing ``wire_tokens`` or ``model_tokens``
    budget or a missing per-profile ceiling is a failure: a gate that compares
    against nothing passes any snapshot, however large. A budget that is there
    but cannot bound anything -- a boolean, a string, ``nan``, ``inf`` -- is a
    failure too, under its own name: an infinite budget turns the regression
    delta into ``nan``, which never exceeds the limit, and the check would pass
    a tool that grew by a million tokens. Measurement runs and baseline
    creation keep the permissive reading.
    """
    profiles = baseline.get("profiles", {})
    entry = profiles.get(snapshot["script"]) if isinstance(profiles, dict) else None
    old = entry if isinstance(entry, dict) else {}
    failures: list[str] = []
    deltas: dict[str, float | None] = {"wire_tokens": None, "model_tokens": None}
    latency_delta = None
    if gate:
        if entry is None:
            failures.append(f"missing_baseline_profile:{snapshot['script']}")
        else:
            budget = old.get("wire_tokens")
            if budget is None:
                failures.append(f"missing_token_budget:{snapshot['script']}")
            elif _positive_limit(budget) is None:
                failures.append(f"invalid_token_budget:{snapshot['script']}:{_describe(budget)}")
            model_budget = old.get("model_tokens")
            if model_budget is None:
                failures.append(f"missing_model_token_budget:{snapshot['script']}")
            elif _positive_limit(model_budget) is None:
                failures.append(f"invalid_model_token_budget:{snapshot['script']}:{_describe(model_budget)}")
            ceiling = _ceiling_for(baseline, str(snapshot["script"]))
            if ceiling is None:
                failures.append(f"missing_ceiling:{snapshot['script']}")
            elif _positive_limit(ceiling) is None:
                failures.append(f"invalid_ceiling:{snapshot['script']}:{_describe(ceiling)}")
    if isinstance(old, dict):
        # Each measure is compared with its own baseline value: the whole wire
        # can stay flat while the part the model pays for grows. Only a finite
        # positive baseline value is a limit; anything else is reported above.
        for measure in ("wire_tokens", "model_tokens"):
            previous = _positive_limit(old.get(measure))
            if previous is not None:
                delta = (snapshot[measure] - previous) / previous * 100
                deltas[measure] = delta
                if delta > max_regression:
                    failures.append(f"{measure}_regression:{delta:.2f}%>{max_regression:.2f}%")
        previous_latency = _finite_number(old.get("latency_ms"))
        if previous_latency is not None and previous_latency > 0:
            latency_delta = snapshot["latency_ms"] - previous_latency
    ceiling_failure = _ceiling_failure(snapshot, baseline)
    if ceiling_failure is not None:
        failures.append(ceiling_failure)
    if max_latency_ms is not None and snapshot["latency_ms"] > max_latency_ms:
        failures.append(f"latency_ms:{snapshot['latency_ms']:.1f}>{max_latency_ms:.1f}")
    return {
        "ok": not failures,
        "failures": failures,
        "token_delta_percent": deltas["wire_tokens"],
        "model_token_delta_percent": deltas["model_tokens"],
        "latency_delta_ms": latency_delta,
        "total_tokens": total_tokens(snapshot),
        "baseline": old,
    }


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scripts", nargs="*", help="MCP console scripts")
    parser.add_argument("--directory", "--dir", dest="directory", default=None)
    parser.add_argument("--timeout", type=float, default=180.0, help="total protocol deadline in seconds")
    parser.add_argument("--baseline", type=Path, default=None, help="stored JSON baseline")
    parser.add_argument("--update-baseline", action="store_true", help="write measured values as baseline")
    parser.add_argument("--json-out", type=Path, default=None, help="write machine-readable report")
    parser.add_argument(
        "--max-token-regression-percent",
        default=None,
        help=f"relative token growth the gate tolerates, in percent (default: {DEFAULT_MAX_TOKEN_REGRESSION_PERCENT:g})",
    )
    parser.add_argument("--max-latency-ms", default=None, help="absolute latency the gate tolerates, in milliseconds")
    args = parser.parse_args(argv[1:])
    # A threshold that cannot bound anything would switch the gate off without
    # saying so: `nan` and `inf` reach argparse's float, and a comparison
    # against them never fires. They are refused here, with the option named.
    try:
        max_regression = _threshold(
            args.max_token_regression_percent,
            "--max-token-regression-percent",
            DEFAULT_MAX_TOKEN_REGRESSION_PERCENT,
        )
        max_latency_ms = _threshold(args.max_latency_ms, "--max-latency-ms", None)
        timeout = _threshold(args.timeout, "--timeout", 180.0)
    except ValueError as exc:
        print(f"invalid option: {exc}", file=sys.stderr)
        return 1
    root = Path(args.directory) if args.directory else DEFAULT_ROOT
    scripts = args.scripts or ["compare-mcp", "marketplace-mcp"]
    # Gate mode is a baseline comparison without --update-baseline: the run is
    # a check, so a missing baseline or a missing budget must fail it instead
    # of silently comparing against nothing. Measurement runs (no --baseline)
    # and baseline creation (--update-baseline) stay permissive.
    gate_mode = args.baseline is not None and not args.update_baseline
    if args.baseline is None:
        baseline = {"version": 1, "profiles": {}}
    else:
        if gate_mode and not args.baseline.exists():
            print(f"baseline file not found: {args.baseline}", file=sys.stderr)
            return 1
        try:
            baseline = _load_baseline(args.baseline)
        except (OSError, ValueError) as exc:
            print(f"cannot read baseline file {args.baseline}: {exc}", file=sys.stderr)
            return 1
        # A budget or ceiling that is not a finite positive number is not a
        # limit: an infinite one compares against nothing and the delta
        # becomes nan, which never exceeds the limit. The whole file is
        # checked, not only the profiles this run measures, because a broken
        # limit is a broken limit whichever profile carries it.
        invalid = _invalid_baseline_limits(baseline)
        if invalid:
            print(
                f"baseline file {args.baseline} carries a limit that is not a finite positive number:", file=sys.stderr
            )
            for line in invalid:
                print(f"  {line}", file=sys.stderr)
            return 1
    snapshots: list[dict[str, object]] = []
    gates: dict[str, object] = {}
    for script in scripts:
        tools, instructions, elapsed = fetch_profile(root, script, timeout)
        if tools is None:
            print(f"{script}: did not answer within {elapsed:.1f}s")
            gates[script] = {"ok": False, "failures": ["no_tools_response"], "latency_ms": round(elapsed * 1000, 1)}
            continue
        snapshot = _snapshot(script, tools, elapsed, instructions)
        snapshots.append(snapshot)
        gates[script] = _check_gate(snapshot, baseline, max_regression, max_latency_ms, gate=gate_mode)
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
        instruction_tokens = int(snapshot["instructions_tokens"])
        model_total = int(snapshot["model_tokens"])
        print(f"\nTOTAL: ~{total} tokens of tools/list, paid on every request")
        print(f"  model pays             : ~{model_total} of that (name, description, input_schema)")
        print(f"  initialize.instructions: ~{instruction_tokens} (paid once per session)")
        print(f"  session total          : ~{total + instruction_tokens}")
        print(f"  model session total    : ~{model_total + instruction_tokens}")
        print(f"  output schema share: ~{output_total} ({100.0 * output_total / total if total else 0.0:.0f}%)")
        print(f"  description share  : ~{sum(row[2] for row in rows)}")
        print(f"  input schema share : ~{sum(row[3] for row in rows)}")
        selfchecks = [row for row in rows if row[1].endswith("_selfcheck")]
        if selfchecks:
            cost = sum(row[0] for row in selfchecks)
            print(f"  {len(selfchecks)} *_selfcheck tools: ~{cost} ({100.0 * cost / total if total else 0.0:.0f}%)")
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
        # Ceilings guard the whole per-profile cost (tools/list plus
        # instructions) against creeping growth. A ceiling already on disk is
        # kept: raising one is a decision, not a side effect of measuring. A new
        # profile gets 5% above what was just measured, and the first floor of
        # 1 keeps the value a positive number, as the gate requires.
        known = baseline.get("ceilings")
        ceilings: dict[str, int] = dict(known) if isinstance(known, dict) else {}
        for snapshot in snapshots:
            ceilings.setdefault(str(snapshot["script"]), int(total_tokens(snapshot) * 1.05) + 1)
        args.baseline.write_text(
            json.dumps(
                {
                    "version": 1,
                    "ceilings": dict(sorted(ceilings.items())),
                    "profiles": {s["script"]: s for s in snapshots},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["ok"]:
        for script, gate_result in gates.items():
            for failure in gate_result.get("failures", []):
                print(f"{script}: {failure}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
