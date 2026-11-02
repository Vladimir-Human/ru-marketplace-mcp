"""Megamarket connector runtime settings (env-driven via MEGAMARKET_ prefix)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024


class MegamarketSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEGAMARKET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(default=30.0, gt=0)
    min_gap: float = Field(default=3.0, ge=0)
    cache_ttl: float = Field(default=120.0, ge=0, description="Seconds to cache upstream reads. 0 disables caching.")
    max_body_bytes: int = Field(default=_DEFAULT_MAX_BODY_BYTES, gt=0)
    address: str = Field(
        default="Москва",
        description=(
            "Delivery address used to resolve an addressId. Megamarket answers a search without one "
            "with listingSize>0 and an empty items array: it finds the products but no deliverable "
            "offer. A logged-in profile's default address wins over this value only when "
            "use_profile_address is enabled; by default this city is what search uses."
        ),
    )
    use_profile_address: bool = Field(
        default=False,
        description=(
            "Opt-in: resolve the delivery address from the logged-in profile's default address by "
            "reading the private account endpoint /profileService/address/list in the operator's "
            "Chrome. On = prices and availability exactly as the operator's own profile sees them, "
            "but a private account endpoint is read. Off (default) = only the public city-level "
            "suggest endpoint is used, driven by MEGAMARKET_ADDRESS."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> MegamarketSettings:
    return MegamarketSettings()
