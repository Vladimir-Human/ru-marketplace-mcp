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
"""

import pytest
from marketplace_connector import cli


@pytest.fixture(autouse=True)
def _client_configs_never_reach_the_real_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETPLACE_MCP_CLAUDE_CONFIG", str(tmp_path / "Claude" / "claude_desktop_config.json"))
    monkeypatch.setenv("MARKETPLACE_MCP_CURSOR_CONFIG", str(tmp_path / ".cursor" / "mcp.json"))
    real_which = cli.shutil.which

    def which_without_claude(name, *args, **kwargs):
        return None if name == "claude" else real_which(name, *args, **kwargs)

    monkeypatch.setattr(cli.shutil, "which", which_without_claude)
