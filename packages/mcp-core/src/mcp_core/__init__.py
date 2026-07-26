"""Shared runtime for ru-marketplace-mcp connectors.

Every marketplace connector in this repo is a thin FastMCP wrapper over the
primitives collected here, so that adding a new source means writing fetch +
parse logic and nothing else:

- ``errors``      — provider-agnostic error taxonomy mapped onto MCP ToolError.
- ``http``        — httpx timeout/retry helpers and HTTP-status classification.
- ``logging``     — stderr JSON logger (stdout is reserved for JSON-RPC).
- ``redact``      — strips PII/session material out of error text.
- ``resilience``  — tolerant-reader parsing plus structural drift detection.
- ``selfcheck``   — tri-state drift-canary contract shared by all connectors.
- ``cache``       — in-process TTL cache for idempotent upstream reads.
- ``transport``   — anonymous HTTP tier and authenticated Chrome-CDP tier.

Never write to stdout from a stdio MCP server: it corrupts the JSON-RPC stream.
Use ``log_event`` (stderr) or the FastMCP ``Context`` logging methods.
"""

from __future__ import annotations

from mcp_core.errors import (
    AuthMissingError,
    BadRequestError,
    ConnectorError,
    ErrorCode,
    NotFoundError,
    ParserDriftError,
    PermissionDeniedError,
    RateLimitedError,
    TransportDownError,
    UpstreamTimeoutError,
    raise_tool_error,
)
from mcp_core.http import classify_http_error, get_timeout, parse_retry_after
from mcp_core.logging import log_event
from mcp_core.redact import redact_error_text

__all__ = [
    "AuthMissingError",
    "BadRequestError",
    "ConnectorError",
    "ErrorCode",
    "NotFoundError",
    "ParserDriftError",
    "PermissionDeniedError",
    "RateLimitedError",
    "TransportDownError",
    "UpstreamTimeoutError",
    "classify_http_error",
    "get_timeout",
    "log_event",
    "parse_retry_after",
    "raise_tool_error",
    "redact_error_text",
]

__version__ = "1.1.0"
