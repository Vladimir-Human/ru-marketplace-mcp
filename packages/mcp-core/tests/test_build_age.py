"""R-OLD-BUILD-INFO: local release metadata, strict age boundary and error scope."""
# START_MODULE_CONTRACT
# PURPOSE: Prove local release-age decisions and additive error serialization.
# SCOPE: Faked release dates, explicit today, all error codes and the shared error reference; offline.
# DEPENDS: M-CORE,M-BUILD-AGE,M-ERRORS,M-RESOURCES; pytest,FastMCP; stdlib datetime,importlib,json.
# LINKS: R-OLD-BUILD-INFO.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# test_release_date_is_declared_as_iso_metadata: Release dates remain valid across future bumps.
# test_build_age_has_a_strict_30_day_boundary: Future/young/30-day builds omit the hint; 31+ days include it.
# test_an_unparseable_release_date_has_no_age_info: Invalid dates do not invent an age.
# test_tool_error_adds_only_optional_old_build_and_preserves_all_other_fields: All-code matrix and nonmutation.
# test_error_reference_documents_optional_age_info: Optional fields, threshold and conditional advice.
# END_MODULE_MAP

from __future__ import annotations

import datetime as dt
import importlib
import json

import mcp_core
import pytest
from fastmcp.exceptions import ToolError
from mcp_core.errors import ChallengeRequiredError, ConnectorError, ErrorCode, raise_tool_error
from mcp_core.resources import render_error_reference

# START_BLOCK_AGE_AND_ERROR_PROOFS

UPGRADE = "if a newer version exists: uvx ru-marketplace-mcp@latest (or: pip install -U ru-marketplace-mcp)"


@pytest.mark.req("R-OLD-BUILD-INFO")
def test_release_date_is_declared_as_iso_metadata() -> None:
    released = getattr(mcp_core, "__release_date__", None)
    assert isinstance(released, str)
    assert dt.date.fromisoformat(released).isoformat() == released


@pytest.mark.req("R-OLD-BUILD-INFO")
@pytest.mark.parametrize("age_days", [-1, 0, 10, 30, 31, 40])
def test_build_age_has_a_strict_30_day_boundary(monkeypatch, age_days: int) -> None:
    monkeypatch.setattr(mcp_core, "__release_date__", "2026-11-15", raising=False)
    build_age = importlib.import_module("mcp_core.build_age")
    info = build_age.old_build_info(today=dt.date(2026, 11, 15) + dt.timedelta(days=age_days))
    expected = (
        {"version": mcp_core.__version__, "released": "2026-11-15", "age_days": age_days, "upgrade": UPGRADE}
        if age_days > 30
        else None
    )
    assert info == expected


@pytest.mark.req("R-OLD-BUILD-INFO")
@pytest.mark.parametrize("released", ["", "not-a-date", "2026-02-30"])
def test_an_unparseable_release_date_has_no_age_info(monkeypatch, released: str) -> None:
    monkeypatch.setattr(mcp_core, "__release_date__", released, raising=False)
    build_age = importlib.import_module("mcp_core.build_age")
    assert build_age.old_build_info(today=dt.date(2026, 12, 25)) is None


@pytest.mark.req("R-OLD-BUILD-INFO")
@pytest.mark.parametrize("age_days", [10, 30, 40])
@pytest.mark.parametrize("code", list(ErrorCode))
def test_tool_error_adds_only_optional_old_build_and_preserves_all_other_fields(monkeypatch, age_days, code) -> None:
    build_age = importlib.import_module("mcp_core.build_age")
    fixed_today = dt.datetime.now(dt.UTC).date()
    released = (fixed_today - dt.timedelta(days=age_days)).isoformat()
    monkeypatch.setattr(build_age, "_utc_today", lambda: fixed_today)
    monkeypatch.setattr(mcp_core, "__release_date__", released, raising=False)
    err = (
        ChallengeRequiredError("human check", provider="fixture", handoff_id="opaque-id")
        if code == ErrorCode.CHALLENGE_REQUIRED
        else ConnectorError(code, "fixture failure", provider="fixture", status_code=503, retry_after_s=60)
    )
    original = err.to_dict()
    with pytest.raises(ToolError) as caught:
        raise_tool_error(err)
    payload = json.loads(str(caught.value))
    expected = dict(original)
    if age_days > 30 and code in {
        ErrorCode.PARSER_DRIFT,
        ErrorCode.TRANSPORT_DOWN,
        ErrorCode.CHALLENGE_REQUIRED,
        ErrorCode.RATE_LIMITED,
    }:
        expected["old_build"] = {
            "version": mcp_core.__version__,
            "released": released,
            "age_days": age_days,
            "upgrade": UPGRADE,
        }
    assert payload == expected
    assert err.to_dict() == original, "serialization must not mutate the original error"


@pytest.mark.req("R-OLD-BUILD-INFO")
def test_error_reference_documents_optional_age_info() -> None:
    text = render_error_reference()
    assert "old_build?" in text
    assert "`version`, `released`, `age_days`, `upgrade`" in text
    assert "older than 30 days" in text
    assert UPGRADE in text
    for code in ("parser_drift", "transport_down", "challenge_required", "rate_limited"):
        assert f"`{code}`" in text


# END_BLOCK_AGE_AND_ERROR_PROOFS
