from __future__ import annotations

import email.utils
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
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt:
            import time

            return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        pass
    return None


def classify_http_error(status_code: int, provider: str | None = None, body: str = "") -> Exception:
    from mcp_common.errors import (
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
        from mcp_common.errors import ConnectorError, ErrorCode

        return ConnectorError(
            ErrorCode.NOT_FOUND, f"not found ({status_code})", provider=provider, status_code=status_code
        )
    if 400 <= status_code < 500:
        return BadRequestError(f"client error ({status_code}): {body[:200]}")
    if status_code >= 500:
        return TransportDownError(f"server error ({status_code})", provider=provider, status_code=status_code)
    return TransportDownError(f"unexpected status {status_code}", provider=provider, status_code=status_code)
