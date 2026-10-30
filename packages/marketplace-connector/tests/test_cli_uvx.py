"""R-CLI-INSTALL-UVX: persisted configs must not reference uv's temporary tool env."""
# START_MODULE_CONTRACT
# PURPOSE: Prove pinned uv tool entries through the install/setup commands and preserve existing deployments.
# SCOPE: Fully fake UV, prefix/cache discovery, PATH and client paths; no process, home or marketplace access.
# DEPENDS: marketplace_connector.cli; pytest; stdlib.
# LINKS: M-MARKETPLACE; R-CLI-INSTALL-UVX.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# uvx_environment: Simulate a release running from uv's deletable cache.
# test_install_uvx_writes_the_pinned_release: Exercise all existing profiles through install --write.
# test_setup_uvx_writes_the_same_pinned_entry: Exercise setup without browser or selfchecks.
# test_uv_queries_cache_dir / test_uv_cache_probe_failure_keeps_console_script: Cache discovery and its failures.
# test_uv_on_path_spelling_is_kept_when_it_is_the_same_file: Symlinked uv keeps its stable PATH spelling.
# test_non_cached_installs_keep_console_scripts / test_checkout_takes_precedence_over_uvx: Preserve old behavior.
# END_MODULE_MAP

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from marketplace_connector import cli

# START_BLOCK_FIXTURES


@pytest.fixture
def uvx_environment(tmp_path, monkeypatch):
    uv = tmp_path / "Program Files" / "uv.exe"
    cache = tmp_path / "uv-cache"
    prefix = cache / "archive-v0" / "temporary-release"
    monkeypatch.setenv("UV", str(uv))
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setattr(cli.sys, "prefix", str(prefix))
    monkeypatch.setattr(cli, "_workspace_root", lambda: None)
    monkeypatch.setattr(cli.shutil, "which", lambda script: str(tmp_path / "bin" / script))

    def no_process(*args, **kwargs):
        pytest.fail("UV_CACHE_DIR is set: cache discovery must not launch a process")

    monkeypatch.setattr(cli.subprocess, "run", no_process)
    return uv, cache, prefix


# END_BLOCK_FIXTURES
# START_BLOCK_PUBLIC_COMMANDS


@pytest.mark.parametrize(
    ("profile", "script"), [("compare", "compare-mcp"), ("decision", "decision-mcp"), ("full", "marketplace-mcp")]
)
def test_install_uvx_writes_the_pinned_release(profile, script, uvx_environment, tmp_path, capsys):
    uv, _, prefix = uvx_environment
    config = tmp_path / "Claude" / "claude_desktop_config.json"
    assert cli.cmd_install(["claude", "--write", "--profile", profile]) == 0
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["marketplace"]
    assert entry == {
        "command": str(uv),
        "args": ["tool", "run", "--from", f"ru-marketplace-mcp=={cli.__version__}", script],
    }
    assert str(prefix) not in json.dumps(entry)
    captured = capsys.readouterr()
    assert "pinned release" in captured.err
    assert "through uv" in captured.err
    assert "install <client> --write --force" in captured.err


def test_setup_uvx_writes_the_same_pinned_entry(uvx_environment, tmp_path, capsys):
    uv, _, _ = uvx_environment
    assert cli.cmd_setup(["--client", "cursor", "--no-browser", "--no-doctor"]) == 0
    config = tmp_path / ".cursor" / "mcp.json"
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["marketplace"]
    assert entry == {
        "command": str(uv),
        "args": ["tool", "run", "--from", f"ru-marketplace-mcp=={cli.__version__}", "compare-mcp"],
    }
    assert "pinned release" in capsys.readouterr().err


def test_install_force_refreshes_the_release_pin(uvx_environment, tmp_path, monkeypatch):
    config = tmp_path / "Claude" / "claude_desktop_config.json"
    assert cli.cmd_install(["claude", "--write"]) == 0
    before = config.read_bytes()
    monkeypatch.setattr(cli, "__version__", "9.8.7")
    assert cli.cmd_install(["claude", "--write"]) == 2
    assert config.read_bytes() == before
    assert cli.cmd_install(["claude", "--write", "--force"]) == 0
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["marketplace"]
    assert entry["args"][-2:] == ["ru-marketplace-mcp==9.8.7", "compare-mcp"]
    backups = list(config.parent.glob("*.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before


# END_BLOCK_PUBLIC_COMMANDS
# START_BLOCK_DISCOVERY


def test_uv_queries_cache_dir(uvx_environment, monkeypatch):
    uv, cache, _ = uvx_environment
    monkeypatch.delenv("UV_CACHE_DIR")
    calls = []

    def cache_dir(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True
        assert kwargs["timeout"] <= 5
        return SimpleNamespace(stdout=f"{cache}\n", returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", cache_dir)
    entry, note = cli._unified_entry("decision-mcp")
    assert calls == [[str(uv), "cache", "dir"]]
    assert entry["command"] == str(uv)
    assert entry["args"] == ["tool", "run", "--from", f"ru-marketplace-mcp=={cli.__version__}", "decision-mcp"]
    assert "pinned release" in note


@pytest.mark.parametrize("reason", ["missing", "timeout", "exit", "empty"])
def test_uv_cache_probe_failure_keeps_console_script(reason, uvx_environment, tmp_path, monkeypatch):
    uv, _, _ = uvx_environment
    monkeypatch.delenv("UV_CACHE_DIR")

    def fail(command, **kwargs):
        if reason == "missing":
            raise FileNotFoundError(str(uv))
        if reason == "timeout":
            raise subprocess.TimeoutExpired(command, 5)
        if reason == "exit":
            raise subprocess.CalledProcessError(2, command)
        return SimpleNamespace(stdout="\n", returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fail)
    entry, note = cli._unified_entry("compare-mcp")
    assert entry == {"command": str(tmp_path / "bin" / "compare-mcp"), "args": []}
    assert "console script" in note


@pytest.mark.parametrize("reason", ["no-uv", "ordinary-venv", "sibling-cache-prefix"])
def test_non_cached_installs_keep_console_scripts(reason, uvx_environment, tmp_path, monkeypatch):
    if reason == "no-uv":
        monkeypatch.delenv("UV")
    else:
        prefix = tmp_path / ("uv-cache-other" if reason == "sibling-cache-prefix" else "venv")
        monkeypatch.setattr(cli.sys, "prefix", str(prefix))
    entry, note = cli._unified_entry("compare-mcp")
    assert entry == {"command": str(tmp_path / "bin" / "compare-mcp"), "args": []}
    assert "console script" in note


def test_uv_env_command_is_resolved_absolutely(uvx_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("UV", "uv-wrapper")
    resolved = tmp_path / "bin" / "uv-wrapper"
    monkeypatch.setattr(cli.shutil, "which", lambda name: str(resolved) if name == "uv-wrapper" else None)
    entry, _ = cli._unified_entry("compare-mcp")
    assert entry["command"] == str(resolved)
    assert entry["args"][0:2] == ["tool", "run"]


def test_uv_on_path_spelling_is_kept_when_it_is_the_same_file(uvx_environment, tmp_path):
    """A symlinked uv (Homebrew, pipx) must not be saved as its versioned target."""
    uv, _, _ = uvx_environment
    uv.parent.mkdir(parents=True)
    uv.write_bytes(b"uv")
    link = tmp_path / "bin" / "uv"
    link.parent.mkdir(parents=True)
    os.link(uv, link)
    entry, _ = cli._unified_entry("compare-mcp")
    assert entry["command"] == str(link)


def test_checkout_takes_precedence_over_uvx(uvx_environment, tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(cli, "_workspace_root", lambda: checkout)
    entry, note = cli._unified_entry("compare-mcp")
    assert entry == {
        "command": str(tmp_path / "bin" / "uv"),
        "args": ["run", "--directory", str(checkout), "compare-mcp"],
    }
    assert "checkout" in note
    assert "pinned release" not in note


# END_BLOCK_DISCOVERY
