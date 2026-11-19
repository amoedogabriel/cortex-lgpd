"""Schemas Pydantic v2 para verificação de saúde e conectividade (PostgreSQL e Redis)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ComponentHealth(BaseModel):
    """Estado de conectividade e latência de uma dependência de infraestrutura."""

    status: Literal["up", "down"]
    latency_ms: float | None = Field(default=None, ge=0.0)
    detail: str | None = None


class HealthCheckResponse(BaseModel):
    """Resposta consolidada do endpoint GET /api/v1/health."""

    status: Literal["healthy", "degraded", "unhealthy"]
    service: str
    version: str
    environment: str
    timestamp: datetime
    components: dict[str, ComponentHealth]
