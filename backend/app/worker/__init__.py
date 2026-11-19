"""Módulo de processamento assíncrono Celery + Redis."""

from app.worker.celery_app import celery_app, purge_expired_temp_files, worker_liveness_probe
from app.worker.tasks import (
    analyze_document_task,
    apply_redaction_task,
    cleanup_expired_jobs_task,
)

__all__ = [
    "celery_app",
    "worker_liveness_probe",
    "purge_expired_temp_files",
    "analyze_document_task",
    "apply_redaction_task",
    "cleanup_expired_jobs_task",
]
