"""
Pipeline Progressivo First-Page-First via Celery + Redis Pub/Sub (`backend/app/worker/tasks.py`).

Requisitos implementados:
1. `analyze_document_task(job_id)`:
   - Abre o PDF do job e processa **a Página 1 com prioridade imediata** (geração do `page_1.webp` + extração/OCR + classificação de `document_profile` + detecção de PII com `recommended_mode`, `action_mode` e `masked_preview`);
   - Assim que a Página 1 termina (em ~1,0 a 1,5s), salva suas ocorrências no cache isolado do Redis (com TTL de 2 horas = 7200s), atualiza o status do job para `PARTIAL_REVIEW_READY` e publica imediatamente no canal Redis Pub/Sub do job o evento `page_ready` referente à Página 1;
   - Continua processando as páginas 2 até N herdando o `document_profile` da Página 1 e publicando eventos `page_ready`, `progress` e `verbose_step` individuais a cada página concluída até finalizar o documento (`AWAITING_REVIEW`), executar propagação cruzada de nomes completos confirmados em todas as páginas (`propagate_person_names_across_pages`) e emitir o evento `analysis_complete`.
2. `apply_redaction_task(job_id, selected_ids, manual_boxes, redaction_items, global_mode)`:
   - Suporta re-geração dinâmica idempotente (`BLACKOUT` <-> `PARTIAL_MASK` <-> `KEEP`) quantas vezes o usuário desejar, sempre lendo o `original.pdf` intacto, excluindo os artefatos antigos e sobrescrevendo `action_mode` no estado;
   - Emite telemetria verbose (`verbose_step`) em cada sub-etapa;
   - Executa o tarjamento/mascaramento físico definitivo, roda a validação cirúrgica por recorte, gera o Relatório de Processamento em PDF (`report_generator.py`), grava/atualiza os contadores em `AuditLog` (sem salvar nenhum texto literal de dado pessoal) e publica o evento `redaction_complete` no Redis Pub/Sub.
3. `cleanup_expired_jobs_task()`:
   - Remove diretórios temporários e chaves Redis expiradas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import socket
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import fitz  # PyMuPDF
import redis

from app.core.config import settings
from app.models.enums import (
    DataCategory,
    DocumentProfile,
    JobStatus,
    RedactionActionMode,
)
from app.services.pdf_extractor.extractor import PageExtractionResult, extract_single_page
from app.services.pii_detector.entity_mapper import (
    DetectedPIIEntity,
    build_person_canonical_map,
    classify_document_profile,
    compute_entity_group_id,
    detect_pii_in_single_page,
    propagate_person_names_across_pages,
)
from app.services.redactor.pdf_redactor import RedactionBoxSpec
from app.services.redactor.structural_sanitizer import execute_complete_redaction_pipeline
from app.services.report_generator import (
    CategoryQuantitativeRow,
    ZeroPIIReportData,
    generate_zero_pii_processing_report,
)
from app.services.security_validator.post_redaction_validator import validate_redacted_pdf_surgically
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

JOB_REDIS_TTL_SECONDS: int = 7200  # 2 horas de TTL no cache isolado de revisão

_IN_MEMORY_KV_STORE: dict[str, tuple[float, str]] = {}
_IN_MEMORY_EVENT_LOG: dict[str, list[dict[str, Any]]] = {}

_REDIS_PROBE_CACHE: tuple[float, bool] = (0.0, False)
_POSTGRES_PROBE_CACHE: tuple[float, bool] = (0.0, False)
_PROBE_TTL_SECONDS: float = 30.0


def _is_tcp_service_open(host: str, port: int, timeout: float = 0.15) -> bool:
    import threading

    target_host = "127.0.0.1" if host.lower() == "localhost" else host
    result = [False]

    def _probe() -> None:
        try:
            with socket.create_connection((target_host, port), timeout=timeout):
                result[0] = True
        except OSError:
            result[0] = False

    th = threading.Thread(target=_probe, daemon=True)
    th.start()
    th.join(timeout=timeout)
    return result[0]


def _is_redis_reachable() -> bool:
    global _REDIS_PROBE_CACHE
    now = time.monotonic()
    checked_at, is_up = _REDIS_PROBE_CACHE
    if checked_at > 0.0 and (now - checked_at) < _PROBE_TTL_SECONDS:
        return is_up

    parsed = urlparse(str(settings.REDIS_URL))
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    up = _is_tcp_service_open(host, port, timeout=0.15)
    _REDIS_PROBE_CACHE = (now, up)
    return up


def _is_postgres_reachable() -> bool:
    global _POSTGRES_PROBE_CACHE
    now = time.monotonic()
    checked_at, is_up = _POSTGRES_PROBE_CACHE
    if checked_at > 0.0 and (now - checked_at) < _PROBE_TTL_SECONDS:
        return is_up

    host = getattr(settings, "POSTGRES_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(getattr(settings, "POSTGRES_PORT", 5432) or 5432)
    up = _is_tcp_service_open(host, port, timeout=0.15)
    _POSTGRES_PROBE_CACHE = (now, up)
    return up


def get_job_channel_name(job_id: str | uuid.UUID) -> str:
    return f"ufam:job:{job_id}:events"


def get_job_state_key(job_id: str | uuid.UUID) -> str:
    return f"ufam:job:{job_id}:state"


def _get_sync_redis_client() -> redis.Redis | None:
    """Retorna um cliente Redis síncrono caso o servidor Redis esteja acessível, ou None para fallback em memória."""
    if not _is_redis_reachable():
        return None
    try:
        client = redis.Redis.from_url(
            str(settings.REDIS_URL),
            decode_responses=True,
            socket_connect_timeout=0.15,
            socket_timeout=0.3,
        )
        client.ping()
        return client
    except Exception:
        return None


def store_job_transient_state(job_id: str | uuid.UUID, state: dict[str, Any], ttl: int = JOB_REDIS_TTL_SECONDS) -> None:
    """Salva o estado transitório do job no Redis (com TTL de 2h) e espelha em memória local para resiliência."""
    jid = str(job_id)
    key = get_job_state_key(jid)
    serialized = json.dumps(state, ensure_ascii=False, default=str)
    _IN_MEMORY_KV_STORE[key] = (time.time() + ttl, serialized)

    r_client = _get_sync_redis_client()
    if r_client is not None:
        try:
            r_client.setex(key, ttl, serialized)
        finally:
            r_client.close()


def load_job_transient_state(job_id: str | uuid.UUID) -> dict[str, Any] | None:
    """Recupera o estado transitório do job do Redis ou do fallback em memória."""
    jid = str(job_id)
    key = get_job_state_key(jid)

    r_client = _get_sync_redis_client()
    if r_client is not None:
        try:
            raw = r_client.get(key)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        finally:
            r_client.close()

    entry = _IN_MEMORY_KV_STORE.get(key)
    if entry is not None:
        expires_at, raw_str = entry
        if time.time() < expires_at:
            return json.loads(raw_str)
        _IN_MEMORY_KV_STORE.pop(key, None)

    state_file = settings.TEMP_STORAGE_DIR / jid / "transient_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def publish_job_event(job_id: str | uuid.UUID, event_type: str, data: dict[str, Any]) -> None:
    """Publica um evento em tempo real no canal Redis Pub/Sub do job e registra no buffer de eventos."""
    jid = str(job_id)
    envelope = {
        "event": event_type,
        "job_id": jid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    _IN_MEMORY_EVENT_LOG.setdefault(jid, []).append(envelope)

    channel = get_job_channel_name(jid)
    r_client = _get_sync_redis_client()
    if r_client is not None:
        try:
            r_client.publish(channel, json.dumps(envelope, ensure_ascii=False, default=str))
        except Exception:
            pass
        finally:
            r_client.close()


def append_verbose_step(
    job_id: str | uuid.UUID,
    state: dict[str, Any],
    *,
    step_number: int,
    stage: str,
    stage_label: str,
    message: str,
    duration_ms: float | None = None,
    details: dict[str, Any] | None = None,
    level: str = "INFO",
) -> dict[str, Any]:
    """Registra uma entrada verbose estruturada (100% Zero-PII) referente a uma etapa do processamento."""
    jid = str(job_id)
    logs_list: list[dict[str, Any]] = state.setdefault("verbose_logs", [])
    seq_index = len(logs_list) + 1
    now_iso = datetime.now(timezone.utc).isoformat()

    entry: dict[str, Any] = {
        "id": f"vlog_{seq_index}_{int(time.time() * 1000) % 100000}",
        "sequence": seq_index,
        "step_number": step_number,
        "stage": stage,
        "stage_label": stage_label,
        "level": level,
        "message": message,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "details": details or {},
        "timestamp": now_iso,
    }
    logs_list.append(entry)
    if len(logs_list) > 120:
        del logs_list[:-120]

    logger.info(
        f"[VERBOSE][Etapa {step_number} - {stage_label}] {message}",
        extra={
            "context": {
                "job_id": jid,
                "step_number": step_number,
                "stage": stage,
                "duration_ms": entry["duration_ms"],
                **(details or {}),
            }
        },
    )

    publish_job_event(jid, "verbose_step", entry)
    return entry


def get_buffered_job_events(job_id: str | uuid.UUID) -> list[dict[str, Any]]:
    """Retorna os eventos já emitidos pelo job."""
    return list(_IN_MEMORY_EVENT_LOG.get(str(job_id), []))


def _persist_state_to_job_spool(job_id: str, state: dict[str, Any]) -> None:
    """Persiste uma cópia do estado transitório no diretório isolado do job em tmpfs."""
    job_dir = settings.TEMP_STORAGE_DIR / job_id
    if job_dir.exists():
        state_file = job_dir / "transient_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")


async def _try_sync_job_and_audit_in_postgres(
    job_id: str,
    status_val: JobStatus,
    *,
    page_count: int | None = None,
    redacted_sha256: str | None = None,
    audit_payload: dict[str, Any] | None = None,
) -> None:
    """Sincroniza `ProcessingJob` e grava ou atualiza `AuditLog` (Zero-PII) no PostgreSQL."""
    if not _is_postgres_reachable():
        return

    local_engine = None
    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.models.audit_log import AuditLog
        from app.models.processing_job import ProcessingJob

        local_engine = create_async_engine(str(settings.DATABASE_URL), poolclass=NullPool)
        local_session_factory = async_sessionmaker(
            bind=local_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        job_uuid = uuid.UUID(str(job_id)) if not isinstance(job_id, uuid.UUID) else job_id
        async with local_session_factory() as session:
            stmt = select(ProcessingJob).where(ProcessingJob.id == job_uuid)
            res = await session.execute(stmt)
            job_row = res.scalar_one_or_none()
            if job_row is not None:
                job_row.status = status_val
                if page_count is not None:
                    job_row.page_count = page_count
                if redacted_sha256 is not None:
                    job_row.redacted_file_sha256 = redacted_sha256
                if status_val == JobStatus.COMPLETED:
                    job_row.completed_at = datetime.now(timezone.utc)

            if audit_payload is not None:
                existing_audit = await session.execute(
                    select(AuditLog).where(AuditLog.job_id == job_uuid)
                )
                audit_row = existing_audit.scalar_one_or_none()
                if audit_row is None:
                    audit_entry = AuditLog(
                        job_id=job_uuid,
                        original_document_sha256=audit_payload["original_document_sha256"],
                        redacted_document_sha256=audit_payload["redacted_document_sha256"],
                        count_personal_common=audit_payload["count_personal_common"],
                        count_personal_sensitive=audit_payload["count_personal_sensitive"],
                        count_financial_banking=audit_payload["count_financial_banking"],
                        count_minor_or_dependent=audit_payload["count_minor_or_dependent"],
                        total_redactions_applied=audit_payload["total_redactions_applied"],
                        category_counts=audit_payload["category_counts"],
                        affected_pages=audit_payload["affected_pages"],
                        total_pages=audit_payload["total_pages"],
                        human_review_confirmed=True,
                        anonymization_disclaimer_acknowledged=True,
                        metadata_sanitized=True,
                    )
                    session.add(audit_entry)
                else:
                    audit_row.redacted_document_sha256 = audit_payload["redacted_document_sha256"]
                    audit_row.count_personal_common = audit_payload["count_personal_common"]
                    audit_row.count_personal_sensitive = audit_payload["count_personal_sensitive"]
                    audit_row.count_financial_banking = audit_payload["count_financial_banking"]
                    audit_row.count_minor_or_dependent = audit_payload["count_minor_or_dependent"]
                    audit_row.total_redactions_applied = audit_payload["total_redactions_applied"]
                    audit_row.category_counts = audit_payload["category_counts"]
                    audit_row.affected_pages = audit_payload["affected_pages"]
                    audit_row.total_pages = audit_payload["total_pages"]

            await session.commit()
    except Exception:
        pass
    finally:
        if local_engine is not None:
            try:
                await local_engine.dispose()
            except Exception:
                pass


def _consume_background_task_result(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _sync_job_in_postgres_if_reachable(
    job_id: str,
    status_val: JobStatus,
    *,
    page_count: int | None = None,
    redacted_sha256: str | None = None,
    audit_payload: dict[str, Any] | None = None,
) -> None:
    if not _is_postgres_reachable():
        return
    coro = _try_sync_job_and_audit_in_postgres(
        job_id,
        status_val,
        page_count=page_count,
        redacted_sha256=redacted_sha256,
        audit_payload=audit_payload,
    )
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        task.add_done_callback(_consume_background_task_result)
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception:
            pass


def _normalize_category_key(raw_category: Any) -> str:
    """Garante que qualquer categoria seja normalizada estritamente nas 4 chaves de `DataCategory`."""
    if isinstance(raw_category, DataCategory):
        return raw_category.value
    val = str(raw_category or "").strip().upper()
    valid_values = {c.value for c in DataCategory}
    if val in valid_values:
        return val
    return DataCategory.PERSONAL_COMMON.value


def _serialize_entity_for_review(
    ent: DetectedPIIEntity,
    occurrence_index: int,
) -> dict[str, Any]:
    """Serializa uma ocorrência detectada para revisão Human-in-the-Loop."""
    occ_id = f"occ_p{ent.page_number}_{occurrence_index}_{ent.group_id[:8]}"
    return {
        "id": occ_id,
        "page_number": ent.page_number,
        "entity_type": ent.entity_type,
        "category": _normalize_category_key(ent.category),
        "x0_norm": ent.x0_norm,
        "y0_norm": ent.y0_norm,
        "x1_norm": ent.x1_norm,
        "y1_norm": ent.y1_norm,
        "confidence_score": ent.confidence_score,
        "confidence_level": ent.confidence_level.value,
        "group_id": ent.group_id,
        "detector_source": ent.detector_source,
        "selected": ent.action_mode != RedactionActionMode.KEEP,
        "action_mode": ent.action_mode.value,
        "recommended_mode": ent.recommended_mode.value,
        "masked_preview": ent.masked_preview,
        "is_continuation": ent.is_continuation,
        "is_ocr": ent.is_ocr,
        "ocr_safe_left_norm": ent.safe_x0_norm,
        "ocr_safe_right_norm": ent.safe_x1_norm,
        "preview_text": ent.matched_text,
    }


def _fold_person_key(name: str) -> str:
    normalized = unicodedata.normalize("NFD", str(name or ""))
    no_accents = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", no_accents).strip().lower()


def _boxes_overlap(
    a_x0: float,
    a_y0: float,
    a_x1: float,
    a_y1: float,
    b_x0: float,
    b_y0: float,
    b_x1: float,
    b_y1: float,
) -> bool:
    return min(a_x1, b_x1) > max(a_x0, b_x0) and min(a_y1, b_y1) > max(a_y0, b_y0)


def _propagate_person_names_across_job_state(
    doc: fitz.Document,
    job_id: str,
    state: dict[str, Any],
    cached_page_exts: dict[int, PageExtractionResult] | None = None,
) -> list[int]:
    """
    Executa segunda passada de propagação cruzada de nomes próprios completos entre todas as
    páginas do job (`First-Page-First` progressivo ou lote completo), sincronizando `group_id`
    e atualizando páginas anteriores que continham o nome ausente ou fragmentado.
    """
    total_pages = len(doc)
    if total_pages <= 1:
        return []

    person_types = {"PERSON_NAME", "DEPENDENT_DATA", "FILIACAO"}
    grouped_occs: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for occ in state.get("occurrences", []):
        ent_type = str(occ.get("entity_type", ""))
        if ent_type not in person_types:
            continue
        grp_id = str(occ.get("group_id", ""))
        if not grp_id:
            continue
        p_num = int(occ.get("page_number", 1))
        grouped_occs.setdefault((p_num, grp_id), []).append(occ)

    group_records: list[tuple[int, str, list[dict[str, Any]], str]] = []
    for (p_num_grp, gid), items in grouped_occs.items():
        if len(items) == 1:
            raw_full = str(items[0].get("preview_text", "") or "").strip()
        else:
            raw_full = " ".join(str(i.get("preview_text", "") or "").strip() for i in items).strip()
        full_name = re.sub(r"\s+", " ", raw_full).strip()
        if len(full_name.split()) >= 2:
            group_records.append((p_num_grp, gid, items, full_name))

    canonical_map = build_person_canonical_map([rec[3] for rec in group_records])
    pages_dict = state.setdefault("pages", {})
    modified_pages_set: set[int] = set()

    if canonical_map:
        for p_num_grp, _gid, items, full_name in group_records:
            norm_ascii = _fold_person_key(full_name).upper()
            canon_full = canonical_map.get(norm_ascii)
            if not canon_full:
                continue
            for item in items:
                target_gid = compute_entity_group_id(
                    str(item.get("entity_type", "PERSON_NAME")),
                    canon_full,
                    session_salt=str(job_id),
                )
                if item.get("group_id") != target_gid:
                    item["group_id"] = target_gid
                    modified_pages_set.add(p_num_grp)

    confirmed_by_key: dict[str, tuple[str, str, DataCategory, float]] = {}
    for _p, _gid, items, full_name in group_records:
        norm_ascii = _fold_person_key(full_name).upper()
        if norm_ascii in canonical_map:
            continue
        key = _fold_person_key(full_name)
        first_item = items[0]
        ent_type = str(first_item.get("entity_type", "PERSON_NAME"))
        try:
            cat = DataCategory(_normalize_category_key(first_item.get("category")))
        except ValueError:
            cat = DataCategory.PERSONAL_COMMON
        conf = max(float(i.get("confidence_score", 0.90) or 0.90) for i in items)

        existing = confirmed_by_key.get(key)
        if existing is None:
            confirmed_by_key[key] = (full_name, ent_type, cat, conf)
        else:
            best_cat = cat if cat == DataCategory.MINOR_OR_DEPENDENT else existing[2]
            best_type = ent_type if ent_type in {"DEPENDENT_DATA", "FILIACAO"} else existing[1]
            confirmed_by_key[key] = (existing[0], best_type, best_cat, max(existing[3], conf))

    if not confirmed_by_key:
        if modified_pages_set:
            all_occs_upd: list[dict[str, Any]] = []
            for p_key in sorted(pages_dict.keys(), key=lambda x: int(x)):
                all_occs_upd.extend(pages_dict[p_key]["occurrences"])
            state["occurrences"] = all_occs_upd
        return sorted(modified_pages_set)

    all_page_results: list[PageExtractionResult] = []
    cache = cached_page_exts or {}
    for p_num in range(1, total_pages + 1):
        if p_num in cache:
            all_page_results.append(cache[p_num])
        else:
            all_page_results.append(
                extract_single_page(
                    doc[p_num - 1],
                    job_id=job_id,
                    page_number=p_num,
                    render_webp=False,
                    base_temp_dir=settings.TEMP_STORAGE_DIR,
                )
            )

    raw_prof = state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value)
    try:
        detected_profile = DocumentProfile(raw_prof)
    except ValueError:
        detected_profile = DocumentProfile.RESTRICTED_PROCESS

    propagated = propagate_person_names_across_pages(
        all_page_results,
        list(confirmed_by_key.values()),
        session_salt=str(job_id),
        document_profile=detected_profile,
    )
    if not propagated:
        if modified_pages_set:
            all_occs_upd: list[dict[str, Any]] = []
            for p_key in sorted(pages_dict.keys(), key=lambda x: int(x)):
                all_occs_upd.extend(pages_dict[p_key]["occurrences"])
            state["occurrences"] = all_occs_upd
        return sorted(modified_pages_set)

    propagated_by_page: dict[int, list[DetectedPIIEntity]] = {}
    for ent in propagated:
        propagated_by_page.setdefault(ent.page_number, []).append(ent)

    modified_pages: list[int] = sorted(modified_pages_set)

    for p_num in sorted(propagated_by_page.keys()):
        page_entry = pages_dict.get(str(p_num))
        if page_entry is None:
            continue

        existing_occs: list[dict[str, Any]] = list(page_entry.get("occurrences", []))
        existing_person_gids: dict[str, int] = {}
        for ex in existing_occs:
            if ex.get("entity_type") in person_types:
                gid = str(ex.get("group_id", ""))
                existing_person_gids[gid] = existing_person_gids.get(gid, 0) + len(str(ex.get("preview_text", "")))

        # Agrupa as ocorrências propagadas em blocos contíguos por `group_id` (cada ocorrência completa)
        prop_groups: list[list[DetectedPIIEntity]] = []
        current_group: list[DetectedPIIEntity] = []
        for ent in propagated_by_page[p_num]:
            if not current_group:
                current_group.append(ent)
            elif ent.is_continuation and ent.group_id == current_group[-1].group_id:
                current_group.append(ent)
            else:
                prop_groups.append(current_group)
                current_group = [ent]
        if current_group:
            prop_groups.append(current_group)

        gids_to_remove: set[str] = set()
        entities_to_add: list[DetectedPIIEntity] = []

        for grp_subs in prop_groups:
            target_gid = grp_subs[0].group_id
            full_prop_len = sum(len(s.matched_text) for s in grp_subs)
            already_present = False
            overlapping_partial_gids: set[str] = set()

            for sub in grp_subs:
                for ex in existing_occs:
                    if not _boxes_overlap(
                        sub.x0_norm,
                        sub.y0_norm,
                        sub.x1_norm,
                        sub.y1_norm,
                        float(ex.get("x0_norm", 0.0)),
                        float(ex.get("y0_norm", 0.0)),
                        float(ex.get("x1_norm", 0.0)),
                        float(ex.get("y1_norm", 0.0)),
                    ):
                        continue
                    ex_gid = str(ex.get("group_id", ""))
                    if ex_gid == target_gid:
                        already_present = True
                        break
                    if ex.get("entity_type") in person_types and existing_person_gids.get(ex_gid, 0) < full_prop_len:
                        overlapping_partial_gids.add(ex_gid)
                if already_present:
                    break

            if already_present:
                continue
            gids_to_remove.update(overlapping_partial_gids)
            entities_to_add.extend(grp_subs)

        if not gids_to_remove and not entities_to_add:
            continue

        filtered_occs = [
            ex for ex in existing_occs if str(ex.get("group_id", "")) not in gids_to_remove
        ]

        next_seq = len(filtered_occs) + 1
        existing_ids = {str(o.get("id", "")) for o in filtered_occs}
        for ent in entities_to_add:
            serialized = _serialize_entity_for_review(ent, next_seq)
            while serialized["id"] in existing_ids:
                next_seq += 1
                serialized = _serialize_entity_for_review(ent, next_seq)
            existing_ids.add(serialized["id"])
            filtered_occs.append(serialized)
            next_seq += 1

        page_entry["occurrences"] = filtered_occs
        if p_num not in modified_pages:
            modified_pages.append(p_num)

    if modified_pages:
        all_occs: list[dict[str, Any]] = []
        for p_key in sorted(pages_dict.keys(), key=lambda x: int(x)):
            all_occs.extend(pages_dict[p_key]["occurrences"])
        state["occurrences"] = all_occs

    return modified_pages


def process_single_page_into_state(
    doc: fitz.Document,
    page_num: int,
    job_id: str,
    state: dict[str, Any],
    base_temp_dir: Path,
    *,
    flush_spool_to_disk: bool = True,
    return_page_extraction: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], PageExtractionResult]:
    """Processa uma única página (`page_num` 1-indexed) com telemetria verbose em cada sub-etapa."""
    total_pages = len(doc)
    page = doc[page_num - 1]

    page_ext = extract_single_page(
        page,
        job_id=job_id,
        page_number=page_num,
        render_webp=True,
        base_temp_dir=base_temp_dir,
    )

    webp_w = page_ext.preview_webp.width_px if page_ext.preview_webp else 0
    webp_h = page_ext.preview_webp.height_px if page_ext.preview_webp else 0
    tokens_count = len(page_ext.tokens)
    ocr_desc = "OCR ONNX executado" if page_ext.ocr_executed else "Camada nativa de texto (sem OCR)"

    append_verbose_step(
        job_id,
        state,
        step_number=2,
        stage="ETAPA_2_EXTRACAO_OCR",
        stage_label="2. Extração Posicional & WebP",
        message=(
            f"Página {page_num}/{total_pages} renderizada em WebP 150 DPI ({webp_w}x{webp_h}px) • "
            f"Layout: {page_ext.page_type.value} • {ocr_desc} • {tokens_count} tokens mapeados."
        ),
        duration_ms=page_ext.processing_time_ms,
        details={
            "page_number": page_num,
            "total_pages": total_pages,
            "page_type": page_ext.page_type.value,
            "ocr_executed": page_ext.ocr_executed,
            "tokens_count": tokens_count,
            "width_px": webp_w,
            "height_px": webp_h,
        },
    )

    t_pii_start = time.perf_counter()
    if page_num == 1 or not state.get("document_profile"):
        detected_profile = classify_document_profile(page_ext.full_text)
        state["document_profile"] = detected_profile.value
        profile_human = (
            "Edital / Transparência Pública (sugere Mascaramento Parcial de CPFs/RGs)"
            if detected_profile == DocumentProfile.PUBLIC_TRANSPARENCY
            else "Despacho / Processo Restrito (sugere Tarja Preta Total 100%)"
        )
        append_verbose_step(
            job_id,
            state,
            step_number=2,
            stage="ETAPA_2_PERFIL_DOCUMENTAL",
            stage_label="2. Classificação de Perfil Semântico",
            message=f"Perfil semântico identificado na Página 1: {detected_profile.value} — {profile_human}.",
            details={"document_profile": detected_profile.value},
        )
    else:
        raw_prof = state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value)
        try:
            detected_profile = DocumentProfile(raw_prof)
        except ValueError:
            detected_profile = DocumentProfile.RESTRICTED_PROCESS

    entities = detect_pii_in_single_page(
        page_ext,
        session_salt=job_id,
        document_profile=detected_profile,
    )
    pii_elapsed_ms = (time.perf_counter() - t_pii_start) * 1000.0

    serialized_occurrences = [
        _serialize_entity_for_review(ent, idx + 1) for idx, ent in enumerate(entities)
    ]

    page_cat_counts: dict[str, int] = {}
    for occ_item in serialized_occurrences:
        c_key = occ_item["category"]
        page_cat_counts[c_key] = page_cat_counts.get(c_key, 0) + 1

    append_verbose_step(
        job_id,
        state,
        step_number=3,
        stage="ETAPA_3_DETECCAO_LGPD",
        stage_label="3. Detecção PII (Regex + Dígito + spaCy NLP)",
        message=(
            f"Página {page_num}/{total_pages} analisada: {len(serialized_occurrences)} ocorrência(s) "
            f"detectada(s) e classificada(s) segundo a LGPD "
            f"(Comum: {page_cat_counts.get('PERSONAL_COMMON', 0)}, "
            f"Sensível: {page_cat_counts.get('PERSONAL_SENSITIVE', 0)}, "
            f"Financeiro: {page_cat_counts.get('FINANCIAL_BANKING', 0)}, "
            f"Menor/Dep.: {page_cat_counts.get('MINOR_OR_DEPENDENT', 0)})."
        ),
        duration_ms=pii_elapsed_ms,
        details={
            "page_number": page_num,
            "occurrences_count": len(serialized_occurrences),
            "category_counts": page_cat_counts,
        },
    )

    page_payload = {
        "page_number": page_num,
        "page_type": page_ext.page_type.value,
        "width_px": webp_w,
        "height_px": webp_h,
        "image_url": f"/api/v1/documents/{job_id}/pages/{page_num}/image",
        "ocr_executed": page_ext.ocr_executed,
        "processing_time_ms": page_ext.processing_time_ms,
        "occurrences": serialized_occurrences,
    }

    pages_dict = state.setdefault("pages", {})
    pages_dict[str(page_num)] = page_payload

    all_occs: list[dict[str, Any]] = []
    for p_key in sorted(pages_dict.keys(), key=lambda x: int(x)):
        all_occs.extend(pages_dict[p_key]["occurrences"])
    state["occurrences"] = all_occs
    state["processed_pages"] = len(pages_dict)
    state["total_pages"] = total_pages

    if page_num == 1 and total_pages > 1:
        state["status"] = JobStatus.PARTIAL_REVIEW_READY.value
        append_verbose_step(
            job_id,
            state,
            step_number=3,
            stage="ETAPA_3_FIRST_PAGE_READY",
            stage_label="3. Liberação Imediata (First-Page-First)",
            message=(
                f"Página 1 pronta para revisão imediata (PARTIAL_REVIEW_READY). "
                f"Continuando processamento assíncrono das páginas 2 a {total_pages} em background."
            ),
            details={"status": JobStatus.PARTIAL_REVIEW_READY.value, "page_number": 1},
        )
        _sync_job_in_postgres_if_reachable(
            job_id,
            JobStatus.PARTIAL_REVIEW_READY,
            page_count=total_pages,
        )
    elif len(pages_dict) >= total_pages:
        state["status"] = JobStatus.AWAITING_REVIEW.value
    else:
        state["status"] = JobStatus.PARTIAL_REVIEW_READY.value

    store_job_transient_state(job_id, state, ttl=JOB_REDIS_TTL_SECONDS)
    if flush_spool_to_disk:
        _persist_state_to_job_spool(job_id, state)

    publish_job_event(
        job_id,
        "page_ready",
        {
            "page_number": page_num,
            "total_pages": total_pages,
            "status": state["status"],
            "document_profile": state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value),
            "image_url": page_payload["image_url"],
            "occurrences_count": len(serialized_occurrences),
            "occurrences": serialized_occurrences,
        },
    )
    publish_job_event(
        job_id,
        "progress",
        {
            "processed_pages": len(pages_dict),
            "total_pages": total_pages,
            "percentage": round((len(pages_dict) / max(1, total_pages)) * 100.0, 1),
            "status": state["status"],
        },
    )

    if return_page_extraction:
        return page_payload, page_ext
    return page_payload


@celery_app.task(name="app.worker.tasks.analyze_document_task")
def analyze_document_task(
    job_id: str,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> dict[str, Any]:
    """Task assíncrona First-Page-First com propagação cruzada de nomes entre páginas."""
    base_temp_dir = settings.TEMP_STORAGE_DIR
    job_dir = base_temp_dir / str(job_id)
    original_pdf_path = job_dir / "original.pdf"

    state = load_job_transient_state(job_id) or {
        "job_id": str(job_id),
        "status": JobStatus.ANALYZING.value,
        "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
        "pages": {},
        "occurrences": [],
        "verbose_logs": [],
        "processed_pages": 0,
        "total_pages": 0,
    }

    if not original_pdf_path.exists():
        state["status"] = JobStatus.FAILED.value
        store_job_transient_state(job_id, state)
        publish_job_event(job_id, "error", {"message": "Arquivo PDF original não encontrado no diretório transitório."})
        return state

    doc = fitz.open(str(original_pdf_path))
    cached_page_exts: dict[int, PageExtractionResult] = {}
    try:
        total_pages = len(doc)
        state["total_pages"] = total_pages
        effective_end = min(total_pages, end_page) if end_page is not None else total_pages

        for p_num in range(max(1, start_page), effective_end + 1):
            should_flush_disk = p_num == 1 or p_num == effective_end
            res = process_single_page_into_state(
                doc=doc,
                page_num=p_num,
                job_id=str(job_id),
                state=state,
                base_temp_dir=base_temp_dir,
                flush_spool_to_disk=should_flush_disk,
                return_page_extraction=True,
            )
            if isinstance(res, tuple):
                _, p_ext = res
                cached_page_exts[p_num] = p_ext

        if state.get("processed_pages", 0) >= total_pages:
            if total_pages > 1:
                modified_pages = _propagate_person_names_across_job_state(
                    doc=doc,
                    job_id=str(job_id),
                    state=state,
                    cached_page_exts=cached_page_exts,
                )
                for mod_p in modified_pages:
                    mod_payload = state["pages"].get(str(mod_p))
                    if mod_payload is not None:
                        publish_job_event(
                            job_id,
                            "page_ready",
                            {
                                "page_number": mod_p,
                                "total_pages": total_pages,
                                "status": JobStatus.AWAITING_REVIEW.value,
                                "document_profile": state.get(
                                    "document_profile", DocumentProfile.RESTRICTED_PROCESS.value
                                ),
                                "image_url": mod_payload["image_url"],
                                "occurrences_count": len(mod_payload["occurrences"]),
                                "occurrences": mod_payload["occurrences"],
                            },
                        )

            state["status"] = JobStatus.AWAITING_REVIEW.value
            total_occs = len(state.get("occurrences", []))
            unique_groups = len({o.get("group_id") for o in state.get("occurrences", [])})
            append_verbose_step(
                job_id,
                state,
                step_number=4,
                stage="ETAPA_4_AGUARDANDO_REVISAO",
                stage_label="4. Revisão e Seleção Human-in-the-Loop",
                message=(
                    f"Análise completa das {total_pages} página(s) concluída! "
                    f"Total de {total_occs} ocorrência(s) agrupadas em {unique_groups} entidade(s) única(s) "
                    f"aguardando conferência e aprovação do servidor."
                ),
                details={
                    "total_pages": total_pages,
                    "total_occurrences": total_occs,
                    "unique_groups": unique_groups,
                },
            )
            store_job_transient_state(job_id, state, ttl=JOB_REDIS_TTL_SECONDS)
            _persist_state_to_job_spool(str(job_id), state)
            _sync_job_in_postgres_if_reachable(
                str(job_id),
                JobStatus.AWAITING_REVIEW,
                page_count=total_pages,
            )
            publish_job_event(
                job_id,
                "analysis_complete",
                {
                    "status": JobStatus.AWAITING_REVIEW.value,
                    "document_profile": state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value),
                    "total_pages": total_pages,
                    "total_occurrences": total_occs,
                },
            )
    except Exception as exc:
        state["status"] = JobStatus.FAILED.value
        append_verbose_step(
            job_id,
            state,
            step_number=2,
            stage="ERRO_ANALISE",
            stage_label="Erro na Análise",
            message=f"Falha técnica durante a análise do documento ({type(exc).__name__}).",
            level="ERROR",
        )
        store_job_transient_state(job_id, state, ttl=JOB_REDIS_TTL_SECONDS)
        _persist_state_to_job_spool(str(job_id), state)
        publish_job_event(
            job_id,
            "error",
            {"code": "ANALYSIS_FAILED", "message": f"Erro durante análise do documento ({type(exc).__name__})."},
        )
        raise
    finally:
        doc.close()

    return state


@celery_app.task(name="app.worker.tasks.apply_redaction_task")
def apply_redaction_task(
    job_id: str,
    selected_ids: list[str] | None = None,
    manual_boxes: list[dict[str, Any]] | None = None,
    redaction_items: list[dict[str, Any]] | None = None,
    global_mode: str | None = None,
) -> dict[str, Any]:
    """
    Executa o tarjamento/mascaramento físico definitivo de forma idempotente e re-executável
    (lendo sempre de `original.pdf` intacto e removendo os arquivos antigos `redacted.pdf` e `report.pdf`).
    """
    base_temp_dir = settings.TEMP_STORAGE_DIR
    job_dir = base_temp_dir / str(job_id)
    original_pdf_path = job_dir / "original.pdf"
    redacted_pdf_path = job_dir / "redacted.pdf"
    report_pdf_path = job_dir / "report.pdf"

    state = load_job_transient_state(job_id)
    if state is None or not original_pdf_path.exists():
        publish_job_event(job_id, "error", {"message": "Sessão do documento expirada ou não encontrada."})
        return {"status": JobStatus.FAILED.value}

    # Remove artefatos anteriores para impedir devolução de arquivo antigo em re-execuções
    redacted_pdf_path.unlink(missing_ok=True)
    report_pdf_path.unlink(missing_ok=True)

    state["status"] = JobStatus.REDACTING.value
    state["redacted_file_sha256"] = None
    state["download_pdf_url"] = None
    state["download_report_url"] = None
    store_job_transient_state(job_id, state, ttl=JOB_REDIS_TTL_SECONDS)
    _persist_state_to_job_spool(str(job_id), state)

    all_detected_occs: list[dict[str, Any]] = state.get("occurrences", [])
    selected_set = set(selected_ids) if selected_ids is not None else {o["id"] for o in all_detected_occs}

    explicit_item_map: dict[str, dict[str, Any]] = {}
    for item in redaction_items or []:
        item_id = str(item.get("id", ""))
        if item_id:
            explicit_item_map[item_id] = item

    parsed_global_mode: RedactionActionMode | None = None
    if global_mode:
        try:
            parsed_global_mode = RedactionActionMode(str(global_mode).upper())
        except ValueError:
            parsed_global_mode = None

    boxes_to_redact: list[RedactionBoxSpec] = []

    cat_detected: dict[str, int] = {c.value: 0 for c in DataCategory}
    cat_blackout_auto: dict[str, int] = {c.value: 0 for c in DataCategory}
    cat_masked_auto: dict[str, int] = {c.value: 0 for c in DataCategory}
    cat_kept: dict[str, int] = {c.value: 0 for c in DataCategory}
    cat_manual: dict[str, int] = {c.value: 0 for c in DataCategory}

    updated_modes_by_id: dict[str, tuple[str, bool, str]] = {}

    for occ in all_detected_occs:
        occ_id = occ["id"]
        cat_key = _normalize_category_key(occ.get("category"))
        cat_detected[cat_key] = cat_detected.get(cat_key, 0) + 1
        is_cont = bool(occ.get("is_continuation", False))

        if occ_id in explicit_item_map:
            raw_mode = str(explicit_item_map[occ_id].get("action_mode", RedactionActionMode.BLACKOUT.value)).upper()
            try:
                effective_mode = RedactionActionMode(raw_mode)
            except ValueError:
                effective_mode = RedactionActionMode.BLACKOUT
            effective_mask_preview = "" if is_cont else (explicit_item_map[occ_id].get("masked_preview") or occ.get("masked_preview"))
        elif parsed_global_mode is not None and (selected_ids is None or occ_id in selected_set):
            effective_mode = parsed_global_mode
            effective_mask_preview = "" if is_cont else occ.get("masked_preview")
        elif selected_ids is not None and occ_id not in selected_set:
            effective_mode = RedactionActionMode.KEEP
            effective_mask_preview = "" if is_cont else occ.get("masked_preview")
        else:
            raw_occ_mode = str(occ.get("action_mode", RedactionActionMode.BLACKOUT.value)).upper()
            try:
                effective_mode = RedactionActionMode(raw_occ_mode)
            except ValueError:
                effective_mode = RedactionActionMode.BLACKOUT
            if effective_mode == RedactionActionMode.KEEP and (selected_ids is None or occ_id in selected_set):
                effective_mode = RedactionActionMode.BLACKOUT
            effective_mask_preview = "" if is_cont else occ.get("masked_preview")

        # Sobrescreve definitivamente no estado transitório para refletir a escolha atual
        occ["action_mode"] = effective_mode.value
        occ["selected"] = effective_mode != RedactionActionMode.KEEP
        if not is_cont and effective_mask_preview:
            occ["masked_preview"] = effective_mask_preview
        updated_modes_by_id[occ_id] = (
            effective_mode.value,
            effective_mode != RedactionActionMode.KEEP,
            "" if is_cont else str(occ.get("masked_preview") or ""),
        )

        if effective_mode == RedactionActionMode.KEEP:
            cat_kept[cat_key] = cat_kept.get(cat_key, 0) + 1
            continue

        if effective_mode == RedactionActionMode.PARTIAL_MASK:
            cat_masked_auto[cat_key] = cat_masked_auto.get(cat_key, 0) + 1
        else:
            cat_blackout_auto[cat_key] = cat_blackout_auto.get(cat_key, 0) + 1

        boxes_to_redact.append(
            RedactionBoxSpec(
                page_number=int(occ["page_number"]),
                x0_norm=float(occ["x0_norm"]),
                y0_norm=float(occ["y0_norm"]),
                x1_norm=float(occ["x1_norm"]),
                y1_norm=float(occ["y1_norm"]),
                category=DataCategory(cat_key),
                expected_text=occ.get("preview_text"),
                action_mode=effective_mode,
                masked_preview=effective_mask_preview,
                entity_type=occ.get("entity_type"),
                is_ocr=bool(occ.get("is_ocr", False)),
                safe_x0_norm=occ.get("ocr_safe_left_norm"),
                safe_x1_norm=occ.get("ocr_safe_right_norm"),
            )
        )

    for p_entry in state.get("pages", {}).values():
        for p_occ in p_entry.get("occurrences", []):
            upd = updated_modes_by_id.get(p_occ.get("id", ""))
            if upd is not None:
                p_occ["action_mode"] = upd[0]
                p_occ["selected"] = upd[1]
                p_occ["masked_preview"] = upd[2]

    for mbox in manual_boxes or []:
        m_cat = _normalize_category_key(mbox.get("category"))
        cat_manual[m_cat] = cat_manual.get(m_cat, 0) + 1
        boxes_to_redact.append(
            RedactionBoxSpec(
                page_number=int(mbox["page_number"]),
                x0_norm=float(mbox["x0_norm"]),
                y0_norm=float(mbox["y0_norm"]),
                x1_norm=float(mbox["x1_norm"]),
                y1_norm=float(mbox["y1_norm"]),
                category=DataCategory(m_cat),
                action_mode=RedactionActionMode.BLACKOUT,
            )
        )

    total_blackout = sum(cat_blackout_auto.values())
    total_masked = sum(cat_masked_auto.values())
    total_kept = sum(cat_kept.values())
    total_manual = sum(cat_manual.values())

    append_verbose_step(
        job_id,
        state,
        step_number=4,
        stage="ETAPA_4_DECISAO_CONSOLIDADA",
        stage_label="4. Consolidação da Revisão Humana",
        message=(
            f"Decisão Human-in-the-Loop consolidada: {total_blackout} Tarja(s) Preta(s) Total(is), "
            f"{total_masked} Mascaramento(s) Parcial(is) (***.XXX-**), {total_manual} Tarja(s) Manual(is) "
            f"e {total_kept} ocorrência(s) mantida(s) visível(is)."
        ),
        details={
            "blackout_count": total_blackout,
            "masked_count": total_masked,
            "manual_count": total_manual,
            "kept_count": total_kept,
        },
    )

    pipeline_res = execute_complete_redaction_pipeline(
        original_pdf_path=original_pdf_path,
        boxes=boxes_to_redact,
        final_output_pdf_path=redacted_pdf_path,
    )

    append_verbose_step(
        job_id,
        state,
        step_number=5,
        stage="ETAPA_5_REDACAO_FISICA",
        stage_label="5. True Redaction (PyMuPDF) & Sanitização (pikepdf)",
        message=(
            f"Redação física concluída em {pipeline_res.physical_result.execution_time_ms:.1f}ms "
            f"({pipeline_res.total_boxes_applied} regiões destruídas nas páginas {pipeline_res.affected_pages}) • "
            f"Sanitização estrutural pikepdf concluída em {pipeline_res.sanitization_result.execution_time_ms:.1f}ms "
            f"(SHA-256 final: {pipeline_res.final_sha256[:16]}...)."
        ),
        duration_ms=pipeline_res.total_execution_time_ms,
        details={
            "boxes_applied": pipeline_res.total_boxes_applied,
            "affected_pages": pipeline_res.affected_pages,
            "pymupdf_ms": round(pipeline_res.physical_result.execution_time_ms, 2),
            "pikepdf_ms": round(pipeline_res.sanitization_result.execution_time_ms, 2),
            "redacted_sha256_prefix": pipeline_res.final_sha256[:16],
        },
    )

    state["status"] = JobStatus.VALIDATING.value
    store_job_transient_state(job_id, state, ttl=JOB_REDIS_TTL_SECONDS)

    security_val = validate_redacted_pdf_surgically(
        redacted_pdf_path=redacted_pdf_path,
        boxes=boxes_to_redact,
    )

    append_verbose_step(
        job_id,
        state,
        step_number=6,
        stage="ETAPA_6_VALIDACAO_CIRURGICA",
        stage_label="6. Validação Cirúrgica Pós-Tarjamento",
        message=(
            f"Inspeção cirúrgica por recorte concluída em {security_val.validation_time_ms:.1f}ms "
            f"({security_val.boxes_checked} regiões inspecionadas): "
            f"Camada de texto={'OK' if security_val.text_layer_verified else 'FALHA'}, "
            f"Camada de pixels={'OK' if security_val.pixel_layer_verified else 'FALHA'}, "
            f"Metadados/Estrutura={'OK' if security_val.structure_verified else 'FALHA'}."
        ),
        duration_ms=security_val.validation_time_ms,
        details={
            "passed": security_val.passed,
            "boxes_checked": security_val.boxes_checked,
            "text_layer_verified": security_val.text_layer_verified,
            "pixel_layer_verified": security_val.pixel_layer_verified,
            "structure_verified": security_val.structure_verified,
        },
    )

    t_rep_start = time.perf_counter()
    category_breakdown: dict[str, CategoryQuantitativeRow] = {}
    final_category_counts: dict[str, int] = {}

    for c in DataCategory:
        ck = c.value
        applied_for_cat = (
            cat_blackout_auto.get(ck, 0)
            + cat_masked_auto.get(ck, 0)
            + cat_manual.get(ck, 0)
        )
        final_category_counts[ck] = applied_for_cat
        category_breakdown[ck] = CategoryQuantitativeRow(
            detected_count=cat_detected.get(ck, 0),
            redacted_count=cat_blackout_auto.get(ck, 0),
            masked_count=cat_masked_auto.get(ck, 0),
            kept_count=cat_kept.get(ck, 0),
            manual_added_count=cat_manual.get(ck, 0),
        )

    report_data = ZeroPIIReportData(
        job_id=job_id,
        document_label=state.get("filename", "documento_institucional.pdf"),
        original_sha256=pipeline_res.original_sha256,
        redacted_sha256=pipeline_res.final_sha256,
        total_pages=pipeline_res.total_pages,
        affected_pages=pipeline_res.affected_pages,
        category_breakdown=category_breakdown,
        security_validation=security_val,
        document_profile=state.get("document_profile", DocumentProfile.RESTRICTED_PROCESS.value),
    )
    generate_zero_pii_processing_report(report_data, report_pdf_path)
    rep_elapsed_ms = (time.perf_counter() - t_rep_start) * 1000.0

    # Expurga textos literais transitórios da sessão concluída mantendo masked_preview e entity_type intactos para re-geração
    for occ in state.get("occurrences", []):
        occ.pop("preview_text", None)
    for p_entry in state.get("pages", {}).values():
        for occ in p_entry.get("occurrences", []):
            occ.pop("preview_text", None)

    append_verbose_step(
        job_id,
        state,
        step_number=7,
        stage="ETAPA_7_EXPORTACAO_AUDITORIA",
        stage_label="7. Relatório Zero-PII & Expurgo de Sessão",
        message=(
            f"Relatório Institucional de Processamento gerado em {rep_elapsed_ms:.1f}ms • "
            f"Textos transitórios de revisão expurgados da memória (Política Zero-PII) • "
            f"Documento PDF tarjado e Relatório prontos para download."
        ),
        duration_ms=rep_elapsed_ms,
        details={
            "report_generated": True,
            "zero_pii_purged": True,
            "total_redactions_applied": pipeline_res.total_boxes_applied,
        },
    )

    audit_summary = {
        "job_id": str(job_id),
        "original_document_sha256": pipeline_res.original_sha256,
        "redacted_document_sha256": pipeline_res.final_sha256,
        "count_personal_common": final_category_counts[DataCategory.PERSONAL_COMMON.value],
        "count_personal_sensitive": final_category_counts[DataCategory.PERSONAL_SENSITIVE.value],
        "count_financial_banking": final_category_counts[DataCategory.FINANCIAL_BANKING.value],
        "count_minor_or_dependent": final_category_counts[DataCategory.MINOR_OR_DEPENDENT.value],
        "total_redactions_applied": pipeline_res.total_boxes_applied,
        "category_counts": final_category_counts,
        "affected_pages": pipeline_res.affected_pages,
        "total_pages": pipeline_res.total_pages,
        "security_validation_passed": security_val.passed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    state["status"] = JobStatus.COMPLETED.value
    state["redacted_file_sha256"] = pipeline_res.final_sha256
    state["audit_log"] = audit_summary
    state["download_pdf_url"] = f"/api/v1/documents/{job_id}/download/pdf"
    state["download_report_url"] = f"/api/v1/documents/{job_id}/download/report"

    store_job_transient_state(job_id, state, ttl=JOB_REDIS_TTL_SECONDS)
    _persist_state_to_job_spool(str(job_id), state)

    _sync_job_in_postgres_if_reachable(
        str(job_id),
        JobStatus.COMPLETED,
        page_count=pipeline_res.total_pages,
        redacted_sha256=pipeline_res.final_sha256,
        audit_payload=audit_summary,
    )

    publish_job_event(
        job_id,
        "redaction_complete",
        {
            "status": JobStatus.COMPLETED.value,
            "original_sha256": pipeline_res.original_sha256,
            "redacted_sha256": pipeline_res.final_sha256,
            "total_redactions_applied": pipeline_res.total_boxes_applied,
            "affected_pages": pipeline_res.affected_pages,
            "security_validation_passed": security_val.passed,
            "download_pdf_url": state["download_pdf_url"],
            "download_report_url": state["download_report_url"],
        },
    )

    return state


@celery_app.task(name="app.worker.tasks.cleanup_expired_jobs_task")
def cleanup_expired_jobs_task() -> dict[str, int]:
    """Task periódica de expurgo para remover diretórios temporários de jobs expirados."""
    spool_dir = settings.TEMP_STORAGE_DIR
    removed_dirs = 0
    removed_keys = 0
    now = time.time() if hasattr(time, "time") else 0.0
    ttl = settings.TEMP_FILE_TTL_SECONDS

    if spool_dir.exists():
        for entry in spool_dir.iterdir():
            if entry.is_dir():
                try:
                    age = now - entry.stat().st_mtime
                    if age >= ttl:
                        shutil.rmtree(entry, ignore_errors=True)
                        removed_dirs += 1
                except OSError:
                    pass

    for k in list(_IN_MEMORY_KV_STORE.keys()):
        exp_ts, _ = _IN_MEMORY_KV_STORE[k]
        if now >= exp_ts:
            _IN_MEMORY_KV_STORE.pop(k, None)
            removed_keys += 1

    return {"removed_job_dirs": removed_dirs, "removed_memory_keys": removed_keys}
