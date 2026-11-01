"""R-SECRET-REDACTION: logging and error output redact by construction.

Redaction used to be caller discipline: every connector wrapped values in
``_redact`` before handing them to ``log_event`` or ``raise_tool_error``, and a
new call site that forgot silently leaked. These tests pass the raw
``MPSTATS_MP_AUTH`` value (no wrapper) straight into both functions and prove it
cannot reach the stderr record or the ToolError the model reads. Existing
explicit wrappers stay correct because redacting twice is a no-op.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastmcp.exceptions import ToolError
from mcp_core.errors import RateLimitedError, TransportDownError, raise_tool_error
from mcp_core.logging import log_event

# The one real secret this project handles is MPSTATS_MP_AUTH, a JWT cookie
# value. redact.py already recognizes that shape; these tests pin where the
# redaction runs, not a new rule.
_SECRET = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwYWlkLXVzZXIifQ.sekretsignature12345"

# A value with no recognizable shape at all: only redact.py's exact-value rule
# for the secret env vars (MPSTATS_MP_AUTH, MCP_HTTP_AUTH_TOKEN) can catch it.
_PLAIN_SECRET = "plain-paid-session-secret-7f3a"


def test_log_event_redacts_a_raw_secret_field(caplog, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _SECRET)

    with caplog.at_level(logging.INFO, logger="mcp_connector"):
        log_event("mpstats.probe", error=f"Cookie: mp_auth={_SECRET}")

    assert _SECRET not in caplog.text
    assert "eyJ" not in caplog.text


def test_log_event_redacts_the_event_name(caplog, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _SECRET)

    with caplog.at_level(logging.INFO, logger="mcp_connector"):
        log_event(f"mpstats.probe.{_SECRET}")

    assert _SECRET not in caplog.text


def test_log_event_redacts_strings_nested_in_fields(caplog, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _SECRET)

    with caplog.at_level(logging.INFO, logger="mcp_connector"):
        log_event("mpstats.probe", sources=[f"vp:{_SECRET}", "wb"], meta={"cookie": f"mp_auth={_SECRET}"})

    assert _SECRET not in caplog.text
    assert "wb" in caplog.text


def test_log_event_redacts_strings_from_the_json_default(caplog, monkeypatch):
    class _Carrier:
        def __str__(self):
            return f"Cookie: mp_auth={_SECRET}"

    monkeypatch.setenv("MPSTATS_MP_AUTH", _SECRET)

    with caplog.at_level(logging.INFO, logger="mcp_connector"):
        log_event("mpstats.probe", carrier=_Carrier())

    assert _SECRET not in caplog.text


def test_log_event_leaves_non_string_fields_untouched(caplog):
    with caplog.at_level(logging.INFO, logger="mcp_connector"):
        log_event("mpstats.probe", retries=2, ok=True, ratio=0.5)

    record = next(record for record in caplog.records if "mpstats.probe" in record.getMessage())
    payload = json.loads(record.getMessage())

    assert payload["retries"] == 2
    assert payload["ok"] is True
    assert payload["ratio"] == 0.5


def test_log_event_redacts_a_non_token_shaped_env_secret(caplog, monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _PLAIN_SECRET)

    with caplog.at_level(logging.INFO, logger="mcp_connector"):
        log_event("mpstats.probe", detail=f"mp_auth value: {_PLAIN_SECRET}")

    assert _PLAIN_SECRET not in caplog.text


def test_raise_tool_error_redacts_the_raw_message(monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _SECRET)

    with pytest.raises(ToolError) as excinfo:
        raise_tool_error(TransportDownError(f"connect failed: Cookie: mp_auth={_SECRET}", provider="mpstats"))

    assert _SECRET not in str(excinfo.value)
    assert "eyJ" not in str(excinfo.value)


def test_raise_tool_error_redacts_string_fields(monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _SECRET)

    with pytest.raises(ToolError) as excinfo:
        raise_tool_error(TransportDownError("upstream refused", provider=_SECRET))

    assert _SECRET not in str(excinfo.value)


def test_raise_tool_error_redacts_a_non_token_shaped_env_secret(monkeypatch):
    monkeypatch.setenv("MPSTATS_MP_AUTH", _PLAIN_SECRET)

    with pytest.raises(ToolError) as excinfo:
        raise_tool_error(TransportDownError(f"mp_auth rejected {_PLAIN_SECRET}", provider=_PLAIN_SECRET))

    assert _PLAIN_SECRET not in str(excinfo.value)


def test_raise_tool_error_leaves_non_string_fields_untouched():
    with pytest.raises(ToolError) as excinfo:
        raise_tool_error(RateLimitedError("wb", retry_after_s=5.0))

    payload = json.loads(str(excinfo.value))

    assert payload["retry_after_s"] == 5.0
    assert payload["retryable"] is True
    assert payload["status_code"] == 429
