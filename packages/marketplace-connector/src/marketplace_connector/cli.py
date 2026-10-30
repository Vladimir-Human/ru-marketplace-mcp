"""Operator CLI for the marketplace connectors.

Three verbs cover the three moments an operator actually needs help with:

``marketplace-mcp install``
    Write the client config. Editing ``claude_desktop_config.json`` by hand is
    where every setup mistake lives (wrong slashes, a stale path, a missing
    comma), so the CLI prints the exact JSON block to paste — or, with
    ``--write``, merges it into the client's own file (backup first).

``marketplace-mcp doctor``
    Run every connector's selfcheck and report per-source status. This is the
    answer to "is it broken, or is it me": a source blocked from your network
    shows as inconclusive with the reason and one fixed next step, a drifted
    parser shows as drift.

``marketplace-mcp browser status`` / ``marketplace-mcp browser show``
    Look at the operator's scraping Chrome and bring its window back. On
    Windows it starts off-screen by design, so a human cannot log in or pass a
    site's check without moving the window first.

``marketplace-mcp --help``
    A short usage screen: the verbs, one example each, and where to read more.
"""
# START_MODULE_CONTRACT
# PURPOSE: Operator CLI — write or print a client config, show usage, report per-source health with one next step, inspect the scraping Chrome.
# SCOPE: install (claude, claude-code, cursor, dsh), doctor (selfchecks + optional status file), browser (status, show), help.
# DEPENDS: mcp_core.logging; mcp_core.source_selection; mcp_core.transport.chrome_cdp (browser verbs); local capabilities (static source table).
# LINKS: M-MARKETPLACE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _config_block: Today's per-source mcpServers block (kept for --per-source).
# _unified_entry: The one "marketplace" entry that runs the unified server, plus the note for the operator.
# _config_path: Where a client's mcpServers file lives on this platform.
# _write_client_config: Merge entries into mcpServers; refuse malformed or conflicting files.
# next_step: One fixed next-step string for a source that is not ok.
# cmd_browser: browser status and browser show — the operator's view of the scraping Chrome.
# cmd_install / cmd_doctor / main: The verbs.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import shutil
import sys
import tempfile
from collections.abc import Mapping
from typing import Any

from mcp_core.logging import log_event
from mcp_core.source_selection import canonical

from marketplace_connector.capabilities import CAPABILITIES, browser_backed

# Clients whose config format this block is known to fit. A typo like
# "cursour" should say so rather than silently printing a Claude block.
# dsh has its own emitter below: the harness patch format is not mcpServers JSON.
KNOWN_CLIENTS = {"claude", "claude-code", "cursor", "dsh"}

# Gate variable for the emitted dsh rows. It doubles as the checkout path used
# by `uv run --directory`, so one user action both enables and locates a server.
DSH_ENV_DIR = "RU_MARKETPLACE_MCP_DIR"
DSH_ENV_FULL = "RU_MARKETPLACE_MCP_FULL"
DSH_ENV_DECISION = "RU_MARKETPLACE_MCP_DECISION"

# The default entry: one server, one name. The unified server mounts every
# connector, so a client that needs a dozen entries needs a dozen tools the
# agent pays for on every request.
UNIFIED_ENTRY = "marketplace"
UNIFIED_SCRIPT = "marketplace-mcp"

# (config key, console script, human note)
SERVERS: list[tuple[str, str, str]] = [
    ("wildberries", "wb-mcp", "anonymous HTTP — availability depends on your network"),
    ("ozon", "ozon-mcp", "needs your Chrome for tier 2 — see docs/CDP_SETUP.md"),
    ("yandex-market", "yandex-mcp", "anonymous HTTP"),
    ("detsky-mir", "detmir-mcp", "anonymous HTTP"),
    ("avito", "avito-mcp", "needs your Chrome — IP firewall"),
    ("taobao", "taobao-mcp", "needs your Chrome — signed API"),
    ("megamarket", "megamarket-mcp", "needs your Chrome — ServicePipe"),
    ("lamoda", "lamoda-mcp", "cards anonymous; search needs your Chrome"),
    ("dns", "dns-mcp", "needs your Chrome — Qrator proof-of-work"),
    ("citilink", "citilink-mcp", "needs your Chrome — Qrator"),
    ("aliexpress", "aliexpress-mcp", "needs your Chrome — AliExpress Russia"),
    ("cian", "cian-mcp", "real estate; needs your Chrome — WAF by IP"),
    ("compare-prices", "compare-mcp", "fans out across all of the above"),
    ("mpstats", "mpstats-mcp", "optional paid analytics — requires MPSTATS_MP_AUTH in the server environment"),
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
    ("aliexpress", "aliexpress_connector.server", "aliexpress_selfcheck"),
    ("cian", "cian_connector.server", "cian_selfcheck"),
    ("mpstats", "mpstats_connector.server", "mpstats_selfcheck"),
]

# The usage screen. Short on purpose: it is read in a terminal, and the
# details live in docs/QUICKSTART.md.
USAGE = """marketplace-mcp — Operator CLI and unified server for the marketplace connectors.

Usage:
  marketplace-mcp                  start the unified MCP server (stdio)
  marketplace-mcp install <client> write the client config (claude, claude-code, cursor, dsh)
  marketplace-mcp doctor [source]  run every connector's selfcheck and report health
  marketplace-mcp browser status   check the scraping Chrome and where its window is
  marketplace-mcp browser show     bring the scraping Chrome window on-screen
  marketplace-mcp help             show this text

Examples:
  marketplace-mcp
  marketplace-mcp install claude --write
  marketplace-mcp doctor wildberries
  marketplace-mcp browser status
  marketplace-mcp help

Read more: docs/QUICKSTART.md"""

# Next-step strings. Fixed text, one per situation: the operator gets one
# instruction, not a menu of possibilities, and never advice about captcha
# solvers, stealth/fingerprint tooling, proxy pools or retry loops.
NEXT_STEP_CDP_DOWN = (
    "Start the scraping Chrome: `scripts/start_chrome_cdp.ps1` (Windows) or "
    "`scripts/start_chrome_cdp.sh`, or let the connector start it on its first browser call."
)
NEXT_STEP_BROWSER_WALL = (
    "Open the scraping Chrome window, pass the site's check or log in by hand, then rerun: "
    "`marketplace-mcp doctor {source}`. On Windows the window starts off-screen; "
    "`marketplace-mcp browser show` brings it on-screen."
)
# The one instruction that starts the operator's Chrome. Fixed text: the
# operator gets one command, not a menu of ways to start a browser.
START_CHROME_HINT = (
    "Start the scraping Chrome: `scripts/start_chrome_cdp.ps1 -Visible` (Windows) or "
    "`scripts/start_chrome_cdp.sh` (macOS/Linux)."
)
NEXT_STEP_WB_TRANSPORT = (
    "WB's API refuses this address; set `WB_TRANSPORT=dom` to read WB pages through the scraping Chrome."
)
NEXT_STEP_MPSTATS_AUTH = "Set `MPSTATS_MP_AUTH` in the MCP server's environment (your MPStats token)."
NEXT_STEP_DRIFT = (
    "Parser drift: run `uv run python scripts/diagnose_drift.py {source}` and open an issue with its output."
)
# The diagnose script only knows a few sources: its argparse refuses every
# other name, so advising it for a source it cannot probe sends the operator
# to a command that fails before it starts. Those sources get the issue
# advice instead — the drift report is what the issue needs.
NEXT_STEP_DRIFT_ISSUE = (
    "Parser drift: rerun `marketplace-mcp doctor {source} --status-file drift.json` and open an issue at "
    "https://github.com/Vladimir-Human/ru-marketplace-mcp/issues with that file."
)
NEXT_STEP_ANTIBOT = "See `docs/ANTI_BOT.md` for this source."
# A rate limit is not a wall: the site asked us to slow down, and the fix is
# to wait it out — a browser cannot do that for the operator.
NEXT_STEP_RATE_LIMITED = (
    "The site rate-limited this address; wait 10-15 minutes, then rerun `marketplace-mcp doctor {source}`."
)
# The selfcheck raised: the site never got to answer, so the advice cannot be
# about the site. The error above is what the issue needs.
NEXT_STEP_SELFCHECK_ERROR = (
    "The selfcheck itself failed (error above); open an issue at "
    "https://github.com/Vladimir-Human/ru-marketplace-mcp/issues with this output."
)

# The sources scripts/diagnose_drift.py can actually probe: the keys of its
# SOURCES table plus its API_SOURCES. Everything else (wildberries, ozon,
# yandex_market, detsky_mir, avito, aliexpress, cian, mpstats) is rejected by
# that script's argparse, so it must not be advertised as the way to diagnose
# them. A test parses the script and keeps this set honest.
DIAGNOSE_DRIFT_SOURCES = frozenset({"citilink", "dns", "lamoda", "taobao", "megamarket"})

# The reason codes the connectors really emit for "the browser refused us"
# or "the site wants a human". These are the selfcheck `reason` values, not
# the error taxonomy: blocked (Avito, Ozon, DNS, Citilink, AliExpress, Cian,
# Lamoda, Taobao challenge), login_wall (Taobao), not_authenticated
# (Megamarket). A rate limit is NOT here — waiting IS the fix for it, and
# waiting is not something the browser can do for the operator.
WALL_REASONS = frozenset({"blocked", "login_wall", "not_authenticated", "challenge_required"})


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


def _unified_entry() -> tuple[dict[str, Any], str]:
    """The one entry that runs the unified server, plus the note for the operator.

    From a source checkout the honest command is ``uv run --directory <root>
    marketplace-mcp``, and ``uv`` is resolved to an absolute path: a desktop
    client often does not inherit the terminal's PATH, and a bare ``uv`` is
    then a server that silently never starts. From a wheel install there is
    no checkout, so the console script on PATH is the command.

    The note is returned, not printed: the caller shows it to the operator
    once per command run, on stderr. A warning printed from inside a helper
    fires once per call, and this one is called by every install path.
    """
    root = _workspace_root()
    if root is not None:
        note = f"# Paths point at this checkout: {root}"
        uv = shutil.which("uv")
        if uv is None:
            note += (
                "\nuv was not found on PATH: the client may not be able to start the server — "
                "install uv or point the command at it by hand."
            )
        return {"command": uv or "uv", "args": ["run", "--directory", str(root), UNIFIED_SCRIPT]}, note

    resolved = shutil.which(UNIFIED_SCRIPT)
    note = "# Installed as a package: the command is the console script on PATH."
    if resolved is None:
        note += f"\n# Not found on PATH, left unresolved: {UNIFIED_SCRIPT}"
    return {"command": resolved or UNIFIED_SCRIPT, "args": []}, note


def _config_path(client: str) -> pathlib.Path:
    """Where the client's mcpServers file lives on this platform.

    Claude Desktop: ``%APPDATA%/Claude`` (Windows), ``~/Library/Application
    Support/Claude`` (macOS), ``~/.config/Claude`` (Linux). Cursor:
    ``~/.cursor/mcp.json``.

    ``MARKETPLACE_MCP_CLAUDE_CONFIG`` / ``MARKETPLACE_MCP_CURSOR_CONFIG``
    override the location, so an operator with an unusual install — and the
    offline tests — can point the writer at a real file.
    """
    override = os.environ.get(f"MARKETPLACE_MCP_{client.upper()}_CONFIG")
    if override:
        return pathlib.Path(override).expanduser()
    home = pathlib.Path.home()
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = pathlib.Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _parse_strict_json(raw: str) -> Any:
    """``json.loads`` that also refuses NaN/Infinity, which are not JSON."""

    def _reject(constant: str) -> Any:
        raise ValueError(f"{constant} is not valid JSON")

    return json.loads(raw, parse_constant=_reject)


def _atomic_write_json(path: pathlib.Path, document: Any) -> None:
    """Write JSON through a temporary file in the same folder, then replace.

    A half-written client config is a broken client: the write must land whole
    or not at all, and the replacement has to happen on the same filesystem
    for ``os.replace`` to be atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            tmp_name = fh.name
        os.replace(tmp_name, path)
    except BaseException:
        if tmp_name:
            pathlib.Path(tmp_name).unlink(missing_ok=True)
        raise


def _quote(part: str) -> str:
    """Quote a command fragment that carries spaces or quotes."""
    if not part:
        return '""'
    if any(character.isspace() for character in part) or '"' in part:
        return '"' + part.replace('"', '\\"') + '"'
    return part


def _claude_mcp_add_line(name: str, entry: Mapping[str, Any]) -> str:
    """The exact ``claude mcp add`` command line for one entry.

    Claude Code keeps its servers in its own store, not in an mcpServers file,
    so the write for this client is a command the operator runs.
    """
    parts = ["claude", "mcp", "add", name, "--scope", "user", "--", str(entry.get("command", ""))]
    parts.extend(str(arg) for arg in entry.get("args", []) or [])
    return " ".join(_quote(part) for part in parts)


def _write_client_config(client: str, entries: dict[str, Any], *, force: bool, dry_run: bool) -> int:
    """Merge the entries into the client's own mcpServers file.

    The file belongs to the operator: other servers and other settings stay
    untouched, a malformed file is never rewritten, and every change is backed
    up before it happens.
    """
    path = _config_path(client)
    document: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = _parse_strict_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"{path} is not valid JSON ({exc}); refusing to change it", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"could not read {path}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(parsed, dict):
            print(f"{path} is not a JSON object; refusing to change it", file=sys.stderr)
            return 2
        document = parsed

    servers = document.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        print(f"{path} has a non-object 'mcpServers'; refusing to change it", file=sys.stderr)
        return 2

    conflicts = sorted(name for name, entry in entries.items() if name in servers and servers[name] != entry)
    if conflicts and not force:
        print(
            f"{path} already has a different {', '.join(conflicts)} entry; re-run with --force to replace it",
            file=sys.stderr,
        )
        return 2

    merged = dict(document)
    merged["mcpServers"] = {**servers, **entries}
    if dry_run:
        # Show the result of the merge, whether or not it would change
        # anything: the operator asked to see the file, not to be told "fine".
        print(json.dumps(merged, indent=2, ensure_ascii=False))
        print(f"target: {path}")
        return 0
    if all(servers.get(name) == entry for name, entry in entries.items()):
        print(f"already configured in {path}; nothing to write")
        return 0

    backup: pathlib.Path | None = None
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        # No backup, no change: the old file stays exactly as it was.
        try:
            shutil.copy2(path, backup)
        except OSError as exc:
            print(f"could not back up {path} to {backup}: {exc}; nothing was changed", file=sys.stderr)
            return 3
    try:
        _atomic_write_json(path, merged)
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return 3
    print(f"wrote {path}")
    if backup is not None:
        print(f"backup: {backup}")
    print(f"restart {client} to load it")
    return 0


def _yaml_double(value: str) -> str:
    """Quote a scalar for double-quoted YAML (backslashes and quotes escaped)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dsh_row(catalog_id: str, command: str, disabled: str, args_lines: list[str]) -> str:
    """One ``dsh-mcp-client`` row for ``cordis.patch.yml``.

    The output is already indented as the second item of a top-level
    ``- insert:`` patch list, matching FINAL-SPEC's bundle layout.
    """
    lines = [
        f"    - id: {catalog_id}",
        "      name: '@deepseek-ai/dsh-mcp-client'",
        f'      disabled: !!js "{disabled}"',
        "      config:",
        "        serverName: rumarket",
        "        transport: stdio",
        f"        command: {command}",
    ]
    if args_lines:
        lines.append("        args:")
        lines.extend(args_lines)
    else:
        lines.append("        args: []")
    lines.append("        failOnStartupError: false")
    return "\n".join(lines)


def _dsh_command(script: str, root: pathlib.Path | None) -> tuple[str, list[str], str | None]:
    """Command fragments for one emitted dsh row.

    From a source checkout, ``uv run --frozen --directory`` is the supported
    native start and the gate variable is the directory value. From a wheel,
    the console script on PATH is the command and the gate variable is only the
    enable switch (any value works).
    """
    if root is not None:
        args = [
            "          - run",
            "          - --frozen",
            "          - --directory",
            f"          - !!js \"process.env.{DSH_ENV_DIR} || '.'\"",
            f"          - {script}",
        ]
        return '"uv"', args, None

    resolved = shutil.which(script) or script
    note = f"#   - console script {script}: resolved to {resolved}"
    if shutil.which(script) is None:
        note += " (NOT FOUND on PATH, left unresolved)"
    return _yaml_double(resolved), [], note


def _dsh_patch_block() -> tuple[str, str]:
    """The dsh ``cordis.patch.yml`` rows for the three supported mounts.

    Unlike Claude/Cursor, DeepSeek Harness does not consume an ``mcpServers``
    JSON block: a plugin patch inserts ``@deepseek-ai/dsh-mcp-client`` rows.
    All rows ship disabled at zero context cost until the operator opts in
    (measured cost of the enabled rows is paid on EVERY client request).

    The three rows deliberately share one ``serverName``. Their ``disabled``
    expressions are mutually exclusive, so only one instance is ever alive:
    ``RU_MARKETPLACE_MCP_FULL`` selects the unified row, otherwise
    ``RU_MARKETPLACE_MCP_DECISION`` selects the middle row, and with neither
    switch set the cheap ``compare-mcp`` row is selected.
    """
    root = _workspace_root()
    compare_cmd, compare_args, compare_note = _dsh_command("compare-mcp", root)
    compare_row = _dsh_row(
        "ru-marketplace-compare",
        compare_cmd,
        f"!process.env.{DSH_ENV_DIR} || !!process.env.{DSH_ENV_FULL} || !!process.env.{DSH_ENV_DECISION}",
        compare_args,
    )
    decision_cmd, decision_args, decision_note = _dsh_command("decision-mcp", root)
    decision_row = _dsh_row(
        "ru-marketplace-decision",
        decision_cmd,
        f"!process.env.{DSH_ENV_DIR} || !!process.env.{DSH_ENV_FULL} || !process.env.{DSH_ENV_DECISION}",
        decision_args,
    )
    full_cmd, full_args, full_note = _dsh_command("marketplace-mcp", root)
    full_row = _dsh_row(
        "ru-marketplace-full",
        full_cmd,
        f"!process.env.{DSH_ENV_DIR} || !process.env.{DSH_ENV_FULL}",
        full_args,
    )

    block = (
        "# Add these rows to the `- insert:` list of your cordis.patch.yml (dsh).\n"
        "# All rows start disabled (zero tool-schema cost until the env gates are\n"
        "# set) and their conditions are mutually exclusive.\n"
        "- insert:\n" + compare_row + "\n" + decision_row + "\n" + full_row
    )

    if root is not None:
        note = (
            f"# Set {DSH_ENV_DIR} to this checkout (or another clone): {root}\n"
            f"# Decision set {DSH_ENV_DECISION}=1 for comparison + shortlist card inspection; full set {DSH_ENV_FULL}=1."
        )
    else:
        lines = [
            "# Installed as a package: commands are the console scripts on PATH, and",
            f"# {DSH_ENV_DIR} is only the enable switch (set it to any value).",
            f"# Decision set {DSH_ENV_DECISION}=1 for comparison + shortlist card inspection; full set {DSH_ENV_FULL}=1.",
        ]
        if compare_note:
            lines.append(compare_note)
        if decision_note:
            lines.append(decision_note)
        if full_note:
            lines.append(full_note)
        note = "\n".join(lines)
    return block, note


def cmd_install(argv: list[str]) -> int:
    """Print the client config block, or write it into the client's own file.

    The default is one entry running the unified server. ``--per-source`` keeps
    the old per-source block. ``--write`` merges the block into the client's
    own configuration file, with a backup of the old one first.
    """
    write = dry_run = force = per_source = False
    client: str | None = None
    for arg in argv:
        if arg == "--write":
            write = True
        elif arg == "--dry-run":
            dry_run = True
        elif arg == "--force":
            force = True
        elif arg == "--per-source":
            per_source = True
        elif arg.startswith("-"):
            print(
                f"unknown install option {arg!r}; expected --write, --dry-run, --force or --per-source",
                file=sys.stderr,
            )
            return 2
        elif client is None:
            client = arg
        else:
            print("install accepts at most one client name", file=sys.stderr)
            return 2
    client = client or "claude"
    if client not in KNOWN_CLIENTS:
        print(
            f"unknown client {client!r}; expected one of {', '.join(sorted(KNOWN_CLIENTS))}",
            file=sys.stderr,
        )
        return 2
    if dry_run and not write:
        print("--dry-run only makes sense together with --write", file=sys.stderr)
        return 2
    if force and not write:
        print("--force only makes sense together with --write", file=sys.stderr)
        return 2

    if client == "dsh":
        if write:
            print(
                "dsh has no mcpServers file: install the plugin bundle instead — see dsh/README.md",
                file=sys.stderr,
            )
            return 2
        patch_block, patch_note = _dsh_patch_block()
        print(patch_block)
        print()
        print(patch_note)
        return 0

    if per_source:
        entries, note = _config_block()
    else:
        entry, note = _unified_entry()
        entries = {UNIFIED_ENTRY: entry}
    # The note is operator information (where the commands point, what is
    # missing from PATH), not part of what gets pasted or written: stderr,
    # once per command run, in every mode.
    print(note, file=sys.stderr)

    if client == "claude-code":
        if write:
            for name, entry in entries.items():
                print(_claude_mcp_add_line(name, entry))
            return 0
    elif write:
        return _write_client_config(client, entries, force=force, dry_run=dry_run)

    print(f"# Add to your mcpServers block ({client}).")
    print(json.dumps(entries, indent=2, ensure_ascii=False))
    print()
    print("# Notes:")
    if per_source:
        for _, _, note_line in SERVERS:
            print(f"#   - {note_line}")
        print()
        print("# Or wire one entry: the unified 'marketplace-mcp' server mounts every source.")
    else:
        print("#   - The unified server mounts every source; tool names stay the same (wb_*, ozon_*).")
        print("#   - Some sources need your Chrome (CDP) — see docs/CDP_SETUP.md.")
        print("#   - mpstats is optional paid analytics — requires MPSTATS_MP_AUTH in the server environment.")
        print()
        print("# Need one entry per source instead? Add --per-source.")
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
    # Some canaries (notably Yandex) carry the diagnosis in `detail` or
    # `notes` instead of `reason`. Keep that explanation for non-healthy
    # states: a bare "inconclusive" loses the operator's next action.
    if not reason and state != "healthy":
        detail = _attr(check, "detail", None)
        if isinstance(detail, str) and detail.strip():
            reason = detail
        else:
            notes = _attr(check, "notes", None)
            if isinstance(notes, list):
                reason = "; ".join(note.strip() for note in notes if isinstance(note, str) and note.strip())
    if not reason:
        return str(state)
    reason = " ".join(str(reason).split())
    if len(reason) > 240:
        reason = reason[:237] + "..."
    suffix = f" http {code}" if code else ""
    return f"{state} ({reason}{suffix})"


def _attr(obj: object, key: str, default: object = None) -> object:
    """Sub-checks arrive as dicts from some connectors and models from others."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _run_one_selfcheck(name: str, module_path: str, tool_name: str) -> tuple[str, str, str, dict[str, Any]]:
    """Run one connector's selfcheck.

    Returns (name, status, detail, checks). The per-check reasons travel with
    the result: the next step is chosen from them, and a rendered string
    cannot be told apart from a reason code.
    """
    try:
        module = __import__(module_path, fromlist=[tool_name])
        tool = getattr(module, tool_name)
        result = await tool()
        status = str(_attr(result, "status", "unknown"))
        checks = _attr(result, "checks", {}) or {}
        if not isinstance(checks, dict):
            raise TypeError("selfcheck checks must be a mapping")
        detail = ", ".join(f"{k}:{_check_detail(v)}" for k, v in checks.items()) or status
        return name, status, detail, dict(checks)
    except Exception as exc:
        return name, "error", f"{type(exc).__name__}: {str(exc)[:120]}", {}


def _wb_api_check_failed(checks: Mapping[str, Any] | None) -> bool:
    """True when WB's own API checks (card / search_v9) were refused."""
    for key in ("card", "search_v9"):
        entry = (checks or {}).get(key)
        if entry is not None and str(_attr(entry, "reason", "")) == "transport_down":
            return True
    return False


def next_step(
    source: str,
    status: str,
    checks: Mapping[str, Any] | None = None,
    *,
    cdp_reachable: bool | None = None,
    wb_transport: str = "api",
) -> str | None:
    """One fixed next step for a source that is not ok; None when it is ok.

    Pure on purpose: the same selfcheck result always yields the same advice,
    so the mapping can be tested offline and cannot change with the run. The
    inputs are the source's own verdict (status plus per-check reasons), the
    static capability table, the CDP probe result and the WB transport mode.

    The advice is always one of a fixed set of strings. It never suggests a
    captcha solver, stealth or fingerprint tooling, a proxy pool or a retry
    loop: this project reads public pages and asks the operator to act.
    """
    if status == "success":
        return None
    if status == "error":
        # The selfcheck itself raised, so the site never got to answer: there
        # is no verdict to act on, and anti-bot or browser advice would send
        # the operator after the wrong thing entirely.
        return NEXT_STEP_SELFCHECK_ERROR

    entries = list((checks or {}).values())
    reasons = {str(_attr(entry, "reason", "") or "") for entry in entries}
    states = {str(_attr(entry, "state", "") or "") for entry in entries}
    capability = CAPABILITIES.get(source, {})
    # WB in DOM mode reads pages through the scraping Chrome even though its
    # static tier is anonymous HTTP.
    uses_browser = browser_backed(capability) or (source == "wildberries" and wb_transport == "dom")

    if status == "drift_detected" or "drift" in states:
        if source in DIAGNOSE_DRIFT_SOURCES:
            return NEXT_STEP_DRIFT.format(source=source)
        return NEXT_STEP_DRIFT_ISSUE.format(source=source)
    if "auth_missing" in reasons:
        return NEXT_STEP_MPSTATS_AUTH
    if source == "wildberries" and wb_transport != "dom" and _wb_api_check_failed(checks):
        return NEXT_STEP_WB_TRANSPORT
    # A rate limit is the site's own answer about this address, so the
    # browser is not what failed: it comes before the CDP and wall cases,
    # and the fix is to wait.
    if "rate_limited" in reasons:
        return NEXT_STEP_RATE_LIMITED.format(source=source)
    if uses_browser and cdp_reachable is False:
        return NEXT_STEP_CDP_DOWN
    if uses_browser and reasons & WALL_REASONS:
        return NEXT_STEP_BROWSER_WALL.format(source=source)
    return NEXT_STEP_ANTIBOT


def _probe_cdp() -> tuple[bool | None, str]:
    """Probe the operator's Chrome.

    Returns (reachable, note). ``None`` means "could not tell" — the probe
    itself failed — and must not be read as "the browser is down".
    """
    try:
        from mcp_core.transport.chrome_cdp import probe_session

        probe = asyncio.run(probe_session())
        if probe.get("reachable"):
            host = probe.get("host", "?")
            port = probe.get("port", "?")
            return True, (f"Chrome CDP: reachable on {host}:{port} ({probe.get('contexts', '?')} context(s)).")
        return False, (
            f"Chrome CDP: NOT reachable — {probe.get('reason')}. "
            "Avito/Taobao/Megamarket/Lamoda-search/DNS/Citilink/AliExpress/Cian need it."
        )
    except Exception as exc:
        return None, f"Chrome CDP: probe failed ({type(exc).__name__})."


def cmd_doctor(argv: list[str]) -> int:
    """Run every selfcheck and print a per-source health table.

    ``--status-file PATH`` also writes the machine-readable report: a JSON
    snapshot with per-source status, the next step and a timestamp, so a cron
    job or dashboard can watch drift without parsing the human table. The file
    is replaced atomically, never appended to — a monitoring reader always
    gets one complete snapshot.
    """
    status_file: str | None = None
    sources: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--status-file":
            if status_file is not None:
                print("--status-file may only be supplied once", file=sys.stderr)
                return 2
            status_file = next(it, None)
            if not status_file or status_file.startswith("-"):
                print("--status-file requires a path", file=sys.stderr)
                return 2
        elif arg.startswith("-"):
            print(f"unknown doctor option {arg!r}", file=sys.stderr)
            return 2
        else:
            sources.append(canonical(arg))
    only = set(sources) if sources else None
    known_sources = {name for name, _, _ in _SELFCHECKS}
    unknown = (only or set()) - known_sources
    if unknown:
        print(
            f"unknown source(s): {', '.join(sorted(unknown))}; expected one of {', '.join(sorted(known_sources))}",
            file=sys.stderr,
        )
        return 2
    results = []
    for name, module_path, tool_name in _SELFCHECKS:
        if only and name not in only:
            continue
        results.append(asyncio.run(_run_one_selfcheck(name, module_path, tool_name)))

    # CDP reachability decides two different next steps ("start the browser"
    # vs "the browser was refused"), so it is probed before the table is
    # printed rather than after it.
    cdp_reachable, cdp_note = _probe_cdp()
    wb_transport = os.environ.get("WB_TRANSPORT", "api")
    steps = {
        name: next_step(name, status, checks, cdp_reachable=cdp_reachable, wb_transport=wb_transport)
        for name, status, _, checks in results
    }

    print("Marketplace connector health (selfcheck):")
    healthy = blocked = drifted = 0
    for name, status, detail, _ in results:
        marker = {"success": "ok ", "drift_detected": "DRF", "inconclusive": "blk"}.get(status, "err")
        if status == "success":
            healthy += 1
        elif status == "drift_detected":
            drifted += 1
        else:
            blocked += 1
        print(f"  {marker} {name:<14} {status:<15} {detail}")
        step = steps.get(name)
        if step:
            print(f"      next: {step}")
    print(f"\n  {healthy} healthy, {blocked} blocked/inconclusive, {drifted} drifted.")
    print("  Every blocked row carries its own next step; 'inconclusive' means the source")
    print("  could not be judged — the parser was never proven broken.")

    print(f"\n  {cdp_note}")

    if status_file:
        report = {
            "checked_at": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "healthy": healthy,
            "blocked": blocked,
            "drifted": drifted,
            "sources": {
                name: {"status": status, "detail": detail, "next_step": steps.get(name)}
                for name, status, detail, _ in results
            },
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


def cmd_browser(argv: list[str]) -> int:
    """``browser status`` and ``browser show`` — the operator's view of the Chrome.

    ``status`` says whether the CDP endpoint answers, which Chrome it is and
    where its window is; ``show`` brings that window back on-screen so a human
    can log in or pass a site's check. Neither sends a request to any website.
    """
    if not argv:
        print("browser needs a verb: status or show", file=sys.stderr)
        return 2
    verb = argv[0]
    if len(argv) > 1:
        print(f"browser {verb} takes no arguments", file=sys.stderr)
        return 2
    if verb == "status":
        return _browser_status()
    if verb == "show":
        return _browser_show()
    print(f"unknown browser verb {verb!r}; expected status or show", file=sys.stderr)
    return 2


def _browser_status() -> int:
    """Print what the operator's scraping Chrome looks like right now."""
    from mcp_core.transport.chrome_cdp import browser_status

    report = asyncio.run(browser_status())
    endpoint = f"{report.get('host', '?')}:{report.get('port', '?')}"
    if not report.get("reachable"):
        print(f"CDP endpoint: not reachable on {endpoint}")
        print(START_CHROME_HINT)
        return 1
    pages = report.get("page_targets")
    print(f"CDP endpoint: reachable on {endpoint}")
    print(f"Chrome: {report.get('version') or 'unknown'}")
    print(f"Page targets: {pages if pages is not None else 'unknown'}")
    print(f"Window: {report.get('window', 'unknown')}")
    if report.get("reason"):
        print(f"Note: {report['reason']}")
    return 0


def _browser_show() -> int:
    """Bring the scraping Chrome window on-screen and say what was done."""
    from mcp_core.transport.chrome_cdp import show_browser_window

    result = asyncio.run(show_browser_window())
    if not result.get("ok"):
        print(f"could not show the scraping Chrome window: {result.get('reason', 'unknown reason')}", file=sys.stderr)
        if not result.get("reachable"):
            print(START_CHROME_HINT, file=sys.stderr)
        return 1
    bounds = result.get("bounds", {})
    print(
        f"Chrome window is on-screen at {bounds.get('left')},{bounds.get('top')} "
        f"({bounds.get('width')}x{bounds.get('height')}) and its first tab is active."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if not args:
        # The console script starts the server for a bare `marketplace-mcp`
        # (see __main__.py); reaching the CLI with no verb means the operator
        # asked for help.
        print(USAGE)
        return 0
    command, rest = args[0], args[1:]
    if command == "install":
        return cmd_install(rest)
    if command == "doctor":
        return cmd_doctor(rest)
    if command == "browser":
        return cmd_browser(rest)
    print(f"unknown command {command!r}; expected install, doctor or browser", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    log_event("marketplace.cli.unknown", command=command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
