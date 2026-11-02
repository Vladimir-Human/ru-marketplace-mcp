"""Cian connector runtime settings (env-driven via CIAN_ prefix).

Env vars (all optional):
  CIAN_TIMEOUT         - per-call CDP timeout seconds, default 25
  CIAN_MAX_BODY_BYTES  - hard cap on any body read inside the browser, default 50 MiB
  CIAN_MIN_GAP         - polite inter-request gap seconds, default 1.5
  CIAN_CACHE_TTL       - seconds to cache upstream reads, 0 disables, default 120
  CIAN_REGION          - default Cian region id for search, default "1" (Moscow)

There is no proxy setting: every read runs inside the operator's Chrome over
CDP (Cian's WAF blocks plain HTTP by IP reputation — see docs/ANTI_BOT.md), so
the browser's own network path is the only one in play.

Settings are read once at import; tests patch the module-level constants in
server.py. This module is the single source of truth for env-driven defaults.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


class CianSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CIAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(default=25.0, gt=0)
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)
    min_gap: float = Field(default=1.5, ge=0)
    cache_ttl: float = Field(
        default=120.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching.",
    )
    region: str = Field(
        default="1",
        min_length=1,
        description=(
            "Default Cian region id (1 = Москва, 2 = Санкт-Петербург, 4593 = Московская область, "
            "4588 = Ленинградская область). Overridden per call by the region argument."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> CianSettings:
    return CianSettings()
