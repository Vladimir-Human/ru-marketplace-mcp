"""Taobao connector runtime settings (env-driven via TAOBAO_ prefix).

Env vars (all optional):
  TAOBAO_TIMEOUT         - CDP page/evaluation timeout seconds, default 30
  TAOBAO_MIN_GAP         - polite inter-request gap seconds, default 3.0
  TAOBAO_CACHE_TTL       - seconds to cache upstream reads, 0 disables, default 120
  TAOBAO_MAX_BODY_BYTES  - hard cap on serialized page data, default 50 MiB

There is no impersonate/proxy setting on purpose: Taobao search is a signed
client-side API, so every read runs in the operator's own Chrome over CDP and
uses that browser's network — a proxy here would be the browser's business, not
ours.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


class TaobaoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TAOBAO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(default=30.0, gt=0)
    min_gap: float = Field(default=3.0, ge=0)
    cache_ttl: float = Field(
        default=120.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching.",
    )
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> TaobaoSettings:
    return TaobaoSettings()
