"""Tests for the small HTTP helpers in mcp_core.http."""

from __future__ import annotations

from mcp_core.http import parse_retry_after


def test_parse_retry_after_reads_seconds():
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(" 30 ") == 30.0


def test_parse_retry_after_never_returns_a_non_finite_or_negative_delay():
    """The Retry-After header is wire-authored: a hostile or drifted ``1e999``
    must not become an infinite sleep, ``nan`` is no delay at all, and a
    negative delay is a moment in the past — clamp it exactly like the date
    branch already does instead of handing it to a scheduler."""
    assert parse_retry_after("1e999") is None
    assert parse_retry_after("inf") is None
    assert parse_retry_after("nan") is None
    assert parse_retry_after("-5") == 0.0


def test_parse_retry_after_absent_or_junk_is_none():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("yesterday-ish") is None
