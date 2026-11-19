"""Entidade ORM User vinculada à identidade OIDC institucional (Keycloak UFAM)."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import UserRole
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog
    from app.models.processing_job import ProcessingJob


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Representa um operador/revisor institucional da UFAM sem retenção de PII desnecessária."""

    __tablename__ = "users"

    keycloak_sub: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
        doc="Subject ID imutável fornecido pelo Keycloak OIDC.",
    )
    username: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        index=True,
        nullable=False,
        doc="Identificador de login institucional (preferred_username).",
    )
    organizational_unit: Mapped[str | None] = mapped_column(
        String(120),
        index=True,
        nullable=True,
        doc="Sigla/nome da Pró-Reitoria ou unidade administrativa da UFAM (ex.: PROGESP, PROEG).",
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum", native_enum=True),
        default=UserRole.OPERATOR,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
    )
