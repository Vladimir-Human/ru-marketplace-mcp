"""Tests for ``mcp_core.runtime`` — transport selection for the connectors.

These cover the one behaviour every connector shares and must never regress:
stdio is the default, so an install with no transport env vars keeps working
exactly as it did before HTTP transport existed. The rest guards the HTTP
opt-in: parsing, defaulting, validation, and that the launch is dispatched with
the right arguments.

Everything here is offline. ``run_server`` is exercised against a fake FastMCP
that records how it was called; no real socket is ever bound, so the suite stays
fast and hermetic and safe to run in CI.
"""

from __future__ import annotations

import logging

import pytest
from mcp_core import runtime


class _FakeMCP:
    """Minimal FastMCP stand-in that records the run() call instead of serving."""

    def __init__(self) -> None:
        self.run_calls: list[dict] = []

    def run(self, **kwargs) -> None:
        self.run_calls.append(kwargs)


# --- resolve_transport: the default is stdio -------------------------------


def test_default_is_stdio_when_env_is_empty():
    """No env vars must mean stdio — the backward-compatibility guarantee.

    Every existing MCP client config spawns these servers expecting stdio; if
    an empty environment resolved to anything else, those configs would break.
    """
    config = runtime.resolve_transport({})

    assert config.transport == "stdio"
    assert config.is_http is False


def test_missing_transport_key_is_stdio():
    """An environment with unrelated keys still defaults to stdio."""
    config = runtime.resolve_transport({"PATH": "/usr/bin", "WB_TIMEOUT": "10"})
    assert config.transport == "stdio"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_transport_is_stdio(blank):
    """An explicitly blank MCP_TRANSPORT is treated as unset, not an error."""
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: blank})
    assert config.transport == "stdio"


def test_transport_is_case_insensitive_and_trimmed():
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: "  HTTP  "})
    assert config.transport == "http"
    assert config.is_http is True


# --- resolve_transport: HTTP family ----------------------------------------


@pytest.mark.parametrize("value", ["http", "streamable-http", "sse"])
def test_http_family_transports_are_accepted(value):
    """All three HTTP-family transports FastMCP supports are selectable."""
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: value})
    assert config.transport == value
    assert config.is_http is True


def test_http_defaults_bind_to_loopback():
    """HTTP with no host/port set must default to 127.0.0.1 — never 0.0.0.0.

    The default bind host is a security boundary: these servers have no auth, so
    the safe default is loopback-only until the operator opts into exposure.
    """
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: "http"})

    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.path == "/mcp"
    assert config.is_loopback is True


def test_http_reads_host_port_path_overrides():
    config = runtime.resolve_transport(
        {
            runtime.ENV_TRANSPORT: "http",
            runtime.ENV_HTTP_HOST: "0.0.0.0",
            runtime.ENV_HTTP_PORT: "9000",
            runtime.ENV_HTTP_PATH: "/rpc",
        }
    )

    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert config.path == "/rpc"
    assert config.is_loopback is False


def test_path_gets_leading_slash():
    """A path without a leading slash is normalised rather than rejected."""
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: "http", runtime.ENV_HTTP_PATH: "mcp"})
    assert config.path == "/mcp"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_hosts_are_recognised(host):
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: "http", runtime.ENV_HTTP_HOST: host})
    assert config.is_loopback is True


# --- resolve_transport: rejection of bad input -----------------------------


def test_invalid_transport_is_rejected_with_a_clear_message():
    """A typo'd transport must fail loudly, not silently fall back to stdio.

    Silent fallback would start a stdio server for someone who asked for HTTP —
    the failure would look like 'the HTTP port never opened', miles from the
    real cause.
    """
    with pytest.raises(ValueError) as exc:
        runtime.resolve_transport({runtime.ENV_TRANSPORT: "htpt"})

    msg = str(exc.value)
    assert "htpt" in msg
    assert "stdio" in msg  # the message lists the supported values


@pytest.mark.parametrize("bad", ["notaport", "12.5", "8000x", ""])
def test_non_integer_port_is_rejected(bad):
    """A blank port falls back to the default; a non-numeric one is an error."""
    env = {runtime.ENV_TRANSPORT: "http", runtime.ENV_HTTP_PORT: bad}
    if bad == "":
        assert runtime.resolve_transport(env).port == runtime.DEFAULT_HTTP_PORT
    else:
        with pytest.raises(ValueError, match="not an integer"):
            runtime.resolve_transport(env)


@pytest.mark.parametrize("bad", ["0", "-1", "65536", "99999"])
def test_out_of_range_port_is_rejected(bad):
    with pytest.raises(ValueError, match="out of range"):
        runtime.resolve_transport({runtime.ENV_TRANSPORT: "http", runtime.ENV_HTTP_PORT: bad})


def test_stdio_ignores_a_bad_http_port():
    """A stray MCP_HTTP_PORT must not fail a stdio launch that never uses it.

    Host/port are meaningless for stdio, so parsing them there would turn an
    irrelevant typo into a startup failure for the default transport.
    """
    config = runtime.resolve_transport({runtime.ENV_TRANSPORT: "stdio", runtime.ENV_HTTP_PORT: "notaport"})
    assert config.transport == "stdio"


# --- run_server: dispatch --------------------------------------------------


def test_run_server_stdio_calls_run_with_stdio_only(monkeypatch):
    """The stdio path must pass transport='stdio' and no host/port kwargs.

    Passing host/port to a stdio run would be meaningless at best; asserting the
    exact kwargs keeps the default launch identical to the hand-written one it
    replaced.
    """
    monkeypatch.delenv(runtime.ENV_TRANSPORT, raising=False)
    fake = _FakeMCP()

    rc = runtime.run_server(fake, server_name="wb")

    assert rc == 0
    assert fake.run_calls == [{"transport": "stdio"}]


def test_run_server_http_passes_host_port_path(monkeypatch):
    monkeypatch.setenv(runtime.ENV_TRANSPORT, "http")
    monkeypatch.setenv(runtime.ENV_HTTP_HOST, "127.0.0.1")
    monkeypatch.setenv(runtime.ENV_HTTP_PORT, "8123")
    fake = _FakeMCP()

    rc = runtime.run_server(fake, server_name="wb")

    assert rc == 0
    assert fake.run_calls == [{"transport": "http", "host": "127.0.0.1", "port": 8123, "path": "/mcp"}]


def test_run_server_translates_keyboard_interrupt(monkeypatch):
    """Ctrl-C is a normal shutdown and must map to the conventional 130."""
    monkeypatch.delenv(runtime.ENV_TRANSPORT, raising=False)

    class _Interrupting(_FakeMCP):
        def run(self, **kwargs):
            raise KeyboardInterrupt

    assert runtime.run_server(_Interrupting(), server_name="wb") == 130


def test_run_server_treats_broken_pipe_as_clean_exit(monkeypatch):
    """A client vanishing mid-write on stdio is expected, not a failure."""
    monkeypatch.delenv(runtime.ENV_TRANSPORT, raising=False)

    class _BrokenPipe(_FakeMCP):
        def run(self, **kwargs):
            raise BrokenPipeError

    assert runtime.run_server(_BrokenPipe(), server_name="wb") == 0


def test_run_server_warns_when_bound_beyond_loopback(monkeypatch, caplog):
    """Binding to 0.0.0.0 must emit a warning — exposure is never silent.

    The server still starts (an operator behind an authenticating proxy has a
    real reason), but an unauthenticated scraper going onto the network without
    a trace would be the wrong default.
    """
    monkeypatch.setenv(runtime.ENV_TRANSPORT, "http")
    monkeypatch.setenv(runtime.ENV_HTTP_HOST, "0.0.0.0")
    fake = _FakeMCP()

    with caplog.at_level(logging.WARNING, logger="mcp_connector"):
        runtime.run_server(fake, server_name="ozon")

    assert any(rec.levelno == logging.WARNING for rec in caplog.records)
    assert any("http_bind_exposed" in rec.getMessage() for rec in caplog.records)


def test_run_server_does_not_warn_on_loopback(monkeypatch, caplog):
    monkeypatch.setenv(runtime.ENV_TRANSPORT, "http")
    monkeypatch.setenv(runtime.ENV_HTTP_HOST, "127.0.0.1")
    fake = _FakeMCP()

    with caplog.at_level(logging.WARNING, logger="mcp_connector"):
        runtime.run_server(fake, server_name="ozon")

    assert not any("http_bind_exposed" in rec.getMessage() for rec in caplog.records)
