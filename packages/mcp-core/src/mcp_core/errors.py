# START_MODULE_CONTRACT
# PURPOSE: Encode provider-neutral failures as machine-readable MCP ToolErrors.
# SCOPE: Error taxonomy, retryability, optional status and user-action/challenge metadata;
#        message and string fields are redacted on the way out.
# DEPENDS: M-REDACT; FastMCP ToolError; stdlib json,enum,typing.
# LINKS: M-ERRORS; M-CORE; M-HTTP; M-BROWSER-HANDOFF; M-REDACT.
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
# raise_tool_error: Redact and raise a FastMCP ToolError from a serialized connector error.
# raise_tool_error_from: Raise the ToolError for any exception, keeping a ConnectorError's fields.
# END_MODULE_MAP
from __future__ import annotations

import json
from enum import StrEnum
from typing import NoReturn

from fastmcp.exceptions import ToolError

from mcp_core.redact import redact_value


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


def raise_tool_error(err: ConnectorError) -> NoReturn:
    raise ToolError(json.dumps(redact_value(err.to_dict()), ensure_ascii=False))


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
