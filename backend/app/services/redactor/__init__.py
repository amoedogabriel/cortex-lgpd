"""Pacote do Motor de Tarjamento Definitivo Irreversível (`redactor`)."""

from app.services.redactor.pdf_redactor import (
    PhysicalRedactionResult,
    RedactionBoxSpec,
    apply_true_redactions_to_pdf,
)
from app.services.redactor.structural_sanitizer import (
    CompleteRedactionPipelineResult,
    StructuralSanitizationResult,
    execute_complete_redaction_pipeline,
    sanitize_pdf_structure,
)

__all__ = [
    "RedactionBoxSpec",
    "PhysicalRedactionResult",
    "apply_true_redactions_to_pdf",
    "StructuralSanitizationResult",
    "CompleteRedactionPipelineResult",
    "sanitize_pdf_structure",
    "execute_complete_redaction_pipeline",
]
