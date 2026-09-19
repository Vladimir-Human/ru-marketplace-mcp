"""Console entrypoint dispatch must never turn a typo into a waiting MCP server."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from marketplace_connector import __main__ as entrypoint
from marketplace_connector import cli
from mcp_core import runtime


@pytest.fixture
def no_server_start(monkeypatch):
    def unexpected_start(*args, **kwargs):
        pytest.fail("operator commands must not start an MCP server")

    monkeypatch.setattr(runtime, "run_server", unexpected_start)


@pytest.mark.parametrize("argument", ["instal", "--bogus", "--version"])
def test_unknown_argument_exits_with_error(argument, monkeypatch, capsys, no_server_start):
    monkeypatch.setattr(sys, "argv", ["marketplace-mcp", argument])

    assert entrypoint.main() == 2
    assert "unknown command" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["install", "doctor"])
def test_operator_subcommands_forward_their_arguments(command, monkeypatch, no_server_start):
    received = []

    def fake_command(arguments):
        received.extend(arguments)
        return 17

    monkeypatch.setattr(cli, f"cmd_{command}", fake_command)
    monkeypatch.setattr(sys, "argv", ["marketplace-mcp", command, "source-or-client"])

    assert entrypoint.main() == 17
    assert received == ["source-or-client"]


@pytest.mark.parametrize("argument", ["-h", "--help"])
def test_help_does_not_start_the_server(argument, monkeypatch, capsys, no_server_start):
    monkeypatch.setattr(sys, "argv", ["marketplace-mcp", argument])

    assert entrypoint.main() == 0
    assert "Operator CLI" in capsys.readouterr().out


def test_no_arguments_starts_the_default_server(monkeypatch):
    server = object()
    received = []

    def fake_run_server(mcp, *, server_name):
        received.append((mcp, server_name))
        return 17

    monkeypatch.setattr(sys, "argv", ["marketplace-mcp"])
    monkeypatch.setitem(sys.modules, "marketplace_connector.server", SimpleNamespace(mcp=server))
    monkeypatch.setattr(runtime, "run_server", fake_run_server)

    assert entrypoint.main() == 17
    assert received == [(server, "marketplace")]
