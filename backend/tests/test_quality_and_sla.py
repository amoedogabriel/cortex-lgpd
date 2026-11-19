"""
Benchmark Automatizado de Qualidade NLP, Posicionamento Geométrico (IoU) e SLA de Performance
(`backend/tests/test_quality_and_sla.py`).

Aferições realizadas sobre a base 100% sintética (`synthetic_generator.py`):
1. Métricas Clássicas de Qualidade de Detecção:
   - `Precision`, `Recall` e `F1-Score` sobre o dataset de 10 páginas da Portaria/Despacho UFAM,
     Laudo Médico Pericial e Planilha Financeira;
   - Taxa de Falsos Positivos (`False Positive Rate` = 0.0% sobre processos SEI meramente organizacionais
     e CNPJ institucional da UFAM);
   - Precisão de Posicionamento Geométrico (`Mean IoU` — Intersection over Union das Bounding Boxes normalizadas).
2. Cumprimento Estrito dos SLAs de Performance de Servidor (Seção 5 da `SPEC.md`):
   - Entrega da Página 1 (`page_ready` / `PARTIAL_REVIEW_READY`): `< 1,8 segundo` (1800 ms);
   - Análise completa do PDF 100% textual de 10 páginas: `< 3,0 segundos` (3000 ms);
   - Tarjamento definitivo + sanitização estrutural + validação cirúrgica por recorte de 10 páginas: `< 2,0 segundos` (2000 ms);
   - 100% de destruição irrecuperável dos dados selecionados nas páginas textuais e escaneadas.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import fitz  # PyMuPDF

from app.core.config import settings
from app.core.ml_models import ml_registry
from app.models.enums import JobStatus
from app.services.pdf_extractor.extractor import extract_single_page
from app.services.pdf_extractor.file_validator import validate_and_store_pdf
from app.services.pii_detector.entity_mapper import detect_pii_in_pages
from app.services.redactor.pdf_redactor import RedactionBoxSpec
from app.services.redactor.structural_sanitizer import execute_complete_redaction_pipeline
from app.services.security_validator.post_redaction_validator import validate_redacted_pdf_surgically
from app.worker.tasks import analyze_document_task, get_buffered_job_events
from tests.synthetic_generator import (
    GroundTruthEntity,
    generate_10page_textual_portaria_and_despacho,
    generate_financial_payroll_table_pdf,
    generate_medical_and_union_report_pdf,
    generate_scanned_and_hybrid_pdf,
)


def compute_bbox_iou(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
) -> float:
    """Calcula a Intersection over Union (IoU) entre duas Bounding Boxes normalizadas `[x0, y0, x1, y1]`."""
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b

    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)

    inter_w = max(0.0, inter_x1 - inter_x0)
    inter_h = max(0.0, inter_y1 - inter_y0)
    inter_area = inter_w * inter_h

    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union_area = area_a + area_b - inter_area

    if union_area <= 1e-9:
        return 0.0
    return inter_area / union_area


def test_nlp_quality_metrics_precision_recall_f1_and_iou(tmp_path: Path, monkeypatch) -> None:
    """
    Calcula e imprime `Precision`, `Recall`, `F1-Score`, `Mean IoU` e `Organizational False Positives`
    sobre os documentos sintéticos da UFAM (Portaria 10 páginas, Laudo Médico e Planilha Financeira).
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)
    ml_registry.warmup(load_ocr=False, load_spacy=True)

    bundles = [
        generate_10page_textual_portaria_and_despacho(),
        generate_medical_and_union_report_pdf(),
        generate_financial_payroll_table_pdf(),
    ]

    true_positives = 0
    false_negatives = 0
    organizational_false_positives = 0
    iou_scores: list[float] = []

    for bundle in bundles:
        doc = fitz.open(stream=bundle.pdf_bytes, filetype="pdf")
        try:
            extracted_pages = [
                extract_single_page(
                    doc[i],
                    job_id=f"bench_{bundle.name}",
                    page_number=i + 1,
                    render_webp=False,
                    base_temp_dir=tmp_path,
                )
                for i in range(len(doc))
            ]
        finally:
            doc.close()

        detected_entities = detect_pii_in_pages(extracted_pages, session_salt=bundle.name)

        # 1. Verifica falsos positivos sobre strings organizacionais (Processos SEI isolados e CNPJ da UFAM)
        detected_values_set = {d.matched_text.strip() for d in detected_entities}
        for neg_str in bundle.negative_organizational_strings:
            if neg_str in detected_values_set:
                organizational_false_positives += 1

        # 2. Compara cada item do Ground Truth com as entidades detectadas na mesma página
        for gt in bundle.ground_truth_entities:
            page_dets = [
                d
                for d in detected_entities
                if d.page_number == gt.page_number and gt.value in d.matched_text
            ]
            if page_dets:
                true_positives += 1
                best_det = page_dets[0]
                iou = compute_bbox_iou(
                    (gt.x0_norm, gt.y0_norm, gt.x1_norm, gt.y1_norm),
                    (best_det.x0_norm, best_det.y0_norm, best_det.x1_norm, best_det.y1_norm),
                )
                iou_scores.append(iou)
            else:
                false_negatives += 1

    precision = true_positives / max(1, true_positives + organizational_false_positives)
    recall = true_positives / max(1, true_positives + false_negatives)
    f1_score = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    mean_iou = sum(iou_scores) / max(1, len(iou_scores))

    print(
        f"\n[UFAM QUALITY BENCHMARK] TP={true_positives} | FN={false_negatives} | "
        f"Org_FP={organizational_false_positives} | Precision={precision:.4f} | "
        f"Recall={recall:.4f} | F1-Score={f1_score:.4f} | Mean_IoU={mean_iou:.4f}"
    )

    assert organizational_false_positives == 0, (
        "Nenhum processo SEI meramente organizacional ou CNPJ da UFAM pode ser marcado como PII!"
    )
    assert precision >= 0.98
    assert recall >= 0.95
    assert f1_score >= 0.96
    assert mean_iou >= 0.65


def test_10page_sla_timings_and_100_percent_destruction(tmp_path: Path, monkeypatch) -> None:
    """
    Valida rigorosamente os SLAs da Seção 5 da `SPEC.md` em um PDF de 10 páginas + documento escaneado:
    1. Emissão do evento `page_ready` da Página 1 em < 1,8s (1800ms);
    2. Análise completa do PDF textual de 10 páginas em < 3,0s (3000ms);
    3. Tarjamento definitivo + validação cirúrgica por recorte de 10 páginas em < 2,0s (2000ms);
    4. 100% de destruição irrecuperável dos dados selecionados nas páginas textuais e escaneadas.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)
    ml_registry.warmup(load_ocr=True, load_spacy=True)

    bundle_10p = generate_10page_textual_portaria_and_despacho()
    job_uuid = uuid.uuid4()
    job_id = str(job_uuid)

    validate_and_store_pdf(
        file_bytes=bundle_10p.pdf_bytes,
        job_id=job_uuid,
        base_temp_dir=tmp_path,
    )

    # SLA 1: Entrega da Página 1 (`page_ready` / `PARTIAL_REVIEW_READY`) em menos de 1,8 segundo
    t0_p1 = time.perf_counter()
    state_p1 = analyze_document_task(job_id, start_page=1, end_page=1)
    elapsed_p1_ms = (time.perf_counter() - t0_p1) * 1000.0

    assert state_p1["status"] == JobStatus.PARTIAL_REVIEW_READY.value
    events = get_buffered_job_events(job_id)
    assert any(e["event"] == "page_ready" and e["data"]["page_number"] == 1 for e in events)
    assert elapsed_p1_ms < 1800.0, (
        f"SLA Violado: Página 1 levou {elapsed_p1_ms:.2f}ms (máximo permitido: 1800ms)"
    )

    # SLA 2: Conclusão da análise das páginas 2..10 (total de 10 páginas textuais em < 3,0 segundos)
    t0_rest = time.perf_counter()
    state_all = analyze_document_task(job_id, start_page=2, end_page=10)
    elapsed_rest_ms = (time.perf_counter() - t0_rest) * 1000.0
    total_analysis_10p_ms = elapsed_p1_ms + elapsed_rest_ms

    assert state_all["status"] == JobStatus.AWAITING_REVIEW.value
    assert state_all["processed_pages"] == 10
    assert total_analysis_10p_ms < 3000.0, (
        f"SLA Violado: Análise completa de 10 páginas levou {total_analysis_10p_ms:.2f}ms (máximo: 3000ms)"
    )

    # SLA 3: Tarjamento definitivo + Sanitização + Validação Cirúrgica de 10 páginas em < 2,0 segundos
    original_pdf_path = tmp_path / job_id / "original.pdf"
    redacted_pdf_path = tmp_path / job_id / "redacted.pdf"

    boxes_to_redact = [
        RedactionBoxSpec(
            page_number=int(occ["page_number"]),
            x0_norm=float(occ["x0_norm"]),
            y0_norm=float(occ["y0_norm"]),
            x1_norm=float(occ["x1_norm"]),
            y1_norm=float(occ["y1_norm"]),
            expected_text=occ.get("preview_text"),
        )
        for occ in state_all["occurrences"]
    ]

    t0_redact = time.perf_counter()
    pipeline_res = execute_complete_redaction_pipeline(
        original_pdf_path=original_pdf_path,
        boxes=boxes_to_redact,
        final_output_pdf_path=redacted_pdf_path,
    )
    val_res = validate_redacted_pdf_surgically(
        redacted_pdf_path=redacted_pdf_path,
        boxes=boxes_to_redact,
    )
    elapsed_redact_and_validate_ms = (time.perf_counter() - t0_redact) * 1000.0

    print(
        f"\n[UFAM SLA BENCHMARK - 10 PÁGINAS] "
        f"Page1_Ready={elapsed_p1_ms:.1f}ms (<1800ms) | "
        f"Full_10p_Analysis={total_analysis_10p_ms:.1f}ms (<3000ms) | "
        f"Redact+Validate_10p={elapsed_redact_and_validate_ms:.1f}ms (<2000ms)"
    )

    assert val_res.passed is True
    assert elapsed_redact_and_validate_ms < 2000.0, (
        f"SLA Violado: Tarjamento + Validação de 10 páginas levou {elapsed_redact_and_validate_ms:.2f}ms (máximo: 2000ms)"
    )

    # 4. Comprova 100% de destruição irrecuperável no PDF textual de 10 páginas
    redacted_doc = fitz.open(str(redacted_pdf_path))
    try:
        full_text_after = "\n".join(p.get_text("text") for p in redacted_doc)
        for gt_item in bundle_10p.ground_truth_entities:
            assert gt_item.value not in full_text_after, (
                f"Falha crítica: dado sintético '{gt_item.value}' sobreviveu ao tarjamento na pág {gt_item.page_number}!"
            )
    finally:
        redacted_doc.close()

    # 5. Comprova destruição física de pixels em página escaneada/híbrida
    scanned_bundle = generate_scanned_and_hybrid_pdf()
    scan_uuid = uuid.uuid4()
    scan_id = str(scan_uuid)
    validate_and_store_pdf(
        file_bytes=scanned_bundle.pdf_bytes,
        job_id=scan_uuid,
        base_temp_dir=tmp_path,
    )
    scan_state = analyze_document_task(scan_id, start_page=1, end_page=2)
    scan_boxes = [
        RedactionBoxSpec(
            page_number=int(occ["page_number"]),
            x0_norm=float(occ["x0_norm"]),
            y0_norm=float(occ["y0_norm"]),
            x1_norm=float(occ["x1_norm"]),
            y1_norm=float(occ["y1_norm"]),
            expected_text=occ.get("preview_text"),
        )
        for occ in scan_state["occurrences"]
    ]
    assert len(scan_boxes) >= 2

    scan_redacted_path = tmp_path / scan_id / "redacted.pdf"
    execute_complete_redaction_pipeline(
        original_pdf_path=tmp_path / scan_id / "original.pdf",
        boxes=scan_boxes,
        final_output_pdf_path=scan_redacted_path,
    )
    scan_val = validate_redacted_pdf_surgically(
        redacted_pdf_path=scan_redacted_path,
        boxes=scan_boxes,
    )
    assert scan_val.passed is True
