"""Avito connector runtime settings (env-driven via AVITO_ prefix).

Env vars (all optional):
  AVITO_TIMEOUT         - per-tier HTTP/CDP timeout seconds, default 20
  AVITO_MAX_BODY_BYTES  - hard cap on any HTTP response body, default 50 MiB
  AVITO_MIN_GAP         - polite inter-request gap seconds, default 2.5
  AVITO_IMPERSONATE     - curl_cffi fingerprint profile, default "chrome"
  AVITO_CACHE_TTL       - seconds to cache upstream reads, 0 disables, default 120
  AVITO_PROXY           - proxy URL for the tier-1 fetch, default unset (honours HTTPS_PROXY)
  AVITO_LOCATION_ID     - default Avito location id for search, default 637640 (Moscow)

Settings are read once at import; tests patch the module-level constants in
server.py. This module is the single source of truth for env-driven defaults.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


class AvitoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVITO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(default=20.0, gt=0)
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)
    min_gap: float = Field(default=2.5, ge=0)
    impersonate: str = Field(default="chrome", min_length=1)
    cache_ttl: float = Field(
        default=120.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching.",
    )
    proxy: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Optional proxy URL for the tier-1 impersonation fetch. Empty honours HTTPS_PROXY/ALL_PROXY. "
            "May carry user:pass credentials, so it is a SecretStr: repr/dump show '**********', "
            "and only the outbound fetch ever unwraps it."
        ),
    )
    location_id: str = Field(
        default="637640",
        min_length=1,
        description="Default Avito location id (637640 = Moscow). Overridden per call by the location_id argument.",
    )


@lru_cache(maxsize=1)
def get_settings() -> AvitoSettings:
    return AvitoSettings()
