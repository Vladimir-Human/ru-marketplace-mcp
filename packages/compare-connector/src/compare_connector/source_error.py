"""Turning a source's failure into the outcome's error fields.

A comparison never fails because one marketplace did: the failed source becomes
an outcome with a status and the recovery signals its connector sent (the error
code, retry timing, the action a human must take). Reading those signals out of
a connector exception or an MCP ToolError envelope, and refusing anything that
is not part of that contract, is what this module does.
"""
# START_MODULE_CONTRACT
# PURPOSE: R-CDP-HOST-PAUSE; R-CHALLENGE-ACTION: keep a failed source's typed recovery signals while bounding and redacting its text.
# SCOPE: Pure decoding of one exception into SourceOutcome error fields; no source access, no browser, no stdout.
# DEPENDS: fastmcp.exceptions (ToolError); mcp_core.errors (ConnectorError, ErrorCode); mcp_core.redact; stdlib datetime, json, math, re, typing.
# LINKS: M-COMPARE; M-COMPARE-SOURCE-ERROR; M-ERRORS; M-REDACT.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# _payload_text: Redacted, bounded text from an error payload.
# _source_error: Preserve typed recovery signals before truncating/redacting error detail.
# END_MODULE_MAP

from __future__ import annotations

import datetime
import json
import math
import re
from typing import Any

from fastmcp.exceptions import ToolError
from mcp_core.errors import ConnectorError, ErrorCode
from mcp_core.redact import redact_error_text as _redact


def _payload_text(value: Any, limit: int) -> str | None:
    """Redacted, bounded text from an error payload, or None when absent.

    Payload text may quote upstream content, so it gets the same redaction and
    bound as the detail line instead of being forwarded verbatim.
    """
    if not isinstance(value, str) or not value:
        return None
    return _redact(value)[:limit]


def _source_error(exc: Exception) -> dict[str, Any]:
    """Preserve typed recovery signals before truncating/redacting error detail.

    Only connector exceptions and MCP ToolError envelopes carry this contract.
    Arbitrary upstream text must not become an instruction to the client.
    """
    payload: dict[str, Any] = {}
    if isinstance(exc, ConnectorError):
        payload = exc.to_dict()
    elif isinstance(exc, ToolError):
        try:
            decoded = json.loads(str(exc))
        except (ValueError, RecursionError):
            pass
        else:
            if isinstance(decoded, dict):
                payload = decoded
    raw_code = payload.get("error")
    try:
        code = ErrorCode(raw_code if isinstance(raw_code, str) else "")
    except (ValueError, TypeError):
        detail = _redact(str(exc))[:200]
        return {
            "status": "blocked"
            if any(word in detail for word in ("transport_down", "rate_limited", "403"))
            else "error",
            "detail": detail,
        }

    challenge = code == ErrorCode.CHALLENGE_REQUIRED
    challenge_type = payload.get("challenge_type")
    if challenge_type not in ("captcha", "login", "login_or_captcha"):
        challenge_type = None
    status = "error"
    if code in (ErrorCode.CHALLENGE_REQUIRED, ErrorCode.RATE_LIMITED, ErrorCode.TRANSPORT_DOWN):
        status = "blocked"
    elif code == ErrorCode.TIMEOUT:
        status = "timeout"
    handoff_expires_at = None
    raw_expiry = payload.get("handoff_expires_at")
    if challenge and isinstance(raw_expiry, str):
        try:
            expiry = datetime.datetime.fromisoformat(raw_expiry)
        except ValueError:
            pass
        else:
            if expiry.tzinfo is not None:
                handoff_expires_at = expiry.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")
    raw_handoff_id = payload.get("handoff_id")
    retry_after_s: float | None = None
    raw_retry_after = payload.get("retry_after_s")
    if (
        isinstance(raw_retry_after, (int, float))
        and not isinstance(raw_retry_after, bool)
        and math.isfinite(raw_retry_after)
        and raw_retry_after >= 0
    ):
        retry_after_s = float(raw_retry_after)
    raw_retryable_now = payload.get("retryable_now")
    return {
        "status": status,
        "detail": _redact(str(payload.get("message", str(exc))))[:200],
        "error_code": code.value,
        "retryable": code.retryable,
        "retry_after_s": retry_after_s,
        "retryable_now": raw_retryable_now if isinstance(raw_retryable_now, bool) else None,
        "retry_when": _payload_text(payload.get("retry_when"), 60),
        "user_action": _payload_text(payload.get("user_action"), 300),
        "requires_user_action": challenge,
        "challenge_type": challenge_type if challenge else None,
        "handoff_expires_at": handoff_expires_at,
        "handoff_id": raw_handoff_id
        if handoff_expires_at
        and isinstance(raw_handoff_id, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{16,80}", raw_handoff_id)
        else None,
    }
