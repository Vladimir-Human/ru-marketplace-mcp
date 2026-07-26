"""Yandex Market connector settings (env prefix ``YANDEX_``).

No credentials: Yandex Market's public pages need none. Everything here is
operational — timeouts, politeness gap, body caps, cache TTL, proxy.

Body caps are deliberately generous. A search page is ~2 MB and a product page
~2.5 MB, because the entire widget state ships inside the HTML.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class YandexSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YANDEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    timeout: float = Field(
        default=40.0,
        gt=0,
        description="Per-request timeout in seconds. Pages are 2-2.5 MB, so this is well above the other connectors'.",
    )
    min_gap: float = Field(
        default=1.5,
        ge=0,
        description="Minimum seconds between requests. Yandex tolerates a steady pace; bursts invite SmartCaptcha.",
    )
    max_body_bytes: int = Field(
        default=12_000_000,
        gt=0,
        description="Hard cap on a response body. Product pages reach ~2.5 MB, so the cap leaves generous headroom.",
    )
    net_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries after the first attempt. Yandex intermittently answers 302 with an empty body.",
    )
    net_backoff_s: float = Field(
        default=1.0,
        ge=0,
        description="Base backoff between retries; doubles per attempt.",
    )
    cache_ttl: float = Field(
        default=180.0,
        ge=0,
        description="Seconds to cache upstream reads. 0 disables caching. Higher than other connectors — pages are heavy.",
    )
    proxy: str = Field(
        default="",
        description="Optional proxy URL. Empty means honour the standard HTTPS_PROXY/ALL_PROXY variables.",
    )
    selfcheck_query: str = Field(
        default="телефон",
        min_length=2,
        description="Baseline search query for the drift canary — a high-volume term that always returns results.",
    )


@lru_cache(maxsize=1)
def get_settings() -> YandexSettings:
    return YandexSettings()
