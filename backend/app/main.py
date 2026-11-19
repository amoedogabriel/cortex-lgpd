"""Ponto de entrada da aplicação FastAPI (Monólito Modular — UFAM)."""

import logging
import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import sanitize_text_anti_pii, setup_logging
from app.core.ml_models import ml_registry
from app.db.session import engine

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gerencia inicialização de diretórios temporários, pre-warming de ML e conexões."""
    settings.TEMP_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    warmup_metrics = ml_registry.warmup(load_ocr=True, load_spacy=True)
    logger.info(
        "Inicializando Backend do Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM",
        extra={
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "temp_storage_dir": str(settings.TEMP_STORAGE_DIR),
            "ml_warmup": warmup_metrics,
        },
    )
    yield
    await engine.dispose()
    logger.info("Conexões do banco de dados encerradas com segurança.")


def create_application() -> FastAPI:
    """Factory de criação da instância FastAPI."""
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Aplicação web institucional da Universidade Federal do Amazonas (UFAM) destinada "
            "à identificação automatizada e tarjamento definitivo (true redaction) de dados "
            "pessoais e sensíveis em documentos PDF, em conformidade com a LGPD (Lei 13.709/2018) "
            "e LAI (Lei 12.527/2011)."
        ),
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url=f"{settings.API_V1_PREFIX}/docs",
        redoc_url=f"{settings.API_V1_PREFIX}/redoc",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)

    @application.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Captura qualquer exceção inesperada garantindo log de traceback e header CORS."""
        safe_tb = sanitize_text_anti_pii(traceback.format_exc())
        logger.error(
            f"Erro interno não tratado em {request.method} {request.url.path}: {type(exc).__name__}\n{safe_tb}"
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": sanitize_text_anti_pii(str(exc)),
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Ocorreu um erro interno inesperado ao processar a solicitação.",
                    "details": {},
                },
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    application.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    return application


app = create_application()
