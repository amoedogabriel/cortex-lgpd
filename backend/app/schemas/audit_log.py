"""Schemas Pydantic v2 para AuditLog com validação Zero-PII estrita."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import DataCategory

SHA256_REGEX = r"^[a-fA-F0-9]{64}$"

LEGAL_ANONYMIZATION_NOTICE = (
    "AVISO LEGAL (Art. 5º, XI, Lei 13.709/2018 - LGPD): O tarjamento definitivo remove "
    "irreversivelmente os fluxos de texto, vetores e pixels das áreas selecionadas, mas "
    "não garante, por si só, a anonimização integral do documento caso existam elementos "
    "contextuais indiretos que permitam a identificação do titular."
)


class AuditLogCreate(BaseModel):
    """
    Payload para persistência de auditoria de tarjamento.
    Proíbe estruturalmente qualquer texto literal de dado pessoal (Zero-PII).
    """

    job_id: uuid.UUID
    user_id: uuid.UUID | None = None
    organizational_unit: str | None = Field(default=None, max_length=120)
    original_document_sha256: str = Field(..., pattern=SHA256_REGEX)
    redacted_document_sha256: str = Field(..., pattern=SHA256_REGEX)
    count_personal_common: int = Field(default=0, ge=0)
    count_personal_sensitive: int = Field(default=0, ge=0)
    count_financial_banking: int = Field(default=0, ge=0)
    count_minor_or_dependent: int = Field(default=0, ge=0)
    affected_pages: list[int] = Field(default_factory=list)
    total_pages: int = Field(..., ge=1)
    human_review_confirmed: bool = Field(default=True)
    anonymization_disclaimer_acknowledged: bool = Field(default=True)
    metadata_sanitized: bool = Field(default=True)

    @field_validator("original_document_sha256", "redacted_document_sha256")
    @classmethod
    def normalize_hashes(cls, v: str) -> str:
        return v.lower()

    @field_validator("affected_pages")
    @classmethod
    def validate_and_sort_pages(cls, pages: list[int]) -> list[int]:
        for p in pages:
            if p < 1:
                raise ValueError("Números de página em affected_pages devem ser >= 1.")
        return sorted(set(pages))

    @model_validator(mode="after")
    def validate_page_bounds(self) -> "AuditLogCreate":
        for p in self.affected_pages:
            if p > self.total_pages:
                raise ValueError(
                    f"A página afetada {p} excede o número total de páginas do documento ({self.total_pages})."
                )
        return self

    @property
    def total_redactions_applied(self) -> int:
        return (
            self.count_personal_common
            + self.count_personal_sensitive
            + self.count_financial_banking
            + self.count_minor_or_dependent
        )

    @property
    def computed_category_counts(self) -> dict[str, int]:
        return {
            DataCategory.PERSONAL_COMMON.value: self.count_personal_common,
            DataCategory.PERSONAL_SENSITIVE.value: self.count_personal_sensitive,
            DataCategory.FINANCIAL_BANKING.value: self.count_financial_banking,
            DataCategory.MINOR_OR_DEPENDENT.value: self.count_minor_or_dependent,
        }


class AuditLogRead(BaseModel):
    """Representação pública da trilha de auditoria institucional (Zero-PII)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    user_id: uuid.UUID | None
    organizational_unit: str | None
    original_document_sha256: str
    redacted_document_sha256: str
    count_personal_common: int
    count_personal_sensitive: int
    count_financial_banking: int
    count_minor_or_dependent: int
    total_redactions_applied: int
    category_counts: dict[str, int]
    affected_pages: list[int]
    total_pages: int
    human_review_confirmed: bool
    anonymization_disclaimer_acknowledged: bool
    metadata_sanitized: bool
    legal_notice: str = Field(default=LEGAL_ANONYMIZATION_NOTICE)
    created_at: datetime
