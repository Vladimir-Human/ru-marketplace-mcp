#!/usr/bin/env python3
"""Measure a first run of a published build on a fresh profile (plan item P3-1).

    python scripts/first_run_probe.py --from ru-marketplace-mcp==2.10.0 [--dry-run] [--skip-live] [--keep]
    python scripts/first_run_probe.py --from dist/ru_marketplace_mcp-2.10.0-py3-none-any.whl

Everything a new user would meet runs in a temporary folder: an empty uv cache
(the download is part of the first run), a new scraping profile, a free CDP
port that is never 9222, and a Claude Desktop config file that is not the
owner's (the CLI's own ``MARKETPLACE_MCP_CLAUDE_CONFIG`` override). The steps:
``setup`` from the build under test, the ``install claude-code`` line, then
``doctor`` and the matrix through ``scripts/live_check.py`` (the only way this
repository makes live requests, under its budget). Each step's command, exit
code, seconds and output tail go into a JSON report on stdout; each doctor row
(state and next step) is listed. The probe's Chrome is closed and the folder
removed at the end unless ``--keep``.
"""

# START_MODULE_CONTRACT
# PURPOSE: One reproducible first-run measurement: commands, seconds, doctor rows and next steps.
# SCOPE: Temp uv cache/profile/config, a free port; uvx for the build under test; live_check for live requests.
# DEPENDS: stdlib argparse, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib; websockets (close).
# LINKS: M-FIRST-RUN-PROBE; R-FIRST-RUN-PROBE; M-LIVE-CHECK; plan P3-1.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# free_port: A free local TCP port other than 9222.
# plan_steps: The command sequence and the environment, pure (what --dry-run prints).
# doctor_rows: The status-file JSON as rows of source, state and next step.
# run_steps: Run each step with a timeout; record exit code, seconds and the output tail.
# close_chrome: Browser.close over the probe's own CDP port; never another port.
# wait_port_closed: Wait briefly for the probe's CDP port to stop accepting TCP connections.
# remove_work_dir: Bounded PermissionError/OSError retry around shutil.rmtree with clean fallback.
# main: Arguments, the work folder, the report and the cleanup.
# END_MODULE_MAP

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_PORT = 9222
TAIL_LINES = 15


@dataclass(frozen=True)
class Step:
    name: str
    argv: list[str]
    timeout_s: int
    live: bool = False
    env: dict[str, str] = field(default_factory=dict)
    # The build under test runs outside any checkout, as a user's would; live_check runs from the repo.
    cwd: Path = REPO_ROOT


def inside_checkout(path: Path) -> Path | None:
    """The uv workspace above ``path``, if any: the CLI would point its config at that checkout."""
    for parent in [path, *path.parents]:
        candidate = parent / "pyproject.toml"
        try:
            if candidate.is_file() and "[tool.uv.workspace]" in candidate.read_text(encoding="utf-8"):
                return parent
        except OSError:
            continue
    return None


def free_port() -> int:
    """A free local port other than the owner's 9222."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port != OWNER_PORT:
            return port


def probe_env(work: Path, port: int, *, warm_cache: bool) -> dict[str, str]:
    """Settings that keep every step off the owner's Chrome, profile and client config."""
    if port == OWNER_PORT:
        raise ValueError("the probe never uses the owner's CDP port 9222")
    env = {
        "CHROME_CDP_PORT": str(port),
        "CHROME_SCRAPING_PROFILE": str(work / "profile"),
        "MARKETPLACE_MCP_CLAUDE_CONFIG": str(work / "claude_desktop_config.json"),
        "CHROME_CHALLENGE_HANDOFF_S": "0",
    }
    if not warm_cache:
        env["UV_CACHE_DIR"] = str(work / "uv-cache")
    return env


def resolve_from_spec(spec: str) -> str:
    """Resolve a local wheel, sdist or directory before steps execute in temp ``work``."""
    if "://" in spec or spec.startswith("git+"):
        return spec
    candidate = Path(spec).expanduser()
    try:
        exists = candidate.exists()
    except OSError:
        exists = False
    if exists or spec.lower().endswith((".whl", ".tar.gz", ".zip")):
        return str(candidate.resolve())
    return spec


def plan_steps(spec: str, work: Path, python: str, *, skip_live: bool) -> list[Step]:
    """The first run, in the order a user meets it."""
    resolved_spec = resolve_from_spec(spec)
    cli = ["uvx", "--from", resolved_spec, "marketplace-mcp"]
    steps = [
        Step("setup", [*cli, "setup", "--client", "claude", "--profile", "compare", "--no-doctor"], 1200, cwd=work),
        Step("install claude-code", [*cli, "install", "claude-code", "--profile", "compare"], 300, cwd=work),
    ]
    if not skip_live:
        live_check = str(REPO_ROOT / "scripts" / "live_check.py")
        status = str(work / "doctor.json")
        steps += [
            Step("doctor", [python, live_check, "doctor", "--status-file", status], 1200, live=True),
            Step("matrix", [python, live_check, "matrix"], 2400, live=True),
        ]
    return steps


def doctor_rows(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Doctor's status file as one row per source: state and the next step it names."""
    rows = []
    for name, entry in sorted((status.get("sources") or {}).items()):
        entry = entry if isinstance(entry, dict) else {}
        rows.append({"source": name, "state": entry.get("status"), "next_step": entry.get("next_step")})
    return rows


def _tail(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()][-TAIL_LINES:]


def run_steps(steps: list[Step], env: dict[str, str]) -> list[dict[str, Any]]:
    """Run each step; a failed step is recorded and the next one still runs."""
    results = []
    for step in steps:
        started = time.monotonic()
        try:
            done = subprocess.run(
                step.argv,
                cwd=step.cwd,
                env={**os.environ, **env, **step.env},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=step.timeout_s,
                check=False,
            )
            code, output = done.returncode, f"{done.stdout}\n{done.stderr}"
        except subprocess.TimeoutExpired as expired:
            timed_out = (
                expired.stdout.decode("utf-8", errors="replace")
                if isinstance(expired.stdout, bytes)
                else (expired.stdout or "")
            )
            code, output = None, f"timed out after {step.timeout_s} s\n{timed_out}"
        except OSError as error:
            code, output = None, f"could not start: {error}"
        results.append(
            {
                "step": step.name,
                "argv": step.argv,
                "exit": code,
                "seconds": round(time.monotonic() - started, 1),
                "tail": _tail(output),
            }
        )
    return results


def close_chrome(port: int) -> bool:
    """Close the probe's Chrome through its own CDP port (Browser.close)."""
    if port == OWNER_PORT:
        raise ValueError("the probe never closes the owner's Chrome on 9222")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as answer:
            ws_url = json.loads(answer.read())["webSocketDebuggerUrl"]
        from websockets.sync.client import connect

        with connect(ws_url, open_timeout=5, max_size=None) as socket_:
            socket_.send(json.dumps({"id": 1, "method": "Browser.close"}))
            socket_.recv(timeout=5)
        return True
    except Exception:  # nothing listening, or it closed first: both mean no Chrome left
        return False


def wait_port_closed(port: int, *, timeout_s: float = 2.0, poll_s: float = 0.05) -> bool:
    """Wait briefly for the probe's CDP port to stop accepting TCP connections."""
    if port == OWNER_PORT:
        raise ValueError("the probe never touches the owner's Chrome on 9222")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                pass
        except OSError:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_s, remaining))


def remove_work_dir(work: Path, *, timeout_s: float = 2.5, initial_delay_s: float = 0.05) -> None:
    """Retry rmtree briefly while Windows Chrome Crashpad/GPU children release file locks."""
    deadline = time.monotonic() + timeout_s
    delay = initial_delay_s
    while work.exists():
        try:
            shutil.rmtree(work)
            return
        except (PermissionError, OSError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                shutil.rmtree(work, ignore_errors=True)
                return
            time.sleep(min(delay, remaining))
            delay = min(delay * 2.0, 0.25)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure a first run of a published build on a fresh profile.")
    parser.add_argument(
        "--from", dest="spec", required=True, help="wheel path or PyPI spec, e.g. ru-marketplace-mcp==2.10.0"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the steps and settings; run nothing")
    parser.add_argument("--skip-live", action="store_true", help="setup and install only; no marketplace request")
    parser.add_argument("--warm-cache", action="store_true", help="use the normal uv cache instead of an empty one")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder")
    parser.add_argument("--work-root", type=Path, default=None, help="where the temporary folder goes (default: TEMP)")
    args = parser.parse_args(argv)

    root = (args.work_root or Path(tempfile.gettempdir())).resolve()
    checkout = inside_checkout(root)
    if checkout is not None:
        sys.stderr.write(
            f"the work folder would sit inside the checkout {checkout}: the CLI would point its config there, "
            "not at the published build. Pass --work-root outside any checkout.\n"
        )
        return 2
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="first-run-probe-", dir=root))
    port = free_port()
    env = probe_env(work, port, warm_cache=args.warm_cache)
    steps = plan_steps(args.spec, work, sys.executable, skip_live=args.skip_live)
    report: dict[str, Any] = {"spec": args.spec, "work": str(work), "port": port, "env": env}
    try:
        if args.dry_run:
            report["steps"] = [{"step": s.name, "argv": s.argv, "live": s.live} for s in steps]
        else:
            started = time.monotonic()
            try:
                report["steps"] = run_steps(steps, env)
                report["total_seconds"] = round(time.monotonic() - started, 1)
                status = work / "doctor.json"
                if status.exists():
                    report["doctor"] = doctor_rows(json.loads(status.read_text(encoding="utf-8")))
            finally:
                try:
                    report["chrome_closed"] = close_chrome(port)
                finally:
                    wait_port_closed(port)
    finally:
        if not args.keep:
            remove_work_dir(work)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for row in report.get("steps", []):
        sys.stderr.write(f"{row['step']:<20} exit={row.get('exit')} {row.get('seconds', '')} s\n")
    for row in report.get("doctor", []):
        sys.stderr.write(f"  {row['source']:<14} {row['state']}: {row['next_step'] or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
