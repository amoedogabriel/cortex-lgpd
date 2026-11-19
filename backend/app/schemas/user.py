"""Schemas Pydantic v2 para a entidade User e perfis RBAC."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.security import UserRole


class UserBase(BaseModel):
    """Atributos comuns de usuário institucional."""

    username: str = Field(..., min_length=2, max_length=150)
    organizational_unit: str | None = Field(default=None, max_length=120)
    role: UserRole = Field(default=UserRole.OPERATOR)
    is_active: bool = Field(default=True)


class UserCreate(UserBase):
    """Payload para criação/sincronização de usuário via claim OIDC."""

    keycloak_sub: str = Field(..., min_length=1, max_length=128)


class UserRead(UserBase):
    """Representação pública de usuário retornada pela API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    keycloak_sub: str
    created_at: datetime
    updated_at: datetime
