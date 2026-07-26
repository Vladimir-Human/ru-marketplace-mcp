"""Detsky Mir connector settings (env prefix ``DETMIR_``).

The Detsky Mir public API needs no credentials, so everything here is an
operational knob: timeouts, politeness gap, body caps, cache TTL and the region
whose prices and stock you want.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DetmirSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DETMIR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(
        default=25.0,
        gt=0,
        description="Per-request timeout in seconds. Category listings are large, so this is higher than WB's.",
    )
    wall_timeout: float = Field(
        default=60.0,
        gt=0,
        description="Hard ceiling for a whole tool call, including retries.",
    )
    min_gap: float = Field(
        default=0.8,
        ge=0,
        description="Minimum seconds between requests. The API is tolerant, but staying polite keeps it that way.",
    )
    max_body_bytes: int = Field(
        default=8_000_000,
        gt=0,
        description="Hard cap on a response body. Listings with facets reach ~700 KB, so the cap sits well above that.",
    )
    net_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries after the first attempt, for transport faults only. The API returns sporadic 502s.",
    )
    net_backoff_s: float = Field(
        default=0.8,
        ge=0,
        description="Base backoff between retries; doubles per attempt.",
    )
    region: str = Field(
        default="RU-MOW",
        min_length=2,
        max_length=16,
        description="ISO region code for prices and stock (RU-MOW = Moscow, RU-SPE = St Petersburg).",
    )
    cache_ttl: float = Field(
        default=120.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching entirely.",
    )
    proxy: str = Field(
        default="",
        description="Optional proxy URL. Empty means honour the standard HTTPS_PROXY/ALL_PROXY variables.",
    )
    selfcheck_product_id: int = Field(
        default=6673568,
        gt=0,
        description="Baseline product id for the drift canary (a long-lived doll SKU with reviews).",
    )
    selfcheck_category: str = Field(
        default="pups",
        min_length=1,
        description="Baseline category alias for the drift canary.",
    )


@lru_cache(maxsize=1)
def get_settings() -> DetmirSettings:
    return DetmirSettings()
