from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    DISABLED = "disabled"


class ComponentHealth(BaseModel):
    name: str
    state: HealthState
    detail: str | None = None
    latency_ms: float | None = None
    last_error: str | None = None


class SelfCheckResponse(BaseModel):
    ok: bool
    transport: str = "stdio"
    server_version: str = ""
    config_loaded: bool = False
    components: list[ComponentHealth] = Field(default_factory=list)
    tool_count: int = 0
    timestamp: str = ""
