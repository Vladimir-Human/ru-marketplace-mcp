from mcp_core.errors import ChallengeRequiredError


def test_challenge_required_error_is_machine_readable_and_retryable():
    payload = ChallengeRequiredError("complete the browser challenge", provider="taobao").to_dict()

    assert payload["error"] == "challenge_required"
    assert payload["retryable"] is True
    assert payload["requires_user_action"] is True
    assert payload["challenge_type"] == "captcha"
