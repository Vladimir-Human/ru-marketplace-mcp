"""AliExpress connector runtime settings (env-driven via ALI_ prefix)."""
# START_MODULE_CONTRACT
# PURPOSE: Validate environment-driven AliExpress read budgets and cache settings.
# SCOPE: Positive finite navigation timeout in seconds; no browser or network access.
# DEPENDS: Pydantic; pydantic-settings; stdlib functools.
# LINKS: M-ALIEXPRESS; R-CDP-NAV-TIMEOUT.
# END_MODULE_CONTRACT
# START_MODULE_MAP
# AliSettings: Load ALI_ settings; ALI_TIMEOUT bounds search/card landing navigation.
# get_settings: Cache one validated settings instance for this server process.
# END_MODULE_MAP

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


# START_BLOCK_SETTINGS
class AliSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(
        default=45.0,
        gt=0,
        allow_inf_nan=False,
        description="Seconds allowed for search and card-landing navigation; separate tool ceilings still apply.",
    )
    min_gap: float = Field(default=3.0, ge=0)
    cache_ttl: float = Field(default=120.0, ge=0, description="Seconds to cache upstream reads. 0 disables caching.")
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> AliSettings:
    return AliSettings()


# END_BLOCK_SETTINGS
