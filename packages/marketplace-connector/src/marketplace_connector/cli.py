"""Operator CLI for the marketplace connectors.

The verbs cover the moments an operator actually needs help with:

``marketplace-mcp setup``
    From zero to the first answer: detect a client, write its config with the
    compare profile by default, start the scraping Chrome, run doctor's
    selfchecks and offer ``unblock`` for a source the site walls off.

``marketplace-mcp install``
    Write the client config. Editing ``claude_desktop_config.json`` by hand is
    where every setup mistake lives (wrong slashes, a stale path, a missing
    comma), so the CLI prints the exact JSON block to paste — or, with
    ``--write``, merges it into the client's own file (backup first).
    ``--profile compare|decision|full`` picks which server the one
    ``marketplace`` entry runs.

``marketplace-mcp doctor``
    Run every connector's selfcheck and report per-source status. This is the
    answer to "is it broken, or is it me": a source blocked from your network
    shows as inconclusive with the reason and one fixed next step, a drifted
    parser shows as drift.

``marketplace-mcp browser status`` / ``marketplace-mcp browser show``
    Look at the operator's scraping Chrome and bring its window back. On
    Windows the connector's autostart parks it off-screen by design, so a
    human cannot log in or pass a site's check without moving the window
    first. ``browser start`` launches it on-screen for exactly that.

``marketplace-mcp unblock [source ...]``
    Open the page a human needs for each browser-walled source, wait for the
    check or login, then re-run those selfchecks. It opens pages and reports;
    it never solves or automates a site's check.

``marketplace-mcp --help``
    A short usage screen: the verbs, one example each, and where to read more.
"""
# START_MODULE_CONTRACT
# PURPOSE: Operator CLI — write or print a client config, show usage, report per-source health with one next step, inspect or start the scraping Chrome, open unblock pages, and run the end-to-end setup flow.
# SCOPE: setup (detect clients, write configs, start Chrome, health, unblock offer), install (claude, claude-code, cursor, dsh; --profile compare|decision|full), doctor (selfchecks + optional status file), browser (status, show, start), unblock, help.
# DEPENDS: mcp_core.logging; mcp_core.source_selection; mcp_core.transport.chrome_cdp (browser verbs, setup); local capabilities (static source table); local unblock map.
# LINKS: M-MARKETPLACE; M-UNBLOCK; R-CLI-INSTALL-PROFILE; R-CLI-INSTALL-UVX; R-CLI-SETUP.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _config_block: Today's per-source mcpServers block (kept for --per-source).
# _uv_cached_entry / _unified_entry: The selected profile entry: checkout uv, a pinned uv tool release, or installed script.
# _config_path: Where a client's mcpServers file lives on this platform.
# _write_client_config: Merge entries into mcpServers; refuse malformed or conflicting files.
# next_step: One fixed next-step string for a source that is not ok.
# _run_selfchecks / _health_steps / _health_exit_code: The doctor pipeline, shared by doctor, unblock and setup.
# cmd_browser: browser status, browser show and browser start.
# cmd_unblock: Open the pages a human passes, then re-check those sources.
# _detected_clients / cmd_setup: Detect clients, write configs and walk an operator to the first answer.
# cmd_install / cmd_doctor / main: The verbs.
# END_MODULE_MAP

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from typing import Any

from mcp_core.logging import log_event
from mcp_core.source_selection import canonical

from marketplace_connector import __version__
from marketplace_connector.capabilities import CAPABILITIES, browser_backed
from marketplace_connector.unblock import UNBLOCK_PAGES

# Clients whose config format this block is known to fit. A typo like
# "cursour" should say so rather than silently printing a Claude block.
# dsh has its own emitter below: the harness patch format is not mcpServers JSON.
KNOWN_CLIENTS = {"claude", "claude-code", "cursor", "dsh"}

# Gate variable for the emitted dsh rows. It doubles as the checkout path used
# by `uv run --directory`, so one user action both enables and locates a server.
DSH_ENV_DIR = "RU_MARKETPLACE_MCP_DIR"
DSH_ENV_FULL = "RU_MARKETPLACE_MCP_FULL"
DSH_ENV_DECISION = "RU_MARKETPLACE_MCP_DECISION"

# The default entry: one server, one name. A profile selects which server the
# `marketplace` entry runs, because a client pays for the mounted tool schemas
# on every request: compare is 4 tools (~0.9k tokens), decision 5, full ~40
# tools (~13.6k). New installs start with the cheap, recommended compare mode.
UNIFIED_ENTRY = "marketplace"
UNIFIED_SCRIPT = "marketplace-mcp"
PROFILE_SCRIPTS: dict[str, str] = {
    "compare": "compare-mcp",
    "decision": "decision-mcp",
    "full": UNIFIED_SCRIPT,
}
DEFAULT_PROFILE = "compare"
PROFILE_DETAILS: dict[str, str] = {
    "compare": "4 comparison tools, about 0.9k tokens per request",
    "decision": "comparison plus shortlist card inspection, 5 tools",
    "full": "every per-source tool, about 40 tools and 13.6k tokens per request",
}

# Clients `setup` can wire. dsh has its own bundle format and is not one of them.
SETUP_CLIENTS: tuple[str, ...] = ("claude", "cursor", "claude-code")
CLIENT_LABELS: dict[str, str] = {
    "claude": "Claude Desktop",
    "cursor": "Cursor",
    "claude-code": "Claude Code",
}

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
  marketplace-mcp setup            wire a detected client and check the first sources
  marketplace-mcp install <client> write the client config (claude, claude-code, cursor, dsh)
                                   --profile compare|decision|full (default: compare)
  marketplace-mcp doctor [source]  run every connector's selfcheck and report health
  marketplace-mcp browser status   check the scraping Chrome and where its window is
  marketplace-mcp browser show     bring the scraping Chrome window on-screen
  marketplace-mcp browser start    launch the scraping Chrome on-screen (--hidden: off-screen)
  marketplace-mcp unblock [source] open the page a human passes, wait, then re-check
  marketplace-mcp help             show this text

Examples:
  marketplace-mcp setup
  marketplace-mcp install claude --write --profile full
  marketplace-mcp doctor wildberries
  marketplace-mcp browser start
  marketplace-mcp unblock ozon --dry-run
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
    "Run `marketplace-mcp unblock {source}`: it opens the page in the scraping Chrome, "
    "you pass the check or log in, and it re-checks."
)
# The one instruction that starts the operator's Chrome. Fixed text: the
# operator gets one command, not a menu of ways to start a browser.
START_CHROME_HINT = (
    "Start the scraping Chrome: `scripts/start_chrome_cdp.ps1` (Windows) or "
    "`scripts/start_chrome_cdp.sh` (macOS/Linux)."
)
NEXT_STEP_WB_TRANSPORT = (
    "WB's API refuses this address; set `WB_TRANSPORT=dom` to read WB pages through the scraping Chrome."
)
NEXT_STEP_WB_DOM_FALLBACK = (
    "WB's API refuses this address; `wb_card` and `wb_search` fall back to the scraping Chrome "
    "automatically. Set `WB_TRANSPORT=dom` to make doctor check that path too."
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

# Sources whose wall is a login rather than a bot check: the operator's action
# there is "log in", and the page in UNBLOCK_PAGES is the login or home page.
_LOGIN_UNBLOCK_SOURCES = frozenset({"taobao", "megamarket"})

# Seconds ``unblock`` waits for the human when stdin is not a terminal.
UNBLOCK_WAIT_DEFAULT_S = 180.0


def _browser_source_names() -> list[str]:
    """Doctor sources whose connector can use the operator's Chrome."""
    return [name for name, _, _ in _SELFCHECKS if browser_backed(CAPABILITIES.get(name))]


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


# START_BLOCK_CLIENT_ENTRY


def _uv_cached_entry(script: str) -> tuple[dict[str, Any], str] | None:
    """Use a durable pinned tool command only for an environment inside uv's cache."""
    uv_env = os.environ.get("UV")
    if not uv_env:
        return None
    uv_path = pathlib.Path(uv_env).expanduser()
    if not uv_path.is_absolute():
        resolved = shutil.which(uv_env)
        if resolved is None:
            return None
        uv_path = pathlib.Path(resolved)
    # Not resolve(): a Homebrew or pipx uv is a symlink to a versioned file that
    # the next upgrade deletes. When uv on PATH is the same file, keep that
    # stable spelling in the saved config.
    on_path = shutil.which("uv")
    if on_path is not None:
        try:
            if os.path.samefile(on_path, uv_path):
                uv_path = pathlib.Path(on_path)
        except OSError:
            pass
    uv_path = uv_path.absolute()
    cache = os.environ.get("UV_CACHE_DIR")
    if not cache:
        try:
            found = subprocess.run(
                [str(uv_path), "cache", "dir"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        cache = found.stdout.strip()
    if not cache:
        return None
    try:
        inside_cache = pathlib.Path(sys.prefix).resolve().is_relative_to(pathlib.Path(cache).expanduser().resolve())
    except (OSError, ValueError):
        return None
    if not inside_cache:
        return None
    release = f"ru-marketplace-mcp=={__version__}"
    entry = {"command": str(uv_path), "args": ["tool", "run", "--from", release, script]}
    note = (
        f"# This entry runs the pinned release {release} through uv, not a temporary cache script.\n"
        "# To move to a newer release, run "
        "`uv tool run --from ru-marketplace-mcp==<new-version> "
        "marketplace-mcp install <client> --write --force`."
    )
    return entry, note


def _unified_entry(script: str = UNIFIED_SCRIPT) -> tuple[dict[str, Any], str]:
    """The one entry that runs the selected profile's server, plus the note.

    From a source checkout the honest command is ``uv run --directory <root>
    <script>``, and ``uv`` is resolved to an absolute path: a desktop client
    often does not inherit the terminal's PATH, and a bare ``uv`` is then a
    server that silently never starts. From a wheel install there is no
    checkout. Inside uv's cache it runs the pinned package release through uv,
    rather than saving a console-script path that uv may later delete; an
    ordinary install keeps the console script on PATH.

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
        return {"command": uv or "uv", "args": ["run", "--directory", str(root), script]}, note

    cached_entry = _uv_cached_entry(script)
    if cached_entry is not None:
        return cached_entry

    resolved = shutil.which(script)
    note = "# Installed as a package: the command is the console script on PATH."
    if resolved is None:
        note += f"\n# Not found on PATH, left unresolved: {script}"
    return {"command": resolved or script, "args": []}, note


# END_BLOCK_CLIENT_ENTRY


def _profile_note(profile: str) -> str:
    """One line naming the profile, its cost and how to switch."""
    script = PROFILE_SCRIPTS[profile]
    switch = (
        "Use --profile compare for the cheap price-comparison server."
        if profile == "full"
        else "Use --profile full for every per-source tool."
    )
    return f"# Profile: {profile} ({script}) — {PROFILE_DETAILS[profile]}. {switch}"


def _entry_script(entry: Mapping[str, Any]) -> str | None:
    """The profile console script an mcpServers entry runs, if it is one of ours."""
    parts = [str(entry.get("command", ""))]
    parts.extend(str(arg) for arg in entry.get("args") or [])
    known = {script: profile for profile, script in PROFILE_SCRIPTS.items()}
    for part in reversed(parts):
        name = pathlib.PurePath(part).name.lower()
        for script in known:
            if name in (script, f"{script}.exe", f"{script}.cmd", f"{script}.bat"):
                return script
    return None


def _entry_profile(entry: Mapping[str, Any]) -> str | None:
    """The profile name of an mcpServers entry, or None for a foreign command."""
    script = _entry_script(entry)
    if script is None:
        return None
    return next(profile for profile, known in PROFILE_SCRIPTS.items() if known == script)


def _configured_profile(client: str) -> str | None:
    """The profile the client's existing ``marketplace`` entry runs, if any.

    The compare default is for new installs only (owner decision, 2026-10-30):
    re-running install or setup without ``--profile`` keeps what an operator
    already chose. Read-only; a missing or unreadable file is "no profile",
    and the write path reports such a file on its own.
    """
    if client not in ("claude", "cursor"):
        return None
    path = _config_path(client)
    try:
        document = _parse_strict_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    servers = document.get("mcpServers") if isinstance(document, dict) else None
    entry = servers.get(UNIFIED_ENTRY) if isinstance(servers, dict) else None
    return _entry_profile(entry) if isinstance(entry, dict) else None


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


def _write_client_config(
    client: str,
    entries: dict[str, Any],
    *,
    force: bool,
    dry_run: bool,
    profile: str | None = None,
    conflict_hint: str | None = None,
    show_document: bool = True,
) -> int:
    """Merge the entries into the client's own mcpServers file.

    The file belongs to the operator: other servers and other settings stay
    untouched, a malformed file is never rewritten, and every change is backed
    up before it happens. When ``profile`` is given, a conflict names the
    profile the existing entry runs and the one that was asked for. A dry run
    prints the merged file, or only the entries when ``show_document`` is
    false: ``setup`` must not echo other servers' settings (their ``env``
    blocks may hold keys) when the operator never asked to see the file.
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
        if profile is None:
            print(
                f"{path} already has a different {', '.join(conflicts)} entry; re-run with --force to replace it",
                file=sys.stderr,
            )
        else:
            details = []
            for name in conflicts:
                configured = _entry_profile(servers[name])
                if configured is None:
                    details.append(f"{name} has an unrecognized command")
                else:
                    details.append(f"{name} is configured as profile {configured} ({PROFILE_SCRIPTS[configured]})")
            print(
                f"{path} already has a different {', '.join(conflicts)} entry: "
                f"{'; '.join(details)}; asked for profile {profile} ({PROFILE_SCRIPTS[profile]}); "
                "re-run with --force to replace it",
                file=sys.stderr,
            )
        if conflict_hint is not None:
            print(conflict_hint, file=sys.stderr)
        return 2

    merged = dict(document)
    merged["mcpServers"] = {**servers, **entries}
    if dry_run:
        # Show the result of the merge, whether or not it would change
        # anything: the operator asked to see the file, not to be told "fine".
        shown = merged if show_document else {"mcpServers": entries}
        print(json.dumps(shown, indent=2, ensure_ascii=False))
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

    The default is one ``marketplace`` entry running the compare profile.
    ``--profile compare|decision|full`` selects the console script the entry
    runs; ``--per-source`` keeps the old per-source block and cannot be
    combined with a profile. ``--write`` merges the block into the client's
    own configuration file, with a backup of the old one first.
    """
    write = dry_run = force = per_source = False
    client: str | None = None
    profile: str | None = None
    it = iter(argv)
    for arg in it:
        if arg == "--write":
            write = True
        elif arg == "--dry-run":
            dry_run = True
        elif arg == "--force":
            force = True
        elif arg == "--per-source":
            per_source = True
        elif arg == "--profile":
            raw = next(it, None)
            if not raw or raw.startswith("-"):
                print("--profile requires one of compare, decision or full", file=sys.stderr)
                return 2
            profile = raw
        elif arg.startswith("-"):
            print(
                f"unknown install option {arg!r}; expected --write, --dry-run, --force, --per-source or --profile",
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
    if per_source and profile is not None:
        print(
            "--per-source writes one entry per source and cannot be combined with --profile",
            file=sys.stderr,
        )
        return 2
    chosen_profile = profile or DEFAULT_PROFILE
    if chosen_profile not in PROFILE_SCRIPTS:
        print(
            f"unknown profile {chosen_profile!r}; expected one of {', '.join(PROFILE_SCRIPTS)}",
            file=sys.stderr,
        )
        return 2

    if client == "dsh":
        if write:
            print(
                "dsh has no mcpServers file: install the plugin bundle instead — see dsh/README.md",
                file=sys.stderr,
            )
            return 2
        if profile is not None:
            print(
                "dsh emits all three rows (compare, decision, full); --profile does not apply",
                file=sys.stderr,
            )
        patch_block, patch_note = _dsh_patch_block()
        print(patch_block)
        print()
        print(patch_note)
        return 0

    if write and not per_source and profile is None:
        kept = _configured_profile(client)
        if kept is not None:
            chosen_profile = kept
            print(
                f"# {_config_path(client)} already runs profile {kept}; keeping it (--profile changes it).",
                file=sys.stderr,
            )

    if per_source:
        entries, note = _config_block()
    else:
        entry, note = _unified_entry(PROFILE_SCRIPTS[chosen_profile])
        entries = {UNIFIED_ENTRY: entry}
    # The note is operator information (where the commands point, what is
    # missing from PATH), not part of what gets pasted or written: stderr,
    # once per command run, in every mode.
    print(note, file=sys.stderr)
    if not per_source:
        print(_profile_note(chosen_profile))

    if client == "claude-code":
        if write:
            for name, entry in entries.items():
                print(_claude_mcp_add_line(name, entry))
            return 0
    elif write:
        return _write_client_config(
            client,
            entries,
            force=force,
            dry_run=dry_run,
            profile=None if per_source else chosen_profile,
        )

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
        print(f"#   - Profile {chosen_profile}: {PROFILE_DETAILS[chosen_profile]}.")
        print("#   - Some sources need your Chrome (CDP) — see docs/CDP_SETUP.md.")
        print("#   - mpstats is optional paid analytics — requires MPSTATS_MP_AUTH in the server environment.")
        print()
        print("# Need one entry per source instead? Add --per-source.")
        print("# Need another tool set? Add --profile decision or --profile full.")
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
    wb_dom_fallback: str = "1",
) -> str | None:
    """One fixed next step for a source that is not ok; None when it is ok.

    Pure on purpose: the same selfcheck result always yields the same advice,
    so the mapping can be tested offline and cannot change with the run. The
    inputs are the source's own verdict (status plus per-check reasons), the
    static capability table, the CDP probe result and the WB transport and
    DOM-fallback settings.

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
        # With the fallback on, the tools already read WB through the DOM path;
        # only an explicit opt-out needs the old "switch transport" instruction.
        if wb_dom_fallback.strip().lower() in {"0", "false", "no", "off"}:
            return NEXT_STEP_WB_TRANSPORT
        return NEXT_STEP_WB_DOM_FALLBACK
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


def _print_health_rows(
    results: list[tuple[str, str, str, dict[str, Any]]],
    steps: Mapping[str, str | None],
) -> tuple[int, int, int]:
    """Print the doctor rows and summary; return (healthy, blocked, drifted).

    Shared with ``unblock``'s re-check so the two commands cannot drift apart
    in what an operator reads.
    """
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
    return healthy, blocked, drifted


def _run_selfchecks(only: set[str] | None) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Run the selfchecks in table order: all of them, or only the named ones."""
    return [
        asyncio.run(_run_one_selfcheck(name, module_path, tool_name))
        for name, module_path, tool_name in _SELFCHECKS
        if only is None or name in only
    ]


def _health_steps(results: list[tuple[str, str, str, dict[str, Any]]]) -> tuple[dict[str, str | None], str]:
    """One next step per checked source, plus the CDP line printed under the table.

    CDP reachability decides two different next steps ("start the browser"
    vs "the browser was refused"), so it is probed before the table is
    printed rather than after it.
    """
    cdp_reachable, cdp_note = _probe_cdp()
    wb_transport = os.environ.get("WB_TRANSPORT", "api")
    wb_dom_fallback = os.environ.get("WB_DOM_FALLBACK", "1")
    steps = {
        name: next_step(
            name,
            status,
            checks,
            cdp_reachable=cdp_reachable,
            wb_transport=wb_transport,
            wb_dom_fallback=wb_dom_fallback,
        )
        for name, status, _, checks in results
    }
    return steps, cdp_note


def _health_exit_code(blocked: int, drifted: int) -> int:
    """The doctor exit code for a checked set of sources.

    Exit codes are what a cron job or CI step actually reads, so they have to
    separate the three outcomes an operator responds to differently:
      0 — everything that could be checked is healthy.
      1 — a parser drifted. Someone has to go look; this is the alarm.
      2 — nothing drifted but a source could not be judged (blocked, no CDP,
          wrong country). Not an alarm, not a clean bill of health either.
    Collapsing 2 into 0 is how "all good" starts meaning "we checked nothing".
    """
    if drifted:
        return 1
    if blocked:
        return 2
    return 0


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
    results = _run_selfchecks(only)
    steps, cdp_note = _health_steps(results)

    print("Marketplace connector health (selfcheck):")
    healthy, blocked, drifted = _print_health_rows(results, steps)
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

    return _health_exit_code(blocked, drifted)


def cmd_browser(argv: list[str]) -> int:
    """``browser status`` / ``show`` / ``start`` — the operator's view of the Chrome.

    ``status`` says whether the CDP endpoint answers, which Chrome it is and
    where its window is; ``show`` brings that window back on-screen so a human
    can log in or pass a site's check; ``start`` launches a Chrome that is
    visible by default (``--hidden`` for the connector's off-screen behaviour).
    None of them sends a request to any website.
    """
    if not argv:
        print("browser needs a verb: status, show or start", file=sys.stderr)
        return 2
    verb = argv[0]
    rest = argv[1:]
    if verb == "start":
        hidden = False
        for arg in rest:
            if arg == "--hidden":
                hidden = True
            elif arg.startswith("-"):
                print(f"unknown browser start option {arg!r}", file=sys.stderr)
                return 2
            else:
                print(f"browser start takes no arguments besides --hidden, got {arg!r}", file=sys.stderr)
                return 2
        return _browser_start(hidden)
    if rest:
        print(f"browser {verb} takes no arguments", file=sys.stderr)
        return 2
    if verb == "status":
        return _browser_status()
    if verb == "show":
        return _browser_show()
    print(f"unknown browser verb {verb!r}; expected status, show or start", file=sys.stderr)
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


def _browser_start(hidden: bool) -> int:
    """Launch the scraping Chrome for the operator; visible unless ``hidden``."""
    from mcp_core.transport.chrome_cdp import start_browser

    result = asyncio.run(start_browser(visible=not hidden))
    if not result.get("ok"):
        print(f"could not start the scraping Chrome: {result.get('reason', 'unknown reason')}", file=sys.stderr)
        return 1
    endpoint = f"{result.get('host', '?')}:{result.get('port', '?')}"
    if result.get("already_running"):
        print(f"Chrome with CDP is already running on {endpoint} - nothing to start.")
        print("To bring its window on-screen: marketplace-mcp browser show")
        return 0
    print(f"Started Chrome with CDP on {endpoint}.")
    print(f"Profile: {result.get('profile')}")
    if hidden:
        print("The window is off-screen. To log in or pass a site check: marketplace-mcp browser show")
    return 0


def _blocked_sources(results: list[tuple[str, str, str, dict[str, Any]]]) -> list[str]:
    """Names whose checks carry a wall reason, in the selfcheck table order."""
    blocked: list[str] = []
    for name, _status, _detail, checks in results:
        reasons = {str(_attr(entry, "reason", "") or "") for entry in checks.values()}
        if reasons & WALL_REASONS:
            blocked.append(name)
    return blocked


def cmd_unblock(argv: list[str]) -> int:
    """Open the right page per browser-walled source, wait, then re-check it.

    This command never solves or automates a site's check: it opens a page in
    the operator's scraping Chrome, waits while the human passes the check or
    logs in, and runs the same selfchecks ``doctor`` runs for those sources.
    """
    named: list[str] = []
    wait_s = UNBLOCK_WAIT_DEFAULT_S
    dry_run = False
    it = iter(argv)
    for arg in it:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--wait":
            raw = next(it, None)
            if raw is None:
                print("--wait requires seconds", file=sys.stderr)
                return 2
            try:
                wait_s = float(raw)
            except ValueError:
                print(f"--wait needs a number of seconds, got {raw!r}", file=sys.stderr)
                return 2
            if wait_s < 0:
                print("--wait cannot be negative", file=sys.stderr)
                return 2
        elif arg.startswith("-"):
            print(f"unknown unblock option {arg!r}", file=sys.stderr)
            return 2
        else:
            named.append(canonical(arg))

    known = {name for name, _, _ in _SELFCHECKS}
    unknown = set(named) - known
    if unknown:
        print(
            f"unknown source(s): {', '.join(sorted(unknown))}; expected one of {', '.join(sorted(known))}",
            file=sys.stderr,
        )
        return 2

    browser_sources = _browser_source_names()
    not_browser = [name for name in named if name not in browser_sources]
    if not_browser:
        source = not_browser[0]
        print(
            f"{source} is not a browser source; see `marketplace-mcp doctor {source}`",
            file=sys.stderr,
        )
        return 2

    if named:
        sources = list(dict.fromkeys(named))
    elif dry_run:
        print(
            "unblock --dry-run needs source names: without them it would run live selfchecks to find a wall",
            file=sys.stderr,
        )
        return 2
    else:
        sources = _blocked_sources(_run_selfchecks(set(browser_sources)))
        if not sources:
            print("No browser source reports a wall right now; nothing to unblock.")
            return 0

    pages = [(name, UNBLOCK_PAGES[name]) for name in sources]
    print(f"Opens {len(pages)} page(s), one per source; the re-check makes the same requests as doctor.")
    if dry_run:
        for name, url in pages:
            print(f"  {name:<14} {url}")
        print("Dry run: nothing was opened and no selfcheck was run.")
        return 0

    from mcp_core.transport.chrome_cdp import open_tabs, show_browser_window, start_browser

    started = asyncio.run(start_browser(visible=True))
    if not started.get("ok"):
        print(f"could not start the scraping Chrome: {started.get('reason', 'unknown reason')}", file=sys.stderr)
        return 1

    opened = asyncio.run(open_tabs([url for _name, url in pages]))
    if not opened.get("ok"):
        print(f"could not open the unblock pages: {opened.get('reason')}", file=sys.stderr)
        return 1

    shown = asyncio.run(show_browser_window(target_id=opened["target_ids"][0]))
    if not shown.get("ok"):
        print(
            f"could not show the scraping Chrome window: {shown.get('reason', 'unknown reason')}",
            file=sys.stderr,
        )
        return 1

    for name, url in pages:
        action = "log in by hand" if name in _LOGIN_UNBLOCK_SOURCES else "pass the site's check by hand, if it asks"
        print(f"  {name:<14} {url}")
        print(f"      {action}; this command does not touch the check.")

    if sys.stdin.isatty():
        try:
            input("Press Enter once you have passed the checks or logged in... ")
        except EOFError:
            pass
    else:
        print(f"Waiting {wait_s:g}s for you to pass the checks or log in.")
        time.sleep(wait_s)

    results = _run_selfchecks(set(sources))
    steps, cdp_note = _health_steps(results)
    print("\nRe-check after the manual step:")
    _, blocked, drifted = _print_health_rows(results, steps)
    print(f"\n  {cdp_note}")
    return _health_exit_code(blocked, drifted)


def _join_labels(clients: list[str]) -> str:
    """Human list of client names: "Claude Desktop, Cursor and Claude Code"."""
    labels = [CLIENT_LABELS[client] for client in clients]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _detected_clients() -> list[str]:
    """Clients that look installed: config folders for desktop clients, PATH for Claude Code."""
    found: list[str] = []
    if _config_path("claude").parent.is_dir():
        found.append("claude")
    if _config_path("cursor").parent.is_dir():
        found.append("cursor")
    if shutil.which("claude") is not None:
        found.append("claude-code")
    return found


def cmd_setup(argv: list[str]) -> int:
    """From zero to the first answer: detect a client, wire it, check the sources.

    The steps are the existing verbs, not a second implementation: config goes
    through ``_write_client_config`` (never with force), Chrome through
    ``start_browser``, health through doctor's shared helpers and a wall
    through ``cmd_unblock``. ``--dry-run`` prints the steps and files and
    writes, starts and requests nothing.
    """
    dry_run = no_browser = no_doctor = False
    selected: list[str] = []
    profile: str | None = None
    it = iter(argv)
    for arg in it:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--no-browser":
            no_browser = True
        elif arg == "--no-doctor":
            no_doctor = True
        elif arg == "--profile":
            raw = next(it, None)
            if not raw or raw.startswith("-"):
                print("--profile requires one of compare, decision or full", file=sys.stderr)
                return 2
            profile = raw
        elif arg == "--client":
            raw = next(it, None)
            if not raw or raw.startswith("-"):
                print("--client requires a client name", file=sys.stderr)
                return 2
            if raw not in SETUP_CLIENTS:
                print(
                    f"unknown client {raw!r}; expected one of {', '.join(SETUP_CLIENTS)}",
                    file=sys.stderr,
                )
                return 2
            if raw not in selected:
                selected.append(raw)
        elif arg.startswith("-"):
            print(
                f"unknown setup option {arg!r}; expected --client, --profile, --dry-run, --no-browser or --no-doctor",
                file=sys.stderr,
            )
            return 2
        else:
            print(f"setup takes no positional arguments, got {arg!r}", file=sys.stderr)
            return 2
    chosen_profile = profile or DEFAULT_PROFILE
    if chosen_profile not in PROFILE_SCRIPTS:
        print(
            f"unknown profile {chosen_profile!r}; expected one of {', '.join(PROFILE_SCRIPTS)}",
            file=sys.stderr,
        )
        return 2

    targets = selected or _detected_clients()
    configured = 0

    print("1. Clients:")
    if selected:
        print(f"   chosen with --client: {_join_labels(targets)}")
    elif targets:
        print(f"   detected: {_join_labels(targets)}")
    else:
        print("   no supported client detected (Claude Desktop, Cursor, Claude Code).")
        print("   wire one by hand:")
        for client in SETUP_CLIENTS:
            print(f"     marketplace-mcp install {client} --write")

    print("2. Config:")
    if not targets:
        print("   no client to configure; see the install commands above.")
    else:
        notes_shown: set[str] = set()
        for client in targets:
            label = CLIENT_LABELS[client]
            kept = None if profile is not None else _configured_profile(client)
            client_profile = kept or chosen_profile
            entry, note = _unified_entry(PROFILE_SCRIPTS[client_profile])
            if note not in notes_shown:
                print(note, file=sys.stderr)
                notes_shown.add(note)
            entries = {UNIFIED_ENTRY: entry}
            how = "already configured, kept" if kept else "selected"
            print(f"   {label}: profile {client_profile} ({how}): {PROFILE_DETAILS[client_profile]}")
            if client == "claude-code":
                print("     run this line once (setup does not run it):")
                print(f"     {_claude_mcp_add_line(UNIFIED_ENTRY, entry)}")
                configured += 1
                continue
            print(f"     {_config_path(client)}")
            rc = _write_client_config(
                client,
                entries,
                force=False,
                dry_run=dry_run,
                profile=client_profile,
                conflict_hint=(
                    f"setup skips {label}; to replace the existing entry run: "
                    f"marketplace-mcp install {client} --write --force --profile {client_profile}"
                ),
                show_document=False,
            )
            if rc == 0:
                configured += 1
            else:
                print(f"   {label}: config skipped (exit {rc}).", file=sys.stderr)

    if no_browser:
        print("3. Browser: skipped (--no-browser).")
    elif dry_run:
        print("3. Browser: would start the scraping Chrome on-screen; skipped in --dry-run.")
    else:
        from mcp_core.transport.chrome_cdp import start_browser

        print("3. Browser:")
        started = asyncio.run(start_browser(visible=True))
        if not started.get("ok"):
            print(
                f"   could not start the scraping Chrome: {started.get('reason', 'unknown reason')}; continuing.",
                file=sys.stderr,
            )
        elif started.get("already_running"):
            print(f"   Chrome with CDP is already running on {started.get('host', '?')}:{started.get('port', '?')}.")
        else:
            print(f"   started Chrome with CDP on {started.get('host', '?')}:{started.get('port', '?')}.")

    results: list[tuple[str, str, str, dict[str, Any]]] = []
    if no_doctor:
        print("4. Health: skipped (--no-doctor).")
    elif dry_run:
        print("4. Health: would run live selfchecks (live requests to the marketplaces); skipped in --dry-run.")
    else:
        print("4. Health: running live selfchecks; this makes live requests to the marketplaces.")
        results = _run_selfchecks(None)
        steps, cdp_note = _health_steps(results)
        print("\nMarketplace connector health (selfcheck):")
        _print_health_rows(results, steps)
        print(f"\n  {cdp_note}")

    print("5. Unblock:")
    blocked = _blocked_sources(results) if results else []
    if dry_run:
        print("   would offer unblock for any source whose checks report a wall; nothing was opened.")
    elif not results:
        print("   no health results; run `marketplace-mcp doctor`, then `marketplace-mcp unblock <source>`.")
    elif not blocked:
        print("   no browser source reports a wall right now.")
    elif sys.stdin.isatty():
        try:
            answer = input(f"   open the unblock pages for {', '.join(blocked)} now? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() in {"y", "yes"}:
            cmd_unblock(blocked)
        else:
            print(f"   skipped; run: marketplace-mcp unblock {' '.join(blocked)}")
    else:
        print(f"   {', '.join(blocked)} report a wall; run: marketplace-mcp unblock {' '.join(blocked)}")

    ask = "«Сравни цены на iPhone 15 128 ГБ на Wildberries, Ozon и Яндекс Маркете»"
    print()
    if targets:
        print(f"Restart {_join_labels(targets)}, then ask: {ask}")
    else:
        print(f"Once a client is wired, restart it, then ask: {ask}")

    return 0 if configured else 1


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
    if command == "setup":
        return cmd_setup(rest)
    if command == "install":
        return cmd_install(rest)
    if command == "doctor":
        return cmd_doctor(rest)
    if command == "browser":
        return cmd_browser(rest)
    if command == "unblock":
        return cmd_unblock(rest)
    print(f"unknown command {command!r}; expected setup, install, doctor, browser or unblock", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    log_event("marketplace.cli.unknown", command=command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
