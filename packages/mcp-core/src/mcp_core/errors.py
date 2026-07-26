from __future__ import annotations

import json
from enum import StrEnum
from typing import NoReturn

from fastmcp.exceptions import ToolError


class ErrorCode(StrEnum):
    AUTH_MISSING = "auth_missing"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT_DOWN = "transport_down"
    PARSER_DRIFT = "parser_drift"
    BAD_REQUEST = "bad_request"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"

    @property
    def retryable(self) -> bool:
        return self in (ErrorCode.RATE_LIMITED, ErrorCode.TIMEOUT, ErrorCode.TRANSPORT_DOWN)


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
        self, message: str = "upstream transport error", *, provider: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(ErrorCode.TRANSPORT_DOWN, message, provider=provider, status_code=status_code)


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


def raise_tool_error(err: ConnectorError) -> NoReturn:
    raise ToolError(json.dumps(err.to_dict(), ensure_ascii=False))
