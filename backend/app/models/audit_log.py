"""Entidade ORM AuditLog para trilha de auditoria LGPD/LAI estritamente isenta de dados pessoais (Zero-PII)."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.processing_job import ProcessingJob
    from app.models.user import User


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """
    Registro imutável de auditoria institucional.

    DIRETRIZ RÍGIDA (SPEC.md Seção 3 e 4.3):
    Esta tabela armazena EXCLUSIVAMENTE contadores agregados por categoria da taxonomia,
    hashes criptográficos SHA-256 (original e tarjado), lista de índices de páginas afetadas
    e carimbo de tempo. É estritamente proibido persistir os textos literais extraídos.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("count_personal_common >= 0", name="non_negative_personal_common"),
        CheckConstraint("count_personal_sensitive >= 0", name="non_negative_personal_sensitive"),
        CheckConstraint("count_financial_banking >= 0", name="non_negative_financial_banking"),
        CheckConstraint("count_minor_or_dependent >= 0", name="non_negative_minor_or_dependent"),
        CheckConstraint("total_redactions_applied >= 0", name="non_negative_total_redactions"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
        doc="Referência ao ProcessingJob auditado.",
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        doc="Identificador do servidor/revisor que aprovou e executou o tarjamento.",
    )
    organizational_unit: Mapped[str | None] = mapped_column(
        String(120),
        index=True,
        nullable=True,
        doc="Unidade administrativa / Pró-Reitoria responsável pela operação.",
    )

    # Hashes SHA-256 de integridade documental
    original_document_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
        doc="Hash SHA-256 (64 caracteres hex) do documento PDF original antes do tarjamento.",
    )
    redacted_document_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
        doc="Hash SHA-256 (64 caracteres hex) do documento PDF após tarjamento físico e sanitização.",
    )

    # Contadores agregados por categoria da taxonomia (SPEC.md Seção 2)
    count_personal_common: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Total de ocorrências tarjadas da categoria PERSONAL_COMMON (Dado Pessoal Comum).",
    )
    count_personal_sensitive: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Total de ocorrências tarjadas da categoria PERSONAL_SENSITIVE (Art. 5º, II, LGPD).",
    )
    count_financial_banking: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Total de ocorrências tarjadas da categoria FINANCIAL_BANKING (Dado Financeiro/Bancário).",
    )
    count_minor_or_dependent: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Total de ocorrências tarjadas da categoria MINOR_OR_DEPENDENT (Art. 14, LGPD).",
    )
    total_redactions_applied: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        doc="Soma total de regiões tarjadas fisicamente no documento.",
    )

    # Contagem agregada por categoria em formato JSONB somente numérico (para consultas analíticas)
    category_counts: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        doc="Mapa estritamente numérico {'PERSONAL_COMMON': int, ...} sem valores textuais.",
    )

    # Páginas afetadas (1-indexed)
    affected_pages: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        default=list,
        nullable=False,
        doc="Lista ordenada de números de páginas (1-indexed) que sofreram tarjamento físico.",
    )
    total_pages: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Total de páginas do documento processado.",
    )

    # Indicadores de conformidade técnica e jurídica
    human_review_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        doc="Indica se houve validação humana explícita (Human-in-the-Loop) antes da geração final.",
    )
    anonymization_disclaimer_acknowledged: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        doc="Confirma ciência da distinção legal entre tarjamento e anonimização integral (Art. 5º, XI, LGPD).",
    )
    metadata_sanitized: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        doc="Confirma execução da limpeza de XMP/Info, camadas OCG e objetos órfãos via pikepdf.",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
        doc="Carimbo de tempo UTC imutável da emissão da trilha de auditoria.",
    )

    job: Mapped["ProcessingJob"] = relationship(
        "ProcessingJob",
        back_populates="audit_log",
    )
    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="audit_logs",
    )
