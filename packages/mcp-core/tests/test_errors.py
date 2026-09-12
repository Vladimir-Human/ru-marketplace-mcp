from mcp_core.errors import ChallengeRequiredError


def test_challenge_required_error_is_machine_readable_and_retryable():
    payload = ChallengeRequiredError("complete the browser challenge", provider="taobao").to_dict()

    assert payload["error"] == "challenge_required"
    assert payload["retryable"] is True
    assert payload["requires_user_action"] is True
    assert payload["challenge_type"] == "captcha"
    assert "handoff_expires_at" not in payload
    assert "handoff_id" not in payload


def test_retained_challenge_has_explicit_expiry():
    expiry = "2026-09-12T15:00:00Z"
    payload = ChallengeRequiredError(
        "complete the browser challenge", handoff_expires_at=expiry, handoff_id="opaque-handoff-token"
    ).to_dict()
    assert payload["handoff_expires_at"] == expiry
    assert payload["handoff_id"] == "opaque-handoff-token"
