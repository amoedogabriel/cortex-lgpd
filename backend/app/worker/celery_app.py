"""Configuração da instância Celery conectada ao Redis com higienização automática de arquivos transitórios."""

import logging
import time
from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging, worker_process_init

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.ml_models import ml_registry

logger = logging.getLogger(__name__)

celery_app = Celery(
    "ufam_redaction_worker",
    broker=str(settings.CELERY_BROKER_URL),
    backend=str(settings.CELERY_RESULT_BACKEND),
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=settings.TEMP_FILE_TTL_SECONDS,
    task_default_queue="default",
    task_routes={
        "app.worker.celery_app.purge_expired_temp_files": {"queue": "default"},
        "app.worker.celery_app.worker_liveness_probe": {"queue": "default"},
        "app.worker.tasks.analyze_document_task": {"queue": "default"},
        "app.worker.tasks.apply_redaction_task": {"queue": "default"},
        "app.worker.tasks.cleanup_expired_jobs_task": {"queue": "default"},
    },
    beat_schedule={
        "purge-expired-transient-pdfs": {
            "task": "app.worker.celery_app.purge_expired_temp_files",
            "schedule": float(settings.CLEANUP_INTERVAL_SECONDS),
        },
        "cleanup-expired-jobs-and-redis-cache": {
            "task": "app.worker.tasks.cleanup_expired_jobs_task",
            "schedule": float(settings.CLEANUP_INTERVAL_SECONDS),
        },
    },
)


@celery_setup_logging.connect
def configure_celery_logging(**kwargs: Any) -> None:
    """Força o worker Celery a utilizar o logger JSON estruturado com filtro Zero-PII."""
    setup_logging()


@worker_process_init.connect
def preload_ml_models_on_worker_init(**kwargs: Any) -> None:
    """
    Carrega uma única vez na inicialização de cada processo Worker do Celery
    o motor OCR ONNX (`RapidOCR`) e o modelo `spaCy` (`pt_core_news_lg`),
    mantendo-os residentes em RAM para eliminar o cold start.
    """
    metrics = ml_registry.warmup(load_ocr=True, load_spacy=True)
    logger.info(
        "Worker Celery inicializado com modelos de ML pré-aquecidos em RAM",
        extra={"warmup_metrics": metrics},
    )


@celery_app.task(name="app.worker.celery_app.worker_liveness_probe")
def worker_liveness_probe() -> dict[str, str]:
    """Task de diagnóstico rápido da fila de processamento assíncrono."""
    return {"status": "alive", "service": settings.APP_NAME}


@celery_app.task(name="app.worker.celery_app.purge_expired_temp_files")
def purge_expired_temp_files() -> dict[str, int]:
    """
    Remove definitivamente arquivos PDF temporários do volume tmpfs cujo tempo de vida
    ultrapassou TEMP_FILE_TTL_SECONDS (Política Zero-Retention).
    """
    spool_dir: Path = settings.TEMP_STORAGE_DIR
    if not spool_dir.exists():
        return {"removed_files": 0, "scanned_files": 0}

    now = time.time()
    ttl = settings.TEMP_FILE_TTL_SECONDS
    removed_count = 0
    scanned_count = 0

    for item in spool_dir.rglob("*"):
        if not item.is_file():
            continue
        scanned_count += 1
        try:
            age_seconds = now - item.stat().st_mtime
            if age_seconds >= ttl:
                # Sobrescreve com zeros antes de desvincular o inode do tmpfs
                size = item.stat().st_size
                if 0 < size <= settings.MAX_UPLOAD_SIZE_BYTES:
                    with item.open("r+b") as f:
                        f.write(b"\x00" * min(size, 65536))
                        f.flush()
                item.unlink(missing_ok=True)
                removed_count += 1
        except OSError as exc:
            logger.warning(
                "Não foi possível remover arquivo transitório expirado",
                extra={"file_name": item.name, "error": str(exc)},
            )

    logger.info(
        "Ciclo de expurgo de arquivos transitórios concluído",
        extra={"scanned_files": scanned_count, "removed_files": removed_count},
    )
    return {"removed_files": removed_count, "scanned_files": scanned_count}
