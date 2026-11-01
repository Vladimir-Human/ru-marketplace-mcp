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


def test_challenge_required_says_a_plain_retry_will_not_help():
    """R-CHALLENGE-ACTION: retryable in principle, never self-retryable now."""
    payload = ChallengeRequiredError("complete the browser challenge", provider="citilink").to_dict()

    assert payload["retryable"] is True
    assert payload["retryable_now"] is False
    assert payload["retry_when"] == "after_user_confirmation"
    assert payload["requires_user_action"] is True


def test_challenge_without_retention_names_the_unblock_command():
    """R-CHALLENGE-ACTION: with no retained page the action is the real CLI command."""
    payload = ChallengeRequiredError("complete the browser challenge", provider="citilink").to_dict()

    assert "handoff_expires_at" not in payload
    assert "handoff_id" not in payload
    assert "marketplace-mcp unblock citilink" in payload["user_action"]
    assert "retained" not in payload["user_action"].lower()


def test_challenge_with_retention_points_at_the_kept_open_tab():
    """R-CHALLENGE-ACTION: a retained page is completed in place, not re-opened."""
    payload = ChallengeRequiredError(
        "complete the browser challenge",
        provider="citilink",
        handoff_expires_at="2026-11-01T15:00:00Z",
        handoff_id="opaque-handoff-token",
    ).to_dict()

    action = payload["user_action"]
    assert "opaque-handoff-token" in action
    assert "marketplace-mcp browser show" in action
    assert "unblock" not in action


def test_challenge_accepts_a_source_specific_user_action():
    """R-CHALLENGE-ACTION: a source the CLI cannot unblock states its own step."""
    action = "open wildberries.ru in the scraping-profile Chrome and pass the captcha"
    payload = ChallengeRequiredError("captcha", provider="wb", user_action=action).to_dict()

    assert payload["user_action"] == action
