"""MPStats connector settings (pydantic-settings BaseSettings, env_prefix='MPSTATS_').

Unlike the anonymous catalog connectors in this workspace, MPStats requires a
paid account. Authentication is a single JWT cookie, ``mp_auth``, issued by the
browser plugin session at mpstats.io. Set ``MPSTATS_MP_AUTH`` to the raw JWT
value (no ``mp_auth=`` prefix, no quotes) — the connector sends it as a cookie.

The token is a secret: it identifies a paid account and carries a request
quota. Never log it, never commit it. It is held as a pydantic ``SecretStr``, so a settings ``repr`` or
``model_dump`` shows ``**********`` instead of the token, and
``redact_error_text`` strips
cookie-shaped material from error strings; this connector additionally reads it
only into a module-level str used for one outbound header, nowhere else.

Other knobs mirror the WB connector's operational set: timeouts, polite gap,
retry budget, body-size cap, cache TTL, and an optional proxy. The same
invariants apply — a missing value stays ``None`` (never ``0``), a 429 is never
auto-retried, and the polite gap is on because these are shared endpoints.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MPStatsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MPSTATS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mp_auth: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Raw mp_auth JWT cookie value for the MPStats plugin API. "
            "Required for any data; without it the tools return auth_missing "
            "rather than failing. Obtain it from the logged-in browser plugin "
            "session (mpstats.io). NEVER commit this value."
        ),
    )
    timeout: float = Field(
        default=15.0,
        gt=0,
        description="Per-request HTTP timeout in seconds (connect+read combined).",
    )
    wall_timeout: float = Field(
        default=45.0,
        gt=0,
        description="Whole-request wall-clock budget in seconds, bounding retries.",
    )
    min_gap: float = Field(
        # MPStats' offer (clause 5.1.1) makes "more than one request per five
        # seconds, sustained over thirty" grounds for blocking the account —
        # and clause 5.2 keeps the money. The default therefore sits on the
        # documented limit, not on what the endpoint tolerates.
        default=5.0,
        ge=0,
        description="Polite gap between MPStats requests in seconds (shared endpoint, quota-billed).",
    )
    max_body_bytes: int = Field(
        default=2097152,
        ge=1024,
        description="Hard cap on response body bytes (MPStats responses are small JSON; 2 MB is generous).",
    )
    net_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Transport-level retries after the first try (httpx transport errors only).",
    )
    net_backoff_s: float = Field(
        default=0.8,
        ge=0,
        description="Backoff between transport retries in seconds.",
    )
    cache_ttl: float = Field(
        default=120.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching. The same SKU is walked repeatedly across item/warehouses calls.",
    )
    proxy: str = Field(
        default="",
        description="Optional proxy URL. Empty means honour the standard HTTPS_PROXY/ALL_PROXY variables.",
    )


@lru_cache(maxsize=1)
def get_settings() -> MPStatsSettings:
    return MPStatsSettings()
