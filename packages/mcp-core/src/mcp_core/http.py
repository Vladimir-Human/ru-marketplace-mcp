from __future__ import annotations

import email.utils
import math
from functools import lru_cache

import httpx


@lru_cache(maxsize=1)
def get_timeout(
    timeout_s: float = 15.0,
    *,
    connect_s: float = 5.0,
    read_s: float | None = None,
    write_s: float = 5.0,
    pool_s: float = 2.0,
) -> httpx.Timeout:
    return httpx.Timeout(
        timeout_s,
        connect=connect_s,
        read=read_s if read_s is not None else timeout_s,
        write=write_s,
        pool=pool_s,
    )


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header into a delay in seconds, or None.

    The header is wire-authored, so it gets the coercion doctrine: a value that
    floats to infinity or NaN (``1e999``, ``inf``) is no delay at all and must
    never become an infinite or corrupting sleep, and a negative delay is a
    moment in the past — clamped to 0.0 exactly like the date branch, so both
    forms agree on "retry now".
    """
    if not value:
        return None
    value = value.strip()
    try:
        delay = float(value)
    except ValueError:
        pass
    else:
        if math.isfinite(delay):
            return max(0.0, delay)
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt:
            import time

            return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        pass
    return None


def classify_http_error(status_code: int, provider: str | None = None, body: str = "") -> Exception:
    """Map an HTTP status onto the shared error vocabulary.

    Imported inside the function because ``errors`` imports nothing from here and
    a module-level import would close the cycle the other way round.
    """
    from mcp_core.errors import (
        AuthMissingError,
        BadRequestError,
        RateLimitedError,
        TransportDownError,
    )

    if status_code == 401 or status_code == 403:
        return AuthMissingError(f"auth failed ({status_code})", provider=provider)
    if status_code == 429:
        return RateLimitedError(provider or "upstream")
    if status_code == 404:
        from mcp_core.errors import ConnectorError, ErrorCode

        return ConnectorError(
            ErrorCode.NOT_FOUND, f"not found ({status_code})", provider=provider, status_code=status_code
        )
    if 400 <= status_code < 500:
        return BadRequestError(f"client error ({status_code}): {body[:200]}")
    if status_code >= 500:
        return TransportDownError(f"server error ({status_code})", provider=provider, status_code=status_code)
    return TransportDownError(f"unexpected status {status_code}", provider=provider, status_code=status_code)
