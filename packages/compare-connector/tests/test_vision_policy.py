"""Offline tests for the snapshot delivery policy (R4).

The JPEG is the largest thing this server can put on the wire (25-54 KB per
call), so the decision to send it has to be explainable: every branch here says
who decided and why, and the structured content repeats that to the caller.
"""

from __future__ import annotations

from compare_connector.vision_policy import (
    client_vision_hint,
    normalize_policy,
    resolve_image_delivery,
)


def test_policy_accepts_the_three_documented_values():
    assert normalize_policy("auto") == "auto"
    assert normalize_policy("always") == "always"
    assert normalize_policy("never") == "never"
    assert normalize_policy(" NEVER ") == "never"


def test_an_operator_typo_falls_back_to_the_default():
    """A typo must not quietly switch image delivery off (or on)."""
    for bad in ("", None, "sometimes", "true", 7, [], "off"):
        assert normalize_policy(bad) == "auto"


def test_client_hint_is_read_from_the_capability_extra_bags():
    assert client_vision_hint({"experimental": {"vision": True}}) is True
    assert client_vision_hint({"experimental": {"vision": False}}) is False
    assert client_vision_hint({"extensions": {"images": False}}) is False
    # A capability object is an advertisement, so its presence means yes.
    assert client_vision_hint({"experimental": {"vision": {"max_bytes": 4096}}}) is True


def test_a_client_that_says_nothing_is_unknown_not_visionless():
    """Silence must never be read as "cannot see images"."""
    assert client_vision_hint({}) is None
    assert client_vision_hint({"roots": {"listChanged": True}}) is None
    assert client_vision_hint({"experimental": {}}) is None
    assert client_vision_hint(None) is None
    assert client_vision_hint("nonsense") is None


def test_default_delivers_the_image():
    """The tool exists to be used: absent an explicit signal, pixels flow."""
    decision = resolve_image_delivery()

    assert decision.deliver is True
    assert decision.policy == "auto"
    assert decision.reason is None


def test_an_explicit_client_refusal_beats_everything():
    """Never send pixels to something that just said it cannot read them."""
    decision = resolve_image_delivery(policy="always", requested=True, client_vision=False)

    assert decision.deliver is False
    assert decision.reason == "client_reports_no_vision"


def test_deployment_policy_never_keeps_pixels_off_the_wire():
    decision = resolve_image_delivery(policy="never", requested=True, client_vision=True)

    assert decision.deliver is False
    assert decision.policy == "never"
    assert decision.reason == "policy_never"


def test_the_caller_can_ask_for_metadata_only():
    decision = resolve_image_delivery(policy="auto", requested=False)

    assert decision.deliver is False
    assert decision.reason == "caller_requested_metadata_only"


def test_deployment_policy_always_delivers():
    decision = resolve_image_delivery(policy="always", requested=None, client_vision=True)

    assert decision.deliver is True


def test_the_decision_is_reported_in_a_shape_the_caller_can_read():
    payload = resolve_image_delivery(policy="never").as_dict()

    assert payload == {
        "image_delivered": False,
        "image_policy": "never",
        "image_omitted_reason": "policy_never",
    }
