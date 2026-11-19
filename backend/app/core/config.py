"""Configurações centralizadas da aplicação via Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações globais do Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Identificação da aplicação
    APP_NAME: str = "Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = Field(default="development", pattern="^(development|staging|production|test)$")
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Banco de Dados (PostgreSQL 16 + asyncpg)
    POSTGRES_USER: str = "ufam_redactor"
    POSTGRES_PASSWORD: str = "ufam_secret_password_change_me"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ufam_redaction_db"
    DATABASE_URL: str | None = None
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # Redis 7 & Celery
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str | None = None
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    # Limites de Upload e Armazenamento Transitório (Zero-Retention Policy)
    TEMP_STORAGE_DIR: Path = Path("/tmp/ufam_redaction_spool")
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB
    MAX_PDF_PAGES: int = 500
    TEMP_FILE_TTL_SECONDS: int = 3600  # 1 hora
    CLEANUP_INTERVAL_SECONDS: int = 300  # 5 minutos

    # Segurança e Autenticação OIDC / Keycloak
    OIDC_ENABLED: bool = True
    OIDC_ISSUER_URL: str = "https://auth.ufam.edu.br/realms/ufam"
    OIDC_CLIENT_ID: str = "ufam-redaction-backend"
    OIDC_AUDIENCE: str = "ufam-redaction-backend"
    OIDC_JWKS_URL: str = "https://auth.ufam.edu.br/realms/ufam/protocol/openid-connect/certs"
    OIDC_ALGORITHMS: List[str] = ["RS256"]
    OIDC_JWKS_CACHE_TTL_SECONDS: int = 3600
    JWT_DEV_SECRET_KEY: str = "dev_only_local_hs256_secret_change_in_prod_8f9a2b3c4d5e6f7a"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_url(cls, v: str | None, info) -> str:
        if isinstance(v, str) and v.strip():
            return v
        data = info.data
        user = data.get("POSTGRES_USER", "ufam_redactor")
        password = data.get("POSTGRES_PASSWORD", "ufam_secret_password_change_me")
        host = data.get("POSTGRES_HOST", "localhost")
        port = data.get("POSTGRES_PORT", 5432)
        db = data.get("POSTGRES_DB", "ufam_redaction_db")
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def assemble_redis_url(cls, v: str | None, info) -> str:
        if isinstance(v, str) and v.strip():
            return v
        data = info.data
        host = data.get("REDIS_HOST", "localhost")
        port = data.get("REDIS_PORT", 6379)
        db = data.get("REDIS_DB", 0)
        password = data.get("REDIS_PASSWORD", "")
        auth_part = f":{password}@" if password else ""
        return f"redis://{auth_part}{host}:{port}/{db}"

    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def assemble_celery_broker(cls, v: str | None, info) -> str:
        if isinstance(v, str) and v.strip():
            return v
        return info.data.get("REDIS_URL") or "redis://localhost:6379/0"

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def assemble_celery_backend(cls, v: str | None, info) -> str:
        if isinstance(v, str) and v.strip():
            return v
        data = info.data
        host = data.get("REDIS_HOST", "localhost")
        port = data.get("REDIS_PORT", 6379)
        password = data.get("REDIS_PASSWORD", "")
        auth_part = f":{password}@" if password else ""
        return f"redis://{auth_part}{host}:{port}/1"


@lru_cache
def get_settings() -> Settings:
    """Retorna instância singleton em cache das configurações da aplicação."""
    return Settings()


settings = get_settings()
