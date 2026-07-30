"""Operator CLI for the marketplace connectors.

Two commands cover the two moments an operator actually needs help with:

``marketplace-mcp install``
    Write the client config. Editing ``claude_desktop_config.json`` by hand is
    where every setup mistake lives (wrong slashes, a stale path, a missing
    comma), so the CLI prints the exact JSON block to paste, per client.

``marketplace-mcp doctor``
    Run every connector's selfcheck and report per-source status. This is the
    answer to "is it broken, or is it me": a source blocked from your network
    shows as inconclusive with the reason, a drifted parser shows as drift.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
from typing import Any

from mcp_core.logging import log_event

# Clients whose config format this block is known to fit. A typo like
# "cursour" should say so rather than silently printing a Claude block.
KNOWN_CLIENTS = {"claude", "claude-code", "cursor"}

# (config key, console script, human note)
SERVERS: list[tuple[str, str, str]] = [
    ("wildberries", "wb-mcp", "anonymous HTTP — works anywhere"),
    ("ozon", "ozon-mcp", "needs your Chrome for tier 2 — see docs/CDP_SETUP.md"),
    ("yandex-market", "yandex-mcp", "anonymous HTTP"),
    ("detsky-mir", "detmir-mcp", "anonymous HTTP"),
    ("avito", "avito-mcp", "needs your Chrome — IP firewall"),
    ("taobao", "taobao-mcp", "needs your Chrome — signed API"),
    ("megamarket", "megamarket-mcp", "needs your Chrome — ServicePipe"),
    ("lamoda", "lamoda-mcp", "cards anonymous; search needs your Chrome"),
    ("dns", "dns-mcp", "needs your Chrome — Qrator proof-of-work"),
    ("citilink", "citilink-mcp", "needs your Chrome — Qrator"),
    ("compare-prices", "compare-mcp", "fans out across all of the above"),
]

_SELFCHECKS: list[tuple[str, str, str]] = [
    ("wildberries", "wb_connector.server", "wb_selfcheck"),
    ("ozon", "ozon_connector.server", "ozon_selfcheck"),
    ("yandex_market", "yandex_connector.server", "yandex_selfcheck"),
    ("detsky_mir", "detmir_connector.server", "detmir_selfcheck"),
    ("avito", "avito_connector.server", "avito_selfcheck"),
    ("taobao", "taobao_connector.server", "taobao_selfcheck"),
    ("megamarket", "megamarket_connector.server", "megamarket_selfcheck"),
    ("lamoda", "lamoda_connector.server", "lamoda_selfcheck"),
    ("dns", "dns_connector.server", "dns_selfcheck"),
    ("citilink", "citilink_connector.server", "citilink_selfcheck"),
    ("mpstats", "mpstats_connector.server", "mpstats_selfcheck"),
]


def _workspace_root() -> pathlib.Path | None:
    """Find the source checkout this CLI is running from, if it is one.

    Walks up from this file looking for the root pyproject that declares the uv
    workspace. Returns None when installed as a plain wheel, where there is no
    checkout to point `uv run --directory` at.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        try:
            if candidate.is_file() and "[tool.uv.workspace]" in candidate.read_text(encoding="utf-8"):
                return parent
        except OSError:
            continue
    return None


def _config_block() -> tuple[dict[str, Any], str]:
    """The mcpServers block, one entry per source, plus a note on how it was built.

    Two deployments need two different blocks. From a source checkout the
    honest command is ``uv run --directory <that checkout>``, and we know the
    path — printing a ``/path/to/...`` placeholder just moves a solved problem
    onto the operator, which is where setup mistakes come from. From a wheel
    install there is no checkout, so the console script on PATH is the command.
    """
    root = _workspace_root()
    if root is not None:
        block = {
            key: {"command": "uv", "args": ["run", "--directory", str(root), script]} for key, script, _ in SERVERS
        }
        return block, f"# Paths point at this checkout: {root}"

    block = {}
    unresolved = []
    for key, script, _ in SERVERS:
        resolved = shutil.which(script)
        if resolved is None:
            unresolved.append(script)
        block[key] = {"command": resolved or script, "args": []}
    note = "# Installed as a package: commands are the console scripts on PATH."
    if unresolved:
        note += f"\n# Not found on PATH, left unresolved: {', '.join(unresolved)}"
    return block, note


def cmd_install(argv: list[str]) -> int:
    """Print the client config block to paste."""
    client = argv[0] if argv else "claude"
    if client not in KNOWN_CLIENTS:
        print(
            f"unknown client {client!r}; expected one of {', '.join(sorted(KNOWN_CLIENTS))}",
            file=sys.stderr,
        )
        return 2
    block, note = _config_block()
    print(f"# Add to your mcpServers block ({client}).")
    print(note)
    print(json.dumps(block, indent=2, ensure_ascii=False))
    print("\n# Notes:")
    for _, _, note_line in SERVERS:
        print(f"#   - {note_line}")
    print("\n# Or wire one entry instead of twelve: the unified 'marketplace-mcp' server mounts every source.")
    return 0


def _check_detail(check: object) -> str:
    """Render one sub-check as state plus the reason it reached that state.

    ``inconclusive`` on its own is unactionable — rate-limited, IP-banned and
    no-CDP all print the same word, and the operator is left guessing which.
    Connectors already classify this (avito sets rate_limited vs blocked vs
    transport_down, with the HTTP code), so the only thing missing was showing
    it. Wait out a 429; fix the network for a 403.
    """
    state = _attr(check, "state", "?")
    reason = _attr(check, "reason", None)
    code = _attr(check, "code", None)
    if not reason:
        return str(state)
    suffix = f" http {code}" if code else ""
    return f"{state} ({reason}{suffix})"


def _attr(obj: object, key: str, default: object = None) -> object:
    """Sub-checks arrive as dicts from some connectors and models from others."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _run_one_selfcheck(name: str, module_path: str, tool_name: str) -> tuple[str, str, str]:
    """Run one connector's selfcheck, returning (name, status, detail)."""
    try:
        module = __import__(module_path, fromlist=[tool_name])
        tool = getattr(module, tool_name)
        result = await tool()
        status = getattr(result, "status", "unknown")
        checks = getattr(result, "checks", {}) or {}
        detail = ", ".join(f"{k}:{_check_detail(v)}" for k, v in checks.items()) or status
        return name, status, detail
    except Exception as exc:
        return name, "error", f"{type(exc).__name__}: {str(exc)[:120]}"


def cmd_doctor(argv: list[str]) -> int:
    """Run every selfcheck and print a per-source health table.

    ``--status-file PATH`` also writes the machine-readable report: a JSON
    snapshot with per-source status and a timestamp, so a cron job or dashboard
    can watch drift without parsing the human table. The file is replaced
    atomically, never appended to — a monitoring reader always gets one
    complete snapshot.
    """
    status_file: str | None = None
    sources: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--status-file":
            status_file = next(it, None)
        else:
            sources.append(arg)
    only = {a.strip() for a in sources} if sources else None
    results = []
    for name, module_path, tool_name in _SELFCHECKS:
        if only and name not in only:
            continue
        results.append(asyncio.run(_run_one_selfcheck(name, module_path, tool_name)))

    print("Marketplace connector health (selfcheck):")
    healthy = blocked = drifted = 0
    for name, status, detail in results:
        marker = {"success": "ok ", "drift_detected": "DRF", "inconclusive": "blk"}.get(status, "err")
        if status == "success":
            healthy += 1
        elif status == "drift_detected":
            drifted += 1
        else:
            blocked += 1
        print(f"  {marker} {name:<14} {status:<15} {detail}")
    print(f"\n  {healthy} healthy, {blocked} blocked/inconclusive, {drifted} drifted.")
    print("  'inconclusive' usually means the source is blocked from this network —")
    print("  run it from a machine with your Chrome (CDP) or a Russian residential IP.")

    # CDP-backed sources depend on the operator's Chrome being up and logged
    # in; probe that session directly so a dead browser is reported as itself,
    # not mistaken for five separate marketplace outages.
    cdp_note = ""
    try:
        from mcp_core.transport.chrome_cdp import probe_session

        probe = asyncio.run(probe_session())
        if probe.get("reachable"):
            cdp_note = (
                f"Chrome CDP: reachable on {probe['host']}:{probe['port']} ({probe.get('contexts', '?')} context(s))."
            )
        else:
            cdp_note = f"Chrome CDP: NOT reachable — {probe.get('reason')}. Avito/Taobao/Megamarket/Lamoda-search/DNS/Citilink need it."
    except Exception as exc:
        cdp_note = f"Chrome CDP: probe failed ({type(exc).__name__})."
    print(f"\n  {cdp_note}")

    if status_file:
        import datetime
        import pathlib
        import tempfile

        report = {
            "checked_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "healthy": healthy,
            "blocked": blocked,
            "drifted": drifted,
            "sources": {name: {"status": status, "detail": detail} for name, status, detail in results},
        }
        target = pathlib.Path(status_file)
        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=target.parent, delete=False, suffix=".tmp", encoding="utf-8"
            ) as fh:
                json.dump(report, fh, indent=2, ensure_ascii=False)
                tmp = fh.name
            pathlib.Path(tmp).replace(target)
            print(f"\n  status written to {target}")
        except OSError as exc:
            print(f"\n  could not write status file {target}: {exc}", file=sys.stderr)
            return 3

    # Exit codes are what a cron job or CI step actually reads, so they have to
    # separate the three outcomes an operator responds to differently:
    #   0 — everything that could be checked is healthy.
    #   1 — a parser drifted. Someone has to go look; this is the alarm.
    #   2 — nothing drifted but a source could not be judged (blocked, no CDP,
    #       wrong country). Not an alarm, not a clean bill of health either.
    # Collapsing 2 into 0 is how "all good" starts meaning "we checked nothing".
    if drifted:
        return 1
    if blocked:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__ or "marketplace-mcp CLI")
        return 0
    command, rest = args[0], args[1:]
    if command == "install":
        return cmd_install(rest)
    if command == "doctor":
        return cmd_doctor(rest)
    print(f"unknown command {command!r}; expected install or doctor", file=sys.stderr)
    log_event("marketplace.cli.unknown", command=command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
