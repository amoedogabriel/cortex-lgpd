"""Exportação centralizada dos schemas Pydantic v2."""

from app.schemas.audit_log import LEGAL_ANONYMIZATION_NOTICE, AuditLogCreate, AuditLogRead
from app.schemas.health import ComponentHealth, HealthCheckResponse
from app.schemas.processing_job import (
    NormalizedBoundingBox,
    ProcessingJobCreate,
    ProcessingJobRead,
    ProcessingJobStatusUpdate,
)
from app.schemas.user import UserBase, UserCreate, UserRead

__all__ = [
    "UserBase",
    "UserCreate",
    "UserRead",
    "NormalizedBoundingBox",
    "ProcessingJobCreate",
    "ProcessingJobStatusUpdate",
    "ProcessingJobRead",
    "AuditLogCreate",
    "AuditLogRead",
    "LEGAL_ANONYMIZATION_NOTICE",
    "ComponentHealth",
    "HealthCheckResponse",
]
