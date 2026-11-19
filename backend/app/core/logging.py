"""Logging estruturado em formato JSON com sanitização rigorosa Anti-PII (Zero-PII Logging)."""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

# Padrões de dados pessoais que JAMAIS podem constar literalmente em logs de aplicação
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # CPF formatado (000.000.000-00) ou contíguo com marcador contextual
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[REDACTED_CPF]"),
    (re.compile(r"(?i)(cpf\s*[:=]?\s*)\d{11}\b"), r"\1[REDACTED_CPF]"),
    # Título de Eleitor rotulado ou formatado em blocos
    (
        re.compile(
            r"(?i)(t[íi]t(?:ulo)?(?:\s+de)?\s*eleitor(?:al)?\s*[:=]?\s*)(?:\d{4}[\s.]\d{4}[\s.]\d{4}|\d{10}-\d{2}|\d{12})\b"
        ),
        r"\1[REDACTED_TITULO]",
    ),
    # E-mail
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Telefone brasileiro ((92) 99999-9999 / +55 92 99999-9999)
    (re.compile(r"(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}-\d{4}\b"), "[REDACTED_PHONE]"),
    # Cartão de crédito / débito (13 a 16 dígitos agrupados)
    (re.compile(r"\b(?:\d{4}[-\s]){3}\d{4}\b"), "[REDACTED_CARD]"),
    # Matrícula SIAPE ou UFAM rotulada
    (re.compile(r"(?i)(siape\s*[:=]?\s*)\d{6,9}\b"), r"\1[REDACTED_SIAPE]"),
    (re.compile(r"(?i)(matr[ií]cula\s*[:=]?\s*)\d{7,12}\b"), r"\1[REDACTED_MATRICULA]"),
    # RG rotulado
    (re.compile(r"(?i)(\brg\s*[:=]?\s*)[0-9.\-]{5,14}\b"), r"\1[REDACTED_RG]"),
    # Chave PIX aleatória ou rotulada
    (re.compile(r"(?i)(pix\s*[:=]?\s*)[^\s,;]+"), r"\1[REDACTED_PIX]"),
]

# Chaves de dicionário potencialmente portadoras de PII em payloads ou extra fields
_FORBIDDEN_LOG_KEYS: frozenset[str] = frozenset(
    {
        "cpf",
        "rg",
        "cnh",
        "passaporte",
        "titulo_eleitor",
        "nome_completo",
        "full_name",
        "raw_text",
        "extracted_text",
        "pii_value",
        "literal_value",
        "telefone",
        "phone",
        "endereco",
        "address",
        "pix",
        "conta_bancaria",
        "agencia",
        "cid",
        "prontuario",
        "password",
        "secret",
        "authorization",
        "access_token",
    }
)


def sanitize_text_anti_pii(value: str) -> str:
    """Remove qualquer padrão conhecido de dado pessoal de uma string antes de logar."""
    sanitized = value
    for pattern, replacement in _PII_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_structure_anti_pii(obj: Any) -> Any:
    """Percorre recursivamente dicts, listas e strings mascarando chaves e padrões proibidos."""
    if isinstance(obj, str):
        return sanitize_text_anti_pii(obj)
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if key_lower in _FORBIDDEN_LOG_KEYS:
                cleaned[str(k)] = "[REDACTED_BY_ZERO_PII_POLICY]"
            else:
                cleaned[str(k)] = sanitize_structure_anti_pii(v)
        return cleaned
    if isinstance(obj, (list, tuple, set)):
        return [sanitize_structure_anti_pii(item) for item in obj]
    return obj


class ZeroPIIFilter(logging.Filter):
    """Filtro de logging que intercepta e higieniza mensagens e argumentos contra vazamento de PII."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_text_anti_pii(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_structure_anti_pii(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize_structure_anti_pii(arg) for arg in record.args)
        return True


class StructuredJSONFormatter(logging.Formatter):
    """Formata registros de log em JSON estruturado compatível com SIEM/observabilidade institucional."""

    _STANDARD_ATTRS = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        raw_message = record.getMessage()
        safe_message = sanitize_text_anti_pii(raw_message)

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": settings.APP_NAME,
            "environment": settings.ENVIRONMENT,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": safe_message,
        }

        # Adiciona atributos extras passados via `extra={...}`, higienizando cada chave
        extra_fields: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS and not key.startswith("_"):
                extra_fields[key] = value

        if extra_fields:
            payload["context"] = sanitize_structure_anti_pii(extra_fields)

        if record.exc_info:
            exc_formatted = self.formatException(record.exc_info)
            payload["exception"] = sanitize_text_anti_pii(exc_formatted)

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Inicializa a configuração global de logs JSON estruturados com filtro Zero-PII."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove handlers preexistentes para evitar duplicação
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)
    stream_handler.addFilter(ZeroPIIFilter())
    stream_handler.setFormatter(StructuredJSONFormatter())

    root_logger.addHandler(stream_handler)

    # Ajusta loggers de bibliotecas terceiras para respeitar o formato e evitar ruído
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "celery"):
        ext_logger = logging.getLogger(logger_name)
        ext_logger.handlers.clear()
        ext_logger.propagate = True
