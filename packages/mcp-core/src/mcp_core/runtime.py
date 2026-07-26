"""Transport selection shared by all five connector entry points.

Every connector is launched the same way — ``mcp.run(...)`` — and every
connector must default to stdio, because that is the transport MCP clients
speak and the only one the existing client configs know about. Rather than
copy the env-var parsing into five ``__main__.py`` files, it lives here once.

Two rules this module exists to enforce, both protocol- or security-critical:

1. **stdio owns stdout.** A stdio MCP server's stdout *is* the JSON-RPC stream;
   one stray byte there corrupts the protocol and surfaces as a baffling
   client-side parse error. So every diagnostic here goes to stderr via
   ``log_event`` — never ``print``, never ``sys.stdout``. ``scripts/check_no_print``
   guards this, and this module is in its scan path.

2. **HTTP binds to loopback by default.** These servers carry no
   authentication of their own; the tools are read-only, but an HTTP endpoint
   on ``0.0.0.0`` is still an unauthenticated scraper anyone on the network can
   drive. Binding to ``127.0.0.1`` keeps it local until the operator
   deliberately puts a reverse proxy with auth in front. Choosing a non-loopback
   host is allowed, but it is logged loudly as a warning so it is never silent.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from mcp_core.logging import log_event

if TYPE_CHECKING:
    from fastmcp import FastMCP

# The transport strings FastMCP 3.x accepts for these servers. "stdio" is the
# default and the only one existing client configs rely on. "http" is the
# modern streamable-HTTP transport; "streamable-http" is its explicit alias and
# "sse" is the older Server-Sent-Events transport, both kept so operators who
# know FastMCP's own vocabulary are not surprised. Verified against the installed
# FastMCP via inspect.signature(FastMCP.run) rather than assumed.
Transport = Literal["stdio", "http", "streamable-http", "sse"]

_VALID_TRANSPORTS: tuple[Transport, ...] = ("stdio", "http", "streamable-http", "sse")
_HTTP_TRANSPORTS: frozenset[str] = frozenset({"http", "streamable-http", "sse"})

DEFAULT_TRANSPORT: Transport = "stdio"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_HTTP_PATH = "/mcp"

ENV_TRANSPORT = "MCP_TRANSPORT"
ENV_HTTP_HOST = "MCP_HTTP_HOST"
ENV_HTTP_PORT = "MCP_HTTP_PORT"
ENV_HTTP_PATH = "MCP_HTTP_PATH"

# Hosts that keep the server reachable only from the machine it runs on. Any
# other value exposes it to the network and earns a warning.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class TransportConfig:
    """Resolved transport selection for one server launch.

    ``host``/``port``/``path`` are only meaningful when ``transport`` is an
    HTTP-family transport; for stdio they are left at their defaults and unused.
    """

    transport: Transport
    host: str = DEFAULT_HTTP_HOST
    port: int = DEFAULT_HTTP_PORT
    path: str = DEFAULT_HTTP_PATH

    @property
    def is_http(self) -> bool:
        """True when this selection runs over the network rather than stdio."""
        return self.transport in _HTTP_TRANSPORTS

    @property
    def is_loopback(self) -> bool:
        """True when the HTTP bind host is reachable only from this machine."""
        return self.host in _LOOPBACK_HOSTS


def _parse_transport(raw: str | None) -> Transport:
    """Map the ``MCP_TRANSPORT`` value to a supported transport.

    Empty or unset means stdio — the backward-compatible default. An unknown
    value is a configuration error worth failing on loudly rather than silently
    falling back, because a typo like ``MCP_TRANSPORT=htpt`` would otherwise
    start a stdio server the operator did not ask for.
    """
    if raw is None or raw.strip() == "":
        return DEFAULT_TRANSPORT

    value = raw.strip().lower()
    if value not in _VALID_TRANSPORTS:
        supported = ", ".join(_VALID_TRANSPORTS)
        raise ValueError(f"{ENV_TRANSPORT}={raw!r} is not a supported transport; use one of: {supported}")
    # mypy: membership in _VALID_TRANSPORTS narrows to the Literal at runtime,
    # but the checker cannot see that, so assert the shape it needs.
    assert value in _VALID_TRANSPORTS
    return value  # type: ignore[return-value]


def _parse_port(raw: str | None) -> int:
    """Parse ``MCP_HTTP_PORT`` into a valid TCP port.

    A non-numeric or out-of-range value is rejected rather than coerced, so a
    bad port fails at startup instead of surfacing as an obscure bind error.
    """
    if raw is None or raw.strip() == "":
        return DEFAULT_HTTP_PORT
    try:
        port = int(raw.strip())
    except ValueError:
        raise ValueError(f"{ENV_HTTP_PORT}={raw!r} is not an integer") from None
    if not (1 <= port <= 65535):
        raise ValueError(f"{ENV_HTTP_PORT}={raw!r} is out of range; must be 1-65535")
    return port


def _parse_host(raw: str | None) -> str:
    if raw is None or raw.strip() == "":
        return DEFAULT_HTTP_HOST
    return raw.strip()


def _parse_path(raw: str | None) -> str:
    """Normalise ``MCP_HTTP_PATH`` to a leading-slash endpoint path."""
    if raw is None or raw.strip() == "":
        return DEFAULT_HTTP_PATH
    path = raw.strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


def resolve_transport(env: dict[str, str] | None = None) -> TransportConfig:
    """Resolve the transport selection from the environment.

    Pure and side-effect-free (no sockets, no logging) so it can be unit-tested
    by passing a plain dict; ``run_server`` layers logging and the actual launch
    on top. Defaults reproduce today's behaviour exactly: no env vars set means
    stdio, which is why every existing client config keeps working untouched.
    """
    source = os.environ if env is None else env

    transport = _parse_transport(source.get(ENV_TRANSPORT))
    if transport == "stdio":
        # Host/port/path are irrelevant to stdio; do not parse them, so a stray
        # MCP_HTTP_PORT typo cannot fail a stdio launch that never uses it.
        return TransportConfig(transport=transport)

    return TransportConfig(
        transport=transport,
        host=_parse_host(source.get(ENV_HTTP_HOST)),
        port=_parse_port(source.get(ENV_HTTP_PORT)),
        path=_parse_path(source.get(ENV_HTTP_PATH)),
    )


def run_server(mcp: FastMCP, *, server_name: str) -> int:
    """Run ``mcp`` on the transport selected by the environment.

    Returns a process exit code so callers can ``sys.exit(run_server(...))``.
    ``KeyboardInterrupt`` and ``BrokenPipeError`` are treated as normal
    shutdowns — a client disconnecting mid-write on stdio is expected, not an
    error worth a traceback.

    Diagnostics go to stderr only; on stdio nothing is emitted before handing
    stdout to the JSON-RPC stream.
    """
    config = resolve_transport()

    try:
        if config.is_http:
            _warn_if_exposed(config, server_name=server_name)
            log_event(
                "server_start",
                server=server_name,
                transport=config.transport,
                host=config.host,
                port=config.port,
                path=config.path,
            )
            # host/port/path flow through FastMCP.run(**transport_kwargs) into
            # run_http_async; verified against the installed signature.
            mcp.run(transport=config.transport, host=config.host, port=config.port, path=config.path)
        else:
            # stdio: emit nothing here — stdout is the JSON-RPC stream and even
            # a stderr line is unnecessary noise for the common path.
            mcp.run(transport="stdio")
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # The client went away mid-write; a normal shutdown for stdio.
        return 0
    return 0


def _warn_if_exposed(config: TransportConfig, *, server_name: str) -> None:
    """Log a loud warning when an HTTP server binds beyond loopback.

    These servers ship no authentication. On ``0.0.0.0`` (or any routable host)
    that makes them an unauthenticated, read-only scraper anyone who can reach
    the port may drive. We do not refuse to start — an operator behind a
    reverse proxy has a legitimate reason — but the choice is never silent.
    """
    if config.is_loopback:
        return
    log_event(
        "http_bind_exposed",
        level=logging.WARNING,
        server=server_name,
        host=config.host,
        port=config.port,
        message=(
            "HTTP transport is bound beyond loopback and this server has no built-in auth. "
            "Put a reverse proxy that enforces authentication in front of it before exposing it."
        ),
    )
