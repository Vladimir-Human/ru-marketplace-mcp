# START_MODULE_CONTRACT
# PURPOSE: Encode provider-neutral failures as machine-readable MCP ToolErrors.
# SCOPE: Error taxonomy, retryability, optional status and user-action/challenge metadata;
#        optional old-build information and pure next-call guidance; outgoing string fields are redacted.
# DEPENDS: M-REDACT,M-BUILD-AGE,M-SOURCES; FastMCP ToolError; stdlib json,re,collections.abc,enum,typing.
# LINKS: M-ERRORS; M-CORE; M-HTTP; M-BROWSER-HANDOFF; M-REDACT; R-OLD-BUILD-INFO; R-ERROR-NEXT-STEP.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# ErrorCode: Enumerate stable wire codes and their retryability.
# ConnectorError: Carry provider/status/retry metadata and serialize failures.
# AuthMissingError: Represent missing authentication with status 401.
# RateLimitedError: Represent provider throttling and optional retry delay.
# UpstreamTimeoutError: Represent upstream request deadlines.
# TransportDownError: Represent transport refusal or outage.
# ParserDriftError: Represent upstream parser/schema drift.
# BadRequestError: Represent invalid requests with status 400.
# NotFoundError: Represent unavailable resources with status 404.
# PermissionDeniedError: Represent forbidden access with status 403.
# ChallengeRequiredError: Add retry timing, user action, challenge and optional handoff metadata.
# error_next_step: Derive one recovery sentence for every ErrorCode from supplied context.
# raise_tool_error: Redact and raise a FastMCP ToolError from a serialized connector error.
# raise_tool_error_from: Raise the ToolError for any exception, keeping a ConnectorError's fields.
# END_MODULE_MAP
from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from typing import Any, NoReturn

from fastmcp.exceptions import ToolError

from mcp_core.build_age import old_build_info
from mcp_core.redact import redact_value
from mcp_core.sources import SourceSpec


class ErrorCode(StrEnum):
    AUTH_MISSING = "auth_missing"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT_DOWN = "transport_down"
    PARSER_DRIFT = "parser_drift"
    BAD_REQUEST = "bad_request"
    PERMISSION_DENIED = "permission_denied"
    CHALLENGE_REQUIRED = "challenge_required"
    NOT_FOUND = "not_found"

    @property
    def retryable(self) -> bool:
        return self in (
            ErrorCode.RATE_LIMITED,
            ErrorCode.TIMEOUT,
            ErrorCode.TRANSPORT_DOWN,
            ErrorCode.CHALLENGE_REQUIRED,
        )


class ConnectorError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        provider: str | None = None,
        retry_after_s: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retry_after_s = retry_after_s
        self.status_code = status_code

    def to_dict(self) -> dict:
        return {
            "error": self.code.value,
            "message": str(self),
            "retryable": self.code.retryable,
            **({"provider": self.provider} if self.provider else {}),
            **({"retry_after_s": self.retry_after_s} if self.retry_after_s is not None else {}),
            **({"status_code": self.status_code} if self.status_code is not None else {}),
        }


class AuthMissingError(ConnectorError):
    def __init__(self, message: str = "authentication missing or invalid", *, provider: str | None = None) -> None:
        super().__init__(ErrorCode.AUTH_MISSING, message, provider=provider, status_code=401)


class RateLimitedError(ConnectorError):
    def __init__(self, provider: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(
            ErrorCode.RATE_LIMITED,
            f"{provider} rate-limited",
            provider=provider,
            retry_after_s=retry_after_s,
            status_code=429,
        )


class UpstreamTimeoutError(ConnectorError):
    def __init__(self, message: str = "upstream request timed out", *, provider: str | None = None) -> None:
        super().__init__(ErrorCode.TIMEOUT, message, provider=provider)


class TransportDownError(ConnectorError):
    def __init__(
        self,
        message: str = "upstream transport error",
        *,
        provider: str | None = None,
        status_code: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.TRANSPORT_DOWN, message, provider=provider, status_code=status_code, retry_after_s=retry_after_s
        )


class ParserDriftError(ConnectorError):
    def __init__(self, message: str = "upstream response parser drift", *, provider: str | None = None) -> None:
        super().__init__(ErrorCode.PARSER_DRIFT, message, provider=provider)


class BadRequestError(ConnectorError):
    def __init__(self, message: str = "bad request") -> None:
        super().__init__(ErrorCode.BAD_REQUEST, message, status_code=400)


class NotFoundError(ConnectorError):
    def __init__(self, message: str = "resource not found", *, provider: str | None = None) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, provider=provider, status_code=404)


class PermissionDeniedError(ConnectorError):
    def __init__(self, message: str = "permission denied") -> None:
        super().__init__(ErrorCode.PERMISSION_DENIED, message, status_code=403)


class ChallengeRequiredError(ConnectorError):
    """A marketplace requires a user-mediated browser challenge completion.

    R-CHALLENGE-ACTION: the serialized fields alone must tell the agent that a
    plain retry now cannot pass the challenge (``retryable_now`` is false even
    though the code stays ``retryable`` as documented), that a human must act,
    what that action is (``user_action`` names the real CLI command or the tab
    this error kept open) and when to call again (``retry_when``). The message
    never promises a tab when retention is off: ``user_action`` is derived from
    the handoff fields, which appear only when ``CHROME_CHALLENGE_HANDOFF_S``
    kept the page, and from the explicit override a source passes when its own
    tier cannot reach the CLI's browser-backed path.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        challenge_type: str = "captcha",
        handoff_expires_at: str | None = None,
        handoff_id: str | None = None,
        user_action: str | None = None,
    ) -> None:
        super().__init__(ErrorCode.CHALLENGE_REQUIRED, message, provider=provider, status_code=403)
        self.challenge_type = challenge_type
        self.handoff_expires_at = handoff_expires_at
        self.handoff_id = handoff_id
        # A source whose tier is not the CLI's browser-backed path passes the
        # exact manual step; the derived action below is right for the rest.
        self.user_action = user_action

    def _derived_user_action(self) -> str:
        """The one action that actually unblocks this configuration.

        Retention is the switch: a retained page is completed in place, while
        with retention off the operator opens one through the CLI. Never claim
        a kept-open tab when there is none.
        """
        if self.user_action:
            return self.user_action
        if self.handoff_id or self.handoff_expires_at:
            handle = self.handoff_id or self.handoff_expires_at
            return (
                f"Complete the challenge in the browser tab this tool kept open ({handle}); "
                "run `marketplace-mcp browser show` if the window is off-screen. Then call this "
                "tool again after the user confirms it is done."
            )
        if self.provider:
            return (
                f"Run `marketplace-mcp unblock {self.provider}` to open the challenge page in the "
                "scraping-profile Chrome and have the user complete it there. Then call this tool "
                "again after the user confirms it is done."
            )
        return (
            "Open the source page in the scraping-profile Chrome (run `marketplace-mcp browser show` "
            "if the window is off-screen) and have the user complete the challenge. Then call this "
            "tool again after the user confirms it is done."
        )

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "requires_user_action": True,
            "retryable_now": False,
            "retry_when": "after_user_confirmation",
            "user_action": self._derived_user_action(),
            "challenge_type": self.challenge_type,
            **({"handoff_expires_at": self.handoff_expires_at} if self.handoff_expires_at else {}),
            **({"handoff_id": self.handoff_id} if self.handoff_id else {}),
        }


# START_BLOCK_NEXT_STEP


def error_next_step(
    code: ErrorCode,
    tool: str,
    payload: Mapping[str, Any],
    *,
    source: SourceSpec | None = None,
    parameters: Sequence[str] = (),
    available: Collection[str] | None = None,
) -> str:
    """R-ERROR-NEXT-STEP: one recovery sentence, purely from supplied error/context data.

    No clock, environment, connector import or I/O: the middleware supplies the
    source, the tool's real parameter names and the tools this server lists, and
    fills a missing retry delay. A tool the server does not list (a standalone
    server has no `compare_prices` or `marketplace_health`) is never named;
    ``available=None`` means every tool is listed. The challenge's existing
    user_action stays authoritative and unmodified.
    """

    def listed(name: str | None) -> bool:
        return name is not None and (available is None or name in available)

    if code in (ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.TRANSPORT_DOWN):
        if code == ErrorCode.RATE_LIMITED and source is not None and not source.retry_rate_limit:
            cover = "`compare_prices` covers the other sources; " if listed("compare_prices") else ""
            return f"Do not retry `{tool}` now; {cover}tell the user this source is limited."
        return f"Call `{tool}` again with the same arguments after {payload['retry_after_s']:g} seconds."
    if code == ErrorCode.PARSER_DRIFT:
        health = "call `marketplace_health` for the source state; " if listed("marketplace_health") else ""
        step = f"Do not retry; {health}this source is unavailable until a fix."
        if payload.get("old_build"):
            step = "Run `uvx ru-marketplace-mcp@latest` if a newer version exists; " + step
        return step
    if code == ErrorCode.NOT_FOUND:
        listing = source.listing_tool if source is not None else None
        if listed(listing):
            return f"Check the id or URL; call `{listing}` to find the item."
        if listed("compare_prices"):
            return "Check the id or URL and source; call `compare_prices` to find an available item."
        return "Check the id or URL; this item is not available from this source."
    if code == ErrorCode.BAD_REQUEST:
        message = str(payload.get("message", ""))
        words = {word.lower() for word in re.findall(r"[A-Za-z0-9]+", message)}
        mentioned = [
            name
            for name in parameters
            if re.search(rf"\b{re.escape(name)}\b", message)
            or any(part in words for part in name.lower().split("_") if part not in {"or", "and", "of", "to"})
        ]
        if not mentioned:
            # The message names no parameter, not even a part of one ("invalid
            # item URL" names item_id_or_url): guessing would send the agent to
            # fix the wrong argument.
            return f"Correct the arguments the message describes, using `{tool}`'s schema, then call `{tool}` again."
        parameter = ", ".join(mentioned)
        return f"Correct the {parameter} parameter using `{tool}`'s schema, then call `{tool}` again."
    if code == ErrorCode.PERMISSION_DENIED:
        refused = re.sub(r"[.!?]+(?:\s+|$)", "; ", str(payload.get("message", "access"))).strip(" ;")
        return f"Access for `{tool}` is refused ({refused}); do not retry because a retry will not help."
    if code == ErrorCode.AUTH_MISSING:
        setting = source.auth_setting if source is not None else None
        action = (
            f"set `{setting}` in the server environment" if setting else "restore the source's authenticated session"
        )
        return f"Ask the user to {action}, then call `{tool}` again; the agent must not set keys."
    if code == ErrorCode.CHALLENGE_REQUIRED:
        return f"Follow `user_action` and wait until the user confirms completion, then call `{tool}` again."
    raise ValueError(f"No next_step policy for {code}")


# END_BLOCK_NEXT_STEP
# START_BLOCK_TOOL_ERROR_MAPPING


def raise_tool_error(err: ConnectorError) -> NoReturn:
    payload = err.to_dict()
    if err.code in (
        ErrorCode.PARSER_DRIFT,
        ErrorCode.TRANSPORT_DOWN,
        ErrorCode.CHALLENGE_REQUIRED,
        ErrorCode.RATE_LIMITED,
    ):
        info = old_build_info()
        if info is not None:
            payload["old_build"] = info
    raise ToolError(json.dumps(redact_value(payload), ensure_ascii=False))


def raise_tool_error_from(exc: Exception, message: str | None = None, *, provider: str | None = None) -> NoReturn:
    """Raise the ToolError for any exception, keeping a ConnectorError's fields.

    A generic handler that wraps every exception in a fresh TransportDownError
    throws away what the error already knew: a busy browser handoff carries
    409 and retry_after_s, a registry that is full carries its own reason, a
    deadline carries the timeout code, and the provider names the source.
    Those machine fields are what a caller acts on, so a ConnectorError is
    passed through unchanged. Anything else becomes the redacted transport_down
    it always was — an unexpected failure must not invent a taxonomy.

    The handler's provider fills in a missing one, so a connector's own name
    still reaches the answer.
    """
    if isinstance(exc, ConnectorError):
        if provider and not exc.provider:
            exc.provider = provider
        raise_tool_error(exc)
    raise_tool_error(TransportDownError(message or f"{type(exc).__name__}: {exc}", provider=provider))


# END_BLOCK_TOOL_ERROR_MAPPING
