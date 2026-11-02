"""R19: a recovery hint must name a command and a file that exist.

The recovery hints are the operator's instructions when something is down. The
PyPI bundle ships the source packages, the README and the LICENSE — not the
root ``scripts/`` and ``docs/``. A hint that names those files sends an
installed operator after a file that was never packaged, so the hint has to
follow the install mode: a checkout may name the local scripts and docs, an
installed package must name the command it ships and a link pinned to the
release's tag.

No wheel is built and nothing is installed: the bundle's own metadata says
which console scripts it ships.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import build_pypi_bundle  # noqa: E402
from marketplace_connector import cli  # noqa: E402

MODES = ("checkout", "wheel", "uvx")

# A repo-relative path the hint names as a file. The lookbehind keeps the
# "scripts/..." inside a GitHub URL out: there it is part of a link.
LOCAL_FILE_RE = re.compile(r"(?<![/\w.])((?:scripts|docs)/[A-Za-z0-9_.\-/]+)")
URL_RE = re.compile(r"https?://[^\s`)\"']+")


def _hints(mode: str) -> list[str]:
    """Every recovery hint this install mode can produce."""
    steps = [
        cli.next_step(
            "avito",
            "inconclusive",
            {"search": {"state": "inconclusive", "reason": "transport_down"}},
            cdp_reachable=False,
            install_mode=mode,
        ),
        cli.next_step(
            "taobao",
            "inconclusive",
            {"search": {"state": "inconclusive", "reason": "login_wall"}},
            cdp_reachable=True,
            install_mode=mode,
        ),
        cli.next_step(
            "citilink",
            "drift_detected",
            {"search": {"state": "drift", "reason": "parse_smoke_failed"}},
            install_mode=mode,
        ),
        cli.next_step(
            "ozon",
            "drift_detected",
            {"search": {"state": "drift", "reason": "parse_smoke_failed"}},
            install_mode=mode,
        ),
        cli.next_step(
            "wildberries",
            "inconclusive",
            {"card": {"state": "inconclusive", "reason": "unknown_reason"}},
            cdp_reachable=True,
            wb_transport="dom",
            install_mode=mode,
        ),
        cli.next_step(
            "avito",
            "inconclusive",
            {"search": {"state": "inconclusive", "reason": "rate_limited"}},
            cdp_reachable=True,
            install_mode=mode,
        ),
        cli.next_step(
            "mpstats",
            "inconclusive",
            {"auth": {"state": "inconclusive", "reason": "auth_missing"}},
            install_mode=mode,
        ),
        cli.next_step("avito", "error", {}, install_mode=mode),
    ]
    assert all(steps), "every situation must produce a next step"
    return [step for step in steps if step]


def test_the_modes_are_the_ones_the_cli_knows():
    """The install modes the hints are built for are the CLI's own names."""
    assert (cli.INSTALL_CHECKOUT, cli.INSTALL_WHEEL, cli.INSTALL_UVX) == MODES


def _bundle_scripts() -> dict[str, str]:
    """The console scripts the PyPI bundle installs, read from its metadata."""
    return build_pypi_bundle.bundle_project(ROOT)["project"]["scripts"]


def _bundle_files() -> set[str]:
    """The repository files the PyPI bundle ships, relative to the repo root."""
    project = build_pypi_bundle.bundle_project(ROOT)
    sources = project["tool.hatch.build.targets.wheel.force-include"]
    readme = project["project"]["readme"]
    files = {str(source) for source in sources}
    files.add(readme if isinstance(readme, str) else str(readme["file"]))
    files.add("LICENSE")
    return files


def test_the_bundle_ships_no_root_scripts_or_docs():
    """The premise of the fix: the package carries no root scripts or docs."""
    shipped = _bundle_files()

    assert "scripts/start_chrome_cdp.ps1" not in shipped
    assert "scripts/diagnose_drift.py" not in shipped
    assert "docs/ANTI_BOT.md" not in shipped
    assert "docs/QUICKSTART.md" not in shipped
    # It does ship the import packages the servers run from.
    assert "packages/marketplace-connector/src/marketplace_connector" in shipped


def test_the_checkout_hints_name_files_that_exist():
    """A checkout has the root scripts and docs, so the hint may name them."""
    for mode in ("checkout",):
        for hint in _hints(mode):
            for relative in LOCAL_FILE_RE.findall(hint):
                assert (ROOT / relative).is_file(), f"{relative} does not exist in the checkout"


@pytest.mark.parametrize("mode", ["wheel", "uvx"])
def test_the_installed_hints_name_no_file_the_package_does_not_ship(mode):
    """The bundle ships no root scripts or docs, so the hint must not name them."""
    for hint in _hints(mode):
        assert LOCAL_FILE_RE.findall(hint) == [], f"the installed hint names a local file: {hint}"


@pytest.mark.parametrize("mode", MODES)
def test_every_link_is_pinned_to_the_release_tag(mode):
    """An unpinned link changes under the operator's feet."""
    for hint in _hints(mode):
        for url in URL_RE.findall(hint):
            assert url.startswith(cli.REPO_URL), f"a hint points outside the project: {url}"
            if "/blob/" in url:
                assert f"/blob/{cli.DOC_TAG}/" in url, f"the link is not pinned to the release: {url}"


@pytest.mark.parametrize("mode", MODES)
def test_every_link_points_at_a_file_that_exists_in_the_release(mode):
    """The pinned file is in the repository, so the link resolves."""
    for hint in _hints(mode):
        for url in URL_RE.findall(hint):
            if "/blob/" not in url:
                continue
            relative = url.split(f"/blob/{cli.DOC_TAG}/", 1)[1]
            assert (ROOT / relative).is_file(), f"{relative} is not in the repository"


def test_the_installed_command_is_the_console_script_the_bundle_installs():
    """A wheel install runs the console script; the bundle must declare it."""
    scripts = _bundle_scripts()

    assert "marketplace-mcp" in scripts
    assert cli.cli_command("wheel", "doctor", "avito") == "marketplace-mcp doctor avito"
    assert scripts["marketplace-mcp"] == "marketplace_connector.__main__:main"


def test_the_uvx_command_names_the_pinned_release_and_its_script():
    """uvx runs the console script the package publishes under its own name."""
    scripts = _bundle_scripts()

    assert "ru-marketplace-mcp" in scripts
    assert cli.cli_command("uvx", "doctor", "avito") == f"uvx ru-marketplace-mcp@{cli.__version__} doctor avito"
    assert scripts["ru-marketplace-mcp"] == scripts["marketplace-mcp"]


def test_the_checkout_command_is_the_spelling_the_docs_use():
    """A checkout runs the workspace through uv, as the quickstart says."""
    quickstart = (ROOT / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")

    assert cli.cli_command("checkout", "doctor", "avito") == "uv run marketplace-mcp doctor avito"
    assert "uv run marketplace-mcp" in quickstart


def test_the_command_the_hint_suggests_runs():
    """The CLI the hints name really starts and answers."""
    result = subprocess.run(
        [sys.executable, "-m", "marketplace_connector", "help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "marketplace-mcp" in result.stdout


def test_the_usage_screen_points_at_a_file_this_install_can_reach():
    """The read-more line is a pointer, and it must not be a missing file."""
    checkout = cli._usage("checkout")
    installed = cli._usage("wheel")

    assert LOCAL_FILE_RE.findall(checkout) == ["docs/QUICKSTART.md"]
    assert LOCAL_FILE_RE.findall(installed) == []
    assert f"/blob/{cli.DOC_TAG}/docs/QUICKSTART.md" in installed
    assert (ROOT / "docs" / "QUICKSTART.md").is_file()


def test_a_dry_run_does_not_change_the_client_config(tmp_path, monkeypatch):
    """The hint's dry run must leave the operator's config alone."""
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}), encoding="utf-8")
    before = config.read_bytes()
    monkeypatch.setenv("MARKETPLACE_MCP_CLAUDE_CONFIG", str(config))

    assert cli.cmd_install(["claude", "--write", "--dry-run"]) == 0
    assert config.read_bytes() == before
    assert list(config.parent.glob("*.bak-*")) == []


def test_the_unblock_dry_run_opens_nothing(capsys):
    """The unblock hint has a dry run too, and it opens no page."""
    assert cli.cmd_unblock(["ozon", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Dry run" in out


def test_a_healthy_source_has_no_next_step_in_any_mode():
    """The positive control: a healthy source is not advised anything."""
    for mode in MODES:
        assert cli.next_step("wildberries", "success", {"card": {"state": "healthy"}}, install_mode=mode) is None
