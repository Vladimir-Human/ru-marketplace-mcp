"""Marks this directory as its own pytest rootdir package, and keeps the operator's client configs out of reach.

Several connectors have a ``test_server.py``; without a conftest per directory
pytest cannot tell the identically-named modules apart during collection.

``install --write`` and ``setup`` write real client config files, and
``setup`` detects a client by its config folder. Without the guard below a
test that sets only one override would detect, and try to rewrite, the
developer's own Claude Desktop or Cursor config (found in review on
2026-10-30). Every test therefore starts with both paths inside its own
``tmp_path`` (folders not created), and with no ``claude`` on PATH; a test
that needs a client creates the folder or patches ``shutil.which`` itself.

The module also pins ``MPSTATS_MP_AUTH`` to an empty value: the unified server
decides at import whether to mount the mpstats tools, and that decision must
not depend on a developer's shell or ``.env`` (R-MPSTATS-MOUNT-GATE).
"""

# START_MODULE_CONTRACT
# PURPOSE: Isolate operator tests from real client configurations, home folders and uv cache discovery.
# SCOPE: Temporary path overrides; no ambient UV/Claude discovery; a pinned token-less MPStats environment.
# DEPENDS: pytest; marketplace_connector.cli.
# LINKS: M-MARKETPLACE; R-OFFLINE; R-CLI-INSTALL-UVX; R-MPSTATS-MOUNT-GATE.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _client_configs_never_reach_the_real_ones: Fake config/home/cache paths and disable real Claude/UV discovery.
# END_MODULE_MAP

import os

import pytest
from marketplace_connector import cli

# The unified server decides at import whether MPStats can answer at all:
# without a non-empty MPSTATS_MP_AUTH it does not mount the two mpstats tools
# (R-MPSTATS-MOUNT-GATE). Pin that here, before any test module imports the
# server, so a developer's exported token or .env file cannot change the
# mounted surface mid-suite. An empty value wins over .env in pydantic-settings
# and is not a token, so the baseline stays token-less; the gate tests set a
# token explicitly and restore this afterwards.
os.environ["MPSTATS_MP_AUTH"] = ""
# The inventory now follows the WB route (needs_cdp, effective_route), and
# that route comes from WB_TRANSPORT / WB_DOM_FALLBACK at import. Pin the
# documented default (API with the DOM fallback) for the same reason: a
# developer's exported mode must not change what the suite expects.
os.environ["WB_TRANSPORT"] = "api"
os.environ["WB_DOM_FALLBACK"] = "1"

# START_BLOCK_CLIENT_PATH_ISOLATION


@pytest.fixture(autouse=True)
def _client_configs_never_reach_the_real_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETPLACE_MCP_CLAUDE_CONFIG", str(tmp_path / "Claude" / "claude_desktop_config.json"))
    monkeypatch.setenv("MARKETPLACE_MCP_CURSOR_CONFIG", str(tmp_path / ".cursor" / "mcp.json"))
    monkeypatch.setattr(cli.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.delenv("UV", raising=False)
    real_which = cli.shutil.which

    def which_without_claude(name, *args, **kwargs):
        return None if name == "claude" else real_which(name, *args, **kwargs)

    monkeypatch.setattr(cli.shutil, "which", which_without_claude)


# END_BLOCK_CLIENT_PATH_ISOLATION
