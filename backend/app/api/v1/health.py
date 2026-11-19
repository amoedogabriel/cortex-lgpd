"""Endpoint GET /api/v1/health verificando conectividade ativa com PostgreSQL 16 e Redis 7."""

import logging
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.health import ComponentHealth, HealthCheckResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Infraestrutura & Observabilidade"])


async def _check_postgresql(session: AsyncSession) -> ComponentHealth:
    """Executa SELECT 1 real no PostgreSQL via sessão assíncrona SQLAlchemy."""
    start = time.perf_counter()
    try:
        result = await session.execute(text("SELECT 1"))
        scalar_val = result.scalar_one()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        if scalar_val == 1:
            return ComponentHealth(status="up", latency_ms=elapsed_ms, detail="PostgreSQL 16 operacional")
        return ComponentHealth(status="down", latency_ms=elapsed_ms, detail="Resposta inesperada do banco")
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("Falha no healthcheck do PostgreSQL", extra={"error": type(exc).__name__})
        return ComponentHealth(
            status="down",
            latency_ms=elapsed_ms,
            detail=f"Falha de conexão ao PostgreSQL ({type(exc).__name__})",
        )


async def _check_redis() -> ComponentHealth:
    """Executa comando PING real no servidor Redis 7."""
    start = time.perf_counter()
    client = aioredis.from_url(
        str(settings.REDIS_URL),
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2.5,
        socket_timeout=2.5,
    )
    try:
        pong = await client.ping()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        if pong is True:
            return ComponentHealth(status="up", latency_ms=elapsed_ms, detail="Redis 7 operacional")
        return ComponentHealth(status="down", latency_ms=elapsed_ms, detail="Resposta inesperada ao comando PING")
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("Falha no healthcheck do Redis", extra={"error": type(exc).__name__})
        return ComponentHealth(
            status="down",
            latency_ms=elapsed_ms,
            detail=f"Falha de conexão ao Redis ({type(exc).__name__})",
        )
    finally:
        await client.aclose()


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    summary="Verifica conectividade com PostgreSQL e Redis",
)
async def health_check(
    response: Response,
    db_session: AsyncSession = Depends(get_db_session),
) -> HealthCheckResponse:
    """
    Realiza sondagem ativa no banco de dados PostgreSQL (`SELECT 1`) e no broker Redis (`PING`).
    Retorna HTTP 200 quando ambos estão operacionais ou HTTP 503 caso alguma dependência falhe.
    """
    pg_health = await _check_postgresql(db_session)
    redis_health = await _check_redis()

    all_up = pg_health.status == "up" and redis_health.status == "up"
    any_up = pg_health.status == "up" or redis_health.status == "up"

    if all_up:
        overall_status = "healthy"
        response.status_code = status.HTTP_200_OK
    elif any_up:
        overall_status = "degraded"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        overall_status = "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthCheckResponse(
        status=overall_status,
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc),
        components={
            "postgresql": pg_health,
            "redis": redis_health,
        },
    )
