"""Citilink connector runtime settings (env-driven via CITILINK_ prefix)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


class CitilinkSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CITILINK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(default=30.0, gt=0)
    min_gap: float = Field(default=3.0, ge=0)
    cache_ttl: float = Field(default=120.0, ge=0, description="Seconds to cache upstream reads. 0 disables caching.")
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> CitilinkSettings:
    return CitilinkSettings()
