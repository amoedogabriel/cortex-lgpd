"""
Endpoints REST e Server-Sent Events (SSE) para o Pipeline Progressivo First-Page-First
(`backend/app/api/v1/endpoints/documents.py`).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.enums import DataCategory, DocumentProfile, JobStatus, RedactionActionMode
from app.schemas.processing_job import NormalizedBoundingBox, RedactionItem
from app.services.pdf_extractor.file_validator import validate_and_store_pdf
from app.worker.tasks import (
    JOB_REDIS_TTL_SECONDS,
    _get_sync_redis_client,
    _is_postgres_reachable,
    _is_redis_reachable,
    _persist_state_to_job_spool,
    analyze_document_task,
    append_verbose_step,
    apply_redaction_task,
    get_buffered_job_events,
    get_job_channel_name,
    load_job_transient_state,
    store_job_transient_state,
)

router = APIRouter(prefix="/documents", tags=["Documents & Progressive Redaction"])

NO_CACHE_DOWNLOAD_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


class RedactionRequestPayload(BaseModel):
    """Payload enviado pelo revisor humano (Human-in-the-Loop) para aplicar o tarjamento/mascaramento definitivo."""

    selected_ids: list[str] | None = Field(
        default=None,
        description="Lista de IDs das ocorrências automáticas aprovadas para proteção. Se omitido (None), protege todas as detectadas.",
    )
    items: list[RedactionItem] | None = Field(
        default=None,
        description="Lista explícita de decisões individuais (`id`, `action_mode`, `masked_preview`) enviada pelo Frontend.",
    )
    redaction_items: list[RedactionItem] | None = Field(
        default=None,
        description="Alias legado para `items`: lista detalhada de decisões individuais por ocorrência.",
    )
    global_mode: RedactionActionMode | None = Field(
        default=None,
        description="Modo global opcional (`BLACKOUT` ou `PARTIAL_MASK`) caso não haja lista por item.",
    )
    manual_boxes: list[NormalizedBoundingBox] = Field(
        default_factory=list,
        description="Caixas de tarjamento manual desenhadas pelo servidor na interface de revisão.",
    )
    anonymization_disclaimer_acknowledged: bool = Field(
        default=True,
        description="Confirmação de ciência da distinção técnica entre tarjamento e anonimização (Art. 5º, XI, LGPD).",
    )


async def _try_create_job_in_postgres(
    job_id: uuid.UUID,
    original_sha256: str,
    file_size_bytes: int,
    page_count: int,
    initial_status: JobStatus,
) -> None:
    """Registra o `ProcessingJob` no PostgreSQL caso o banco esteja ativo no ambiente."""
    if not _is_postgres_reachable():
        return
    try:
        from app.db.session import AsyncSessionLocal
        from app.models.processing_job import ProcessingJob

        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=settings.TEMP_FILE_TTL_SECONDS)
        async with AsyncSessionLocal() as session:
            job_row = ProcessingJob(
                id=job_id,
                status=initial_status,
                original_file_sha256=original_sha256,
                file_size_bytes=file_size_bytes,
                page_count=page_count,
                temp_dir_path=str(settings.TEMP_STORAGE_DIR / str(job_id)),
                expires_at=expires,
            )
            session.add(job_row)
            await session.commit()
    except Exception:
        pass


def _is_celery_worker_reachable() -> bool:
    """Verifica rapidamente se há broker Redis + worker Celery ativo para despacho via fila externa."""
    r_client = _get_sync_redis_client()
    if r_client is None:
        return False
    try:
        inspector = analyze_document_task.app.control.inspect(timeout=0.25)
        ping_res = inspector.ping()
        return bool(ping_res)
    except Exception:
        return False
    finally:
        r_client.close()


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Valida o PDF, processa a Página 1 com prioridade imediata e inicia o pipeline First-Page-First",
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Arquivo PDF institucional a ser analisado e tarjado"),
    defer_remaining_pages: bool = Query(
        default=True,
        description="Quando True, processa a Página 1 imediatamente (`PARTIAL_REVIEW_READY`) e continua as páginas 2..N em background.",
    ),
) -> dict[str, Any]:
    t_upload_start = time.perf_counter()
    raw_bytes = await file.read()
    job_uuid = uuid.uuid4()
    job_id_str = str(job_uuid)

    validated = validate_and_store_pdf(
        file_bytes=raw_bytes,
        job_id=job_uuid,
        base_temp_dir=settings.TEMP_STORAGE_DIR,
    )
    validation_ms = (time.perf_counter() - t_upload_start) * 1000.0

    initial_state: dict[str, Any] = {
        "job_id": job_id_str,
        "filename": file.filename or "documento.pdf",
        "original_file_sha256": validated.sha256_hash,
        "file_size_bytes": validated.file_size_bytes,
        "status": JobStatus.ANALYZING.value,
        "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
        "total_pages": validated.page_count,
        "processed_pages": 0,
        "pages": {},
        "occurrences": [],
        "verbose_logs": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    append_verbose_step(
        job_id_str,
        initial_state,
        step_number=1,
        stage="ETAPA_1_INGESTAO_SEGURA",
        stage_label="1. Ingestão Segura & Validação Estrutural",
        message=(
            f"Arquivo PDF recebido ({validated.file_size_bytes / 1024:.1f} KB, {validated.page_count} página(s)) • "
            f"Assinatura mágica %PDF- e árvore de objetos validadas via pikepdf (sem criptografia ou scripts /JS /Launch) • "
            f"SHA-256: {validated.sha256_hash[:16]}..."
        ),
        duration_ms=validation_ms,
        details={
            "file_size_bytes": validated.file_size_bytes,
            "total_pages": validated.page_count,
            "sha256_prefix": validated.sha256_hash[:16],
        },
    )

    store_job_transient_state(job_id_str, initial_state, ttl=JOB_REDIS_TTL_SECONDS)
    _persist_state_to_job_spool(job_id_str, initial_state)

    await _try_create_job_in_postgres(
        job_id=job_uuid,
        original_sha256=validated.sha256_hash,
        file_size_bytes=validated.file_size_bytes,
        page_count=validated.page_count,
        initial_status=JobStatus.ANALYZING,
    )

    doc_profile = DocumentProfile.RESTRICTED_PROCESS.value
    verbose_logs_out = initial_state.get("verbose_logs", [])

    if defer_remaining_pages and validated.page_count > 1:
        state_after_p1 = analyze_document_task(job_id_str, start_page=1, end_page=1)
        doc_profile = state_after_p1.get("document_profile", doc_profile)
        verbose_logs_out = state_after_p1.get("verbose_logs", verbose_logs_out)
        if _is_celery_worker_reachable():
            analyze_document_task.delay(job_id_str, start_page=2, end_page=validated.page_count)
        else:
            background_tasks.add_task(
                analyze_document_task,
                job_id_str,
                start_page=2,
                end_page=validated.page_count,
            )
        current_status = state_after_p1.get("status", JobStatus.PARTIAL_REVIEW_READY.value)
        processed_pages = state_after_p1.get("processed_pages", 1)
    else:
        if _is_celery_worker_reachable():
            analyze_document_task.delay(job_id_str, start_page=1, end_page=validated.page_count)
            current_status = JobStatus.ANALYZING.value
            processed_pages = 0
        else:
            final_state = analyze_document_task(job_id_str, start_page=1, end_page=validated.page_count)
            doc_profile = final_state.get("document_profile", doc_profile)
            verbose_logs_out = final_state.get("verbose_logs", verbose_logs_out)
            current_status = final_state.get("status", JobStatus.AWAITING_REVIEW.value)
            processed_pages = final_state.get("processed_pages", validated.page_count)

    return {
        "job_id": job_id_str,
        "status": current_status,
        "document_profile": doc_profile,
        "original_file_sha256": validated.sha256_hash,
        "file_size_bytes": validated.file_size_bytes,
        "total_pages": validated.page_count,
        "processed_pages": processed_pages,
        "verbose_logs": verbose_logs_out,
        "stream_url": f"/api/v1/documents/{job_id_str}/stream",
        "occurrences_url": f"/api/v1/documents/{job_id_str}/occurrences",
        "first_page_image_url": f"/api/v1/documents/{job_id_str}/pages/1/image",
    }


@router.get(
    "/{job_id}/stream",
    summary="Streaming Server-Sent Events (SSE) do progresso por página, logs verbose e conclusão do tarjamento",
)
async def stream_document_events(job_id: uuid.UUID) -> StreamingResponse:
    job_id_str = str(job_id)
    state = load_job_transient_state(job_id_str)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job não encontrado ou expirado.",
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        sent_count = 0
        buffered = get_buffered_job_events(job_id_str)

        if not buffered and state.get("pages"):
            for v_entry in state.get("verbose_logs", []):
                v_evt = {
                    "event": "verbose_step",
                    "job_id": job_id_str,
                    "timestamp": v_entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    "data": v_entry,
                }
                yield f"event: verbose_step\ndata: {json.dumps(v_evt, ensure_ascii=False)}\n\n"

            pages_map = state.get("pages", {})
            total_pages = state.get("total_pages", len(pages_map))
            doc_profile = state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value)
            for p_key in sorted(pages_map.keys(), key=lambda x: int(x)):
                p_info = pages_map[p_key]
                synthetic_evt = {
                    "event": "page_ready",
                    "job_id": job_id_str,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "page_number": p_info["page_number"],
                        "total_pages": total_pages,
                        "status": state.get("status"),
                        "document_profile": doc_profile,
                        "image_url": p_info["image_url"],
                        "occurrences_count": len(p_info.get("occurrences", [])),
                        "occurrences": p_info.get("occurrences", []),
                    },
                }
                yield f"event: {synthetic_evt['event']}\ndata: {json.dumps(synthetic_evt, ensure_ascii=False)}\n\n"

            if state.get("status") == JobStatus.AWAITING_REVIEW.value:
                complete_evt = {
                    "event": "analysis_complete",
                    "job_id": job_id_str,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "status": JobStatus.AWAITING_REVIEW.value,
                        "document_profile": doc_profile,
                        "total_pages": total_pages,
                        "total_occurrences": len(state.get("occurrences", [])),
                    },
                }
                yield f"event: analysis_complete\ndata: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                return

            if state.get("status") == JobStatus.COMPLETED.value:
                redacted_evt = {
                    "event": "redaction_complete",
                    "job_id": job_id_str,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "status": JobStatus.COMPLETED.value,
                        "download_pdf_url": state.get("download_pdf_url"),
                        "download_report_url": state.get("download_report_url"),
                    },
                }
                yield f"event: redaction_complete\ndata: {json.dumps(redacted_evt, ensure_ascii=False)}\n\n"
                return

        while sent_count < len(buffered):
            evt = buffered[sent_count]
            sent_count += 1
            evt_name = evt.get("event", "message")
            yield f"event: {evt_name}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt_name in ("analysis_complete", "redaction_complete", "error"):
                return

        if _is_redis_reachable():
            try:
                import redis.asyncio as aioredis

                async_redis = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)
                pubsub = async_redis.pubsub()
                await pubsub.subscribe(get_job_channel_name(job_id_str))
                try:
                    deadline = time.monotonic() + 120.0
                    while time.monotonic() < deadline:
                        current_buffered = get_buffered_job_events(job_id_str)
                        while sent_count < len(current_buffered):
                            evt = current_buffered[sent_count]
                            sent_count += 1
                            evt_name = evt.get("event", "message")
                            yield f"event: {evt_name}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                            if evt_name in ("analysis_complete", "redaction_complete", "error"):
                                return

                        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.15)
                        if msg and msg.get("data"):
                            try:
                                evt = json.loads(str(msg["data"]))
                                evt_name = evt.get("event", "message")
                                yield f"event: {evt_name}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                                if evt_name in ("analysis_complete", "redaction_complete", "error"):
                                    return
                            except Exception:
                                pass

                        latest_state = load_job_transient_state(job_id_str)
                        if latest_state and latest_state.get("status") in (
                            JobStatus.AWAITING_REVIEW.value,
                            JobStatus.COMPLETED.value,
                            JobStatus.FAILED.value,
                        ):
                            if latest_state.get("status") == JobStatus.AWAITING_REVIEW.value:
                                complete_evt = {
                                    "event": "analysis_complete",
                                    "job_id": job_id_str,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "data": {
                                        "status": JobStatus.AWAITING_REVIEW.value,
                                        "document_profile": latest_state.get(
                                            "document_profile", DocumentProfile.RESTRICTED_PROCESS.value
                                        ),
                                        "total_pages": latest_state.get("total_pages", 0),
                                        "total_occurrences": len(latest_state.get("occurrences", [])),
                                    },
                                }
                                yield f"event: analysis_complete\ndata: {json.dumps(complete_evt, ensure_ascii=False)}\n\n"
                            return
                    return
                finally:
                    await pubsub.unsubscribe(get_job_channel_name(job_id_str))
                    await pubsub.close()
                    await async_redis.aclose()
            except Exception:
                pass

        max_wait_cycles = 40
        for _ in range(max_wait_cycles):
            await asyncio.sleep(0.05)
            current_buffered = get_buffered_job_events(job_id_str)
            while sent_count < len(current_buffered):
                evt = current_buffered[sent_count]
                sent_count += 1
                evt_name = evt.get("event", "message")
                yield f"event: {evt_name}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                if evt_name in ("analysis_complete", "redaction_complete", "error"):
                    return

            latest_state = load_job_transient_state(job_id_str)
            if latest_state and latest_state.get("status") in (
                JobStatus.AWAITING_REVIEW.value,
                JobStatus.COMPLETED.value,
                JobStatus.FAILED.value,
            ):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{job_id}/pages/{page_num}/image",
    summary="Serve a imagem WebP otimizada (150 DPI) da página para Thin-Client",
)
async def get_page_webp_image(job_id: uuid.UUID, page_num: int) -> FileResponse:
    if page_num < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O número da página deve ser maior ou igual a 1.",
        )

    webp_path: Path = settings.TEMP_STORAGE_DIR / str(job_id) / "pages" / f"page_{page_num}.webp"
    if not webp_path.exists():
        original_pdf = settings.TEMP_STORAGE_DIR / str(job_id) / "original.pdf"
        if original_pdf.exists():
            import fitz
            from app.worker.tasks import process_single_page_into_state

            state = load_job_transient_state(str(job_id)) or {
                "job_id": str(job_id),
                "status": JobStatus.PARTIAL_REVIEW_READY.value,
                "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
                "pages": {},
                "occurrences": [],
                "verbose_logs": [],
            }
            doc = fitz.open(str(original_pdf))
            try:
                if 1 <= page_num <= len(doc):
                    process_single_page_into_state(
                        doc=doc,
                        page_num=page_num,
                        job_id=str(job_id),
                        state=state,
                        base_temp_dir=settings.TEMP_STORAGE_DIR,
                    )
            finally:
                doc.close()

    if not webp_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Imagem da página {page_num} não encontrada para o job {job_id}.",
        )

    return FileResponse(
        path=str(webp_path),
        media_type="image/webp",
        filename=f"page_{page_num}.webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get(
    "/{job_id}/occurrences",
    summary="Retorna o estado consolidado das páginas processadas, logs verbose e ocorrências de PII detectadas",
)
async def get_document_occurrences(job_id: uuid.UUID) -> dict[str, Any]:
    state = load_job_transient_state(str(job_id))
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job não encontrado ou expirado.",
        )

    return {
        "job_id": str(job_id),
        "status": state.get("status"),
        "document_profile": state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value),
        "original_file_sha256": state.get("original_file_sha256"),
        "redacted_file_sha256": state.get("redacted_file_sha256"),
        "total_pages": state.get("total_pages", 0),
        "processed_pages": state.get("processed_pages", 0),
        "pages": state.get("pages", {}),
        "occurrences": state.get("occurrences", []),
        "verbose_logs": state.get("verbose_logs", []),
        "audit_log": state.get("audit_log"),
    }


@router.post(
    "/{job_id}/redact",
    status_code=status.HTTP_200_OK,
    summary="Aplica o tarjamento/mascaramento físico definitivo, validação cirúrgica e gera o Relatório Zero-PII",
)
async def redact_document(
    job_id: uuid.UUID,
    payload: RedactionRequestPayload | None = None,
) -> dict[str, Any]:
    """
    Re-executável quantas vezes o revisor desejar (`BLACKOUT` <-> `PARTIAL_MASK` <-> `KEEP`):
    remove artefatos antigos, reinicia status para `REDACTING` sincronamente, atualiza `action_mode`
    a partir de `items` / `redaction_items` / `global_mode` e gera PDF tarjado/mascarado a partir
    do `original.pdf` intacto.
    """
    job_id_str = str(job_id)
    state = load_job_transient_state(job_id_str)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job não encontrado ou expirado.",
        )

    total_pages = int(state.get("total_pages", 0))
    processed_pages = int(state.get("processed_pages", 0))
    if processed_pages < total_pages:
        analyze_document_task(job_id_str, start_page=processed_pages + 1, end_page=total_pages)
        state = load_job_transient_state(job_id_str) or state

    req = payload or RedactionRequestPayload()
    effective_items = req.items if req.items is not None else req.redaction_items

    # Limpa artefatos anteriores e marca REDACTING sincronamente antes de re-gerar
    job_dir = settings.TEMP_STORAGE_DIR / job_id_str
    (job_dir / "redacted.pdf").unlink(missing_ok=True)
    (job_dir / "report.pdf").unlink(missing_ok=True)
    state["status"] = JobStatus.REDACTING.value
    state["redacted_file_sha256"] = None
    state["download_pdf_url"] = None
    state["download_report_url"] = None
    store_job_transient_state(job_id_str, state, ttl=JOB_REDIS_TTL_SECONDS)
    _persist_state_to_job_spool(job_id_str, state)

    manual_boxes_dicts = [
        {
            "page_number": b.page_number,
            "x0_norm": b.x0_norm,
            "y0_norm": b.y0_norm,
            "x1_norm": b.x1_norm,
            "y1_norm": b.y1_norm,
            "category": b.category.value if isinstance(b.category, DataCategory) else str(b.category),
        }
        for b in req.manual_boxes
    ]
    redaction_items_dicts = (
        [item.model_dump() for item in effective_items]
        if effective_items is not None
        else None
    )
    global_mode_str = req.global_mode.value if req.global_mode is not None else None

    updated_state = apply_redaction_task(
        job_id=job_id_str,
        selected_ids=req.selected_ids,
        manual_boxes=manual_boxes_dicts,
        redaction_items=redaction_items_dicts,
        global_mode=global_mode_str,
    )

    return {
        "job_id": job_id_str,
        "status": updated_state.get("status"),
        "document_profile": updated_state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value),
        "original_file_sha256": updated_state.get("original_file_sha256"),
        "redacted_file_sha256": updated_state.get("redacted_file_sha256"),
        "verbose_logs": updated_state.get("verbose_logs", []),
        "audit_log": updated_state.get("audit_log"),
        "download_pdf_url": updated_state.get("download_pdf_url"),
        "download_report_url": updated_state.get("download_report_url"),
    }


@router.get(
    "/{job_id}/download/pdf",
    summary="Download em streaming do documento PDF final tarjado e sanitizado",
)
async def download_redacted_pdf(job_id: uuid.UUID) -> FileResponse:
    """Faz o streaming do arquivo `redacted.pdf` sem cache para garantir sempre a versão mais recente."""
    redacted_path: Path = settings.TEMP_STORAGE_DIR / str(job_id) / "redacted.pdf"
    if not redacted_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento tarjado ainda não foi gerado ou já expirou.",
        )

    return FileResponse(
        path=str(redacted_path),
        media_type="application/pdf",
        filename=f"ufam_documento_tarjado_{str(job_id)[:8]}.pdf",
        headers=NO_CACHE_DOWNLOAD_HEADERS,
    )


@router.get(
    "/{job_id}/download/report",
    summary="Download em streaming do Relatório Institucional de Processamento Zero-PII em PDF",
)
async def download_processing_report(job_id: uuid.UUID) -> FileResponse:
    """Faz o streaming do arquivo `report.pdf` sem cache para garantir sempre a versão mais recente."""
    report_path: Path = settings.TEMP_STORAGE_DIR / str(job_id) / "report.pdf"
    if not report_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relatório de processamento ainda não foi gerado ou já expirou.",
        )

    return FileResponse(
        path=str(report_path),
        media_type="application/pdf",
        filename=f"ufam_relatorio_tarjamento_{str(job_id)[:8]}.pdf",
        headers=NO_CACHE_DOWNLOAD_HEADERS,
    )
