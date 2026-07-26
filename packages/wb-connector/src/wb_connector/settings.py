"""WB connector settings (pydantic-settings BaseSettings, env_prefix='WB_').

All tunable parameters are configurable via environment variables with a WB_
prefix, e.g. WB_TIMEOUT, WB_WALL_TIMEOUT, WB_MIN_GAP, WB_DEFAULT_DEST,
WB_MAX_BODY_BYTES, WB_NET_RETRIES, WB_NET_BACKOFF_S, WB_CACHE_TTL, WB_PROXY.

WB public catalog APIs need no credentials, so this settings object holds only
operational knobs — timeouts, rate-limit gap, retry budget, body-size cap, cache
TTL, and proxy.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WBSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
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
        default=1.5,
        ge=0,
        description="Polite gap between WB requests in seconds (residential-IP rate limit).",
    )
    default_dest: str = Field(
        default="-1257786",
        description="Default WB region ID. -1257786 = Moscow. Required for valid prices/stocks.",
    )
    max_body_bytes: int = Field(
        default=52428800,
        ge=1024,
        description="Hard cap on response body bytes (CDN compromise / MITM defense).",
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
        description="Seconds to cache upstream reads. 0 disables caching. An agent asking price, then reviews, then a comparison hits the same SKU repeatedly.",
    )
    proxy: str = Field(
        default="",
        description="Optional proxy URL. Empty means honour the standard HTTPS_PROXY/ALL_PROXY variables.",
    )
    basket_fallback: str = Field(
        default="basket-28",
        min_length=1,
        pattern=r"^basket-\d+$",
        description="Fallback basket host prefix when SKU vol is not in the lookup table. Must match 'basket-N' pattern.",
    )


@lru_cache(maxsize=1)
def get_settings() -> WBSettings:
    return WBSettings()
