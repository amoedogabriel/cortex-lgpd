"""Schemas Pydantic v2 para ProcessingJob e coordenadas normalizadas [0.0, 1.0]."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ConfidenceLevel, DataCategory, JobStatus, RedactionActionMode

SHA256_REGEX = r"^[a-fA-F0-9]{64}$"


class RedactionItem(BaseModel):
    """Item de decisão de redação por ocorrência (BLACKOUT, PARTIAL_MASK ou KEEP)."""

    id: str = Field(..., description="ID único da ocorrência detectada (ex.: occ_p1_1_...).")
    action_mode: RedactionActionMode = Field(
        default=RedactionActionMode.BLACKOUT,
        description="Modo escolhido pelo revisor ou recomendado pelo sistema: BLACKOUT, PARTIAL_MASK ou KEEP.",
    )
    recommended_mode: RedactionActionMode | None = Field(
        default=None,
        description="Sugestão automática do sistema (BLACKOUT ou PARTIAL_MASK).",
    )
    masked_preview: str | None = Field(
        default=None,
        description="Valor mascarado caso action_mode seja PARTIAL_MASK (ex.: ***.729.160-**).",
    )


class NormalizedBoundingBox(BaseModel):
    """
    Coordenadas relativas normalizadas no intervalo [0.0, 1.0] conforme SPEC.md (Seção 4.4).
    Permite conversão exata entre PyMuPDF (72 DPI points) e Canvas CSS no Frontend.
    """

    page_number: int = Field(..., ge=1, description="Índice da página (1-indexed).")
    x0_norm: float = Field(..., ge=0.0, le=1.0, description="Coordenada X esquerda normalizada.")
    y0_norm: float = Field(..., ge=0.0, le=1.0, description="Coordenada Y superior normalizada.")
    x1_norm: float = Field(..., ge=0.0, le=1.0, description="Coordenada X direita normalizada.")
    y1_norm: float = Field(..., ge=0.0, le=1.0, description="Coordenada Y inferior normalizada.")
    category: DataCategory = Field(..., description="Categoria estrita da taxonomia LGPD/UFAM.")
    confidence_level: ConfidenceLevel = Field(default=ConfidenceLevel.HIGH)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    action_mode: RedactionActionMode = Field(default=RedactionActionMode.BLACKOUT)
    recommended_mode: RedactionActionMode = Field(default=RedactionActionMode.BLACKOUT)
    masked_preview: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_box_geometry(self) -> "NormalizedBoundingBox":
        if self.x1_norm <= self.x0_norm:
            raise ValueError("x1_norm deve ser estritamente maior que x0_norm.")
        if self.y1_norm <= self.y0_norm:
            raise ValueError("y1_norm deve ser estritamente maior que y0_norm.")
        return self


class ProcessingJobCreate(BaseModel):
    """Schema de criação inicial de um job após validação do upload."""

    user_id: uuid.UUID | None = None
    original_file_sha256: str = Field(..., pattern=SHA256_REGEX)
    file_size_bytes: int = Field(..., gt=0)
    page_count: int | None = Field(default=None, ge=1)
    expires_at: datetime

    @field_validator("original_file_sha256")
    @classmethod
    def normalize_sha256(cls, v: str) -> str:
        return v.lower()


class ProcessingJobStatusUpdate(BaseModel):
    """Schema para atualização controlada de status de um ProcessingJob."""

    status: JobStatus
    redacted_file_sha256: str | None = Field(default=None, pattern=SHA256_REGEX)
    page_count: int | None = Field(default=None, ge=1)
    celery_task_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=64)
    error_summary: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None


class ProcessingJobRead(BaseModel):
    """Representação de leitura de um ProcessingJob retornada pela API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    status: JobStatus
    original_file_sha256: str
    redacted_file_sha256: str | None
    file_size_bytes: int
    page_count: int | None
    celery_task_id: str | None
    error_code: str | None
    error_summary: str | None
    expires_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
