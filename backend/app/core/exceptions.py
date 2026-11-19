"""Exceções centralizadas de domínio e infraestrutura e handlers HTTP do FastAPI."""

import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import sanitize_structure_anti_pii, sanitize_text_anti_pii

logger = logging.getLogger(__name__)

_CORS_ERROR_HEADERS: dict[str, str] = {"Access-Control-Allow-Origin": "*"}


class AppError(Exception):
    """Exceção base para todos os erros controlados da aplicação."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = sanitize_text_anti_pii(message)
        self.code = code
        self.status_code = status_code
        self.details = sanitize_structure_anti_pii(details or {})
        super().__init__(self.message)


class ResourceNotFoundError(AppError):
    """Recurso solicitado (Job, Usuário, Log de Auditoria) não encontrado."""

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(
            message=f"{resource} com identificador '{identifier}' não encontrado.",
            code="RESOURCE_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"resource": resource, "identifier": identifier},
        )


class InvalidDocumentError(AppError):
    """Documento enviado não é um PDF válido, excede limites ou encontra-se corrompido."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message=message,
            code="INVALID_DOCUMENT",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class FileTooLargeError(AppError):
    """Arquivo ultrapassa o limite máximo configurado em MAX_UPLOAD_SIZE_BYTES."""

    def __init__(self, max_bytes: int, received_bytes: int) -> None:
        super().__init__(
            message=f"O arquivo excede o tamanho máximo permitido de {max_bytes} bytes.",
            code="FILE_TOO_LARGE",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            details={"max_bytes": max_bytes, "received_bytes": received_bytes},
        )


class AuthenticationError(AppError):
    """Falha na validação do token JWT OIDC/Keycloak."""

    def __init__(self, message: str = "Credenciais de autenticação inválidas ou expiradas.") -> None:
        super().__init__(
            message=message,
            code="AUTHENTICATION_FAILED",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AuthorizationError(AppError):
    """Usuário autenticado não possui papel RBAC suficiente para a operação."""

    def __init__(self, message: str = "Permissão insuficiente para executar esta operação.") -> None:
        super().__init__(
            message=message,
            code="INSUFFICIENT_PERMISSIONS",
            status_code=status.HTTP_403_FORBIDDEN,
        )


class RedactionPipelineError(AppError):
    """Erro crítico durante a extração, OCR, redação física ou sanitização estrutural do PDF."""

    def __init__(self, message: str, stage: str) -> None:
        super().__init__(
            message=message,
            code="REDACTION_PIPELINE_FAILURE",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"stage": stage},
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Registra os tratadores globais de exceção na instância FastAPI garantindo Zero-PII e headers CORS."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "Erro de domínio/aplicação capturado",
            extra={
                "error_code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
                "details": exc.details,
            },
        )
        headers = dict(_CORS_ERROR_HEADERS)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Sanitiza erros de validação do Pydantic para nunca ecoar PII enviada no input
        sanitized_errors = []
        for err in exc.errors():
            safe_err = {
                "loc": err.get("loc"),
                "msg": sanitize_text_anti_pii(str(err.get("msg", ""))),
                "type": err.get("type"),
            }
            sanitized_errors.append(safe_err)

        logger.info(
            "Falha de validação de requisição",
            extra={"path": request.url.path, "errors": sanitized_errors},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            headers=dict(_CORS_ERROR_HEADERS),
            content={
                "error": {
                    "code": "REQUEST_VALIDATION_ERROR",
                    "message": "Os parâmetros fornecidos na requisição são inválidos.",
                    "details": {"errors": sanitized_errors},
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        safe_tb = sanitize_text_anti_pii(traceback.format_exc())
        logger.error(
            "Exceção não tratada na camada HTTP",
            extra={
                "path": request.url.path,
                "method": request.method,
                "exception_type": type(exc).__name__,
                "traceback": safe_tb,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            headers=dict(_CORS_ERROR_HEADERS),
            content={
                "detail": sanitize_text_anti_pii(str(exc)),
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Ocorreu um erro interno inesperado ao processar a solicitação.",
                    "details": {},
                },
            },
        )
