"""Ozon connector runtime settings (env-driven via OZON_ prefix).

Env vars (all optional, defaults match the pre-settings constants):
  OZON_TIMEOUT         - per-tier HTTP/CDP timeout seconds, default 20
  OZON_MAX_BODY_BYTES  - hard cap on any HTTP response body, default 50 MiB
  OZON_MIN_GAP         - polite inter-request gap seconds, default 2.5
  OZON_IMPERSONATE     - curl_cffi fingerprint profile, default "chrome"
  OZON_SELFCHECK_SKU   - golden-fixture SKU for ozon_selfcheck, default 3015796642
  OZON_CACHE_TTL       - seconds to cache upstream reads, 0 disables, default 120
  OZON_PROXY           - proxy URL for the tier-1 fetch, default unset (honours HTTPS_PROXY)

Settings are read once at import; callers that need to override in tests
patch the module-level constants in server.py (TIMEOUT, _min_gap, ...) as
before. This module is the single source of truth for env-driven defaults.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


class OzonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OZON_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(default=20.0, gt=0)
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)
    min_gap: float = Field(default=2.5, ge=0)
    impersonate: str = Field(default="chrome", min_length=1)
    selfcheck_sku: str = Field(default="3015796642", min_length=1)
    cache_ttl: float = Field(
        default=120.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching.",
    )
    proxy: str = Field(
        default="",
        description="Optional proxy URL for the tier-1 impersonation fetch. Empty honours HTTPS_PROXY/ALL_PROXY.",
    )


@lru_cache(maxsize=1)
def get_settings() -> OzonSettings:
    return OzonSettings()
