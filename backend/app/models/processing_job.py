"""Entidade ORM ProcessingJob para controle assíncrono do ciclo de vida de tarjamento de PDF."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import JobStatus

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog
    from app.models.user import User


class ProcessingJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Registra o estado operacional de um job de processamento de PDF (Zero-PII no banco)."""

    __tablename__ = "processing_jobs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        doc="Usuário institucional que submeteu o documento.",
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status_enum", native_enum=True),
        default=JobStatus.RECEIVED,
        index=True,
        nullable=False,
        doc="Estado atual do pipeline (RECEIVED..COMPLETED/FAILED).",
    )
    original_file_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
        doc="Hash criptográfico SHA-256 hexadecimal do PDF original submetido.",
    )
    redacted_file_sha256: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
        nullable=True,
        doc="Hash criptográfico SHA-256 hexadecimal do PDF final fisicamente tarjado e sanitizado.",
    )
    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Tamanho em bytes do documento PDF original.",
    )
    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Número total de páginas identificadas no documento PDF.",
    )
    celery_task_id: Mapped[str | None] = mapped_column(
        String(128),
        index=True,
        nullable=True,
        doc="ID da task ativa no Celery/Redis para acompanhamento assíncrono.",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="Código técnico padronizado do erro em caso de falha (sem fragmentos de texto do PDF).",
    )
    error_summary: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Mensagem técnica sanitizada (Zero-PII) descrevendo eventual falha.",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        doc="Data/hora limite para expurgo definitivo dos arquivos transitórios no volume tmpfs.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Carimbo de tempo de conclusão do processamento e validação.",
    )

    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="jobs",
    )
    audit_log: Mapped["AuditLog | None"] = relationship(
        "AuditLog",
        back_populates="job",
        uselist=False,
        cascade="all, delete-orphan",
    )
