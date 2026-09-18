"""When a retained page's JPEG may go on the wire.

A snapshot of a marketplace page is 25-54 KB of base64 (1440x900, q70) sent on
every call — the single largest thing this server can put on the wire. Sending it
to a client that cannot read images buys nothing and costs a lot, so delivery is
decided, not assumed:

* **the caller** can say so explicitly (`include_image=True/False`);
* **the deployment** can set a policy (`COMPARE_SNAPSHOT_IMAGES` = auto | always |
  never) — the same shape as Playwright MCP's ``imageResponses``;
* **the client** can advertise its own capability: MCP has no standard "vision"
  capability, so only an *explicit* hint is honoured, and its absence is not read
  as "no vision" (that would silently break every existing client). The hint is
  looked up in ``capabilities.experimental`` / ``capabilities.extensions``, the
  the ``extensions`` bag is the one fastmcp itself inspects; ``experimental`` is read as our own convention, because a client that means 'no images' has nowhere standard to say it.

The resolver is pure: policy text, the caller's wish and the client's hint in,
one decision out. Nothing here opens a browser or touches MCP.
"""

from __future__ import annotations

from typing import Any, Literal

__all__ = [
    "ImageDelivery",
    "client_vision_hint",
    "normalize_policy",
    "resolve_image_delivery",
]

#: Delivered as the default: follow the caller unless the deployment forbids it.
DEFAULT_POLICY = "auto"

Policy = Literal["auto", "always", "never"]


class ImageDelivery:
    """The decision, and why — so the caller can be told instead of guessing."""

    __slots__ = ("deliver", "policy", "reason")

    def __init__(self, deliver: bool, policy: str, reason: str | None) -> None:
        self.deliver = deliver
        self.policy = policy
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {"image_delivered": self.deliver, "image_policy": self.policy, "image_omitted_reason": self.reason}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ImageDelivery(deliver={self.deliver}, policy={self.policy!r}, reason={self.reason!r})"


def normalize_policy(raw: object) -> Policy:
    """Accept an operator's policy text, falling back to the default.

    A typo must not silently switch image delivery off — or on.
    """
    text = str(raw or "").strip().lower()
    return text if text in ("auto", "always", "never") else DEFAULT_POLICY  # type: ignore[return-value]


def client_vision_hint(capabilities: object) -> bool | None:
    """An *explicit* vision hint from the client, or ``None`` when it says nothing.

    Only ``capabilities.experimental`` and ``capabilities.extensions`` are read:
    they are the documented bags for non-standard fields, and a client that does
    not mention vision is treated as "unknown", never as "cannot".
    """
    if not isinstance(capabilities, dict):
        return None
    for bag_name in ("experimental", "extensions"):
        bag = capabilities.get(bag_name)
        if not isinstance(bag, dict):
            continue
        for key in ("vision", "images", "image", "supports_vision"):
            if key in bag:
                value = bag[key]
                if isinstance(value, bool):
                    return value
                if isinstance(value, dict):
                    # A capability object (e.g. {"vision": {"max_bytes": …}}) is
                    # an advertisement, so its presence means yes.
                    return True
    return None


def resolve_image_delivery(
    *,
    policy: object = DEFAULT_POLICY,
    requested: bool | None = None,
    client_vision: bool | None = None,
) -> ImageDelivery:
    """Decide whether the JPEG goes on the wire, and name the reason when it does not.

    Precedence, highest first — the deployment outranks the caller, and only the
    client's own explicit refusal outranks the deployment:

    1. the client said it cannot read images → never send pixels;
    2. ``never`` → the deployment forbids images for everyone;
    3. ``always`` → the deployment requires them, so a caller asking for metadata
       only does not veto it (that is what makes ``always`` differ from ``auto``:
       an independent review found the two identical);
    4. the caller asked for metadata only;
    5. otherwise deliver — the tool exists to be used.
    """
    chosen = normalize_policy(policy)

    if client_vision is False:
        return ImageDelivery(False, chosen, "client_reports_no_vision")
    if chosen == "never":
        return ImageDelivery(False, chosen, "policy_never")
    if chosen == "always":
        return ImageDelivery(True, chosen, None)
    if requested is False:
        return ImageDelivery(False, chosen, "caller_requested_metadata_only")
    return ImageDelivery(True, chosen, None)
