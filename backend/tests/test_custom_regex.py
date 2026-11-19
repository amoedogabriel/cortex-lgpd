"""
Testes automatizados para suporte a Expressões Regulares Personalizadas (`test_custom_regex.py`).
Verifica validação de segurança (ReDoS, sintaxe, match vazio), detecção no pipeline híbrido,
destruição física no PDF e envio via endpoint REST `/api/v1/documents/upload`.
"""

from __future__ import annotations

import io
import json

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import DataCategory, RedactionActionMode
from app.services.pdf_extractor.extractor import extract_single_page
from app.services.pii_detector.entity_mapper import detect_pii_in_single_page
from app.services.pii_detector.regex_patterns import compile_custom_regex_specs
from app.services.redactor.pdf_redactor import RedactionBoxSpec, apply_true_redactions_to_pdf


def _build_pdf_bytes(lines: list[str]) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 72
    for line in lines:
        page.insert_text((54, y), line, fontsize=11, fontname="helv")
        y += 22
    out = doc.tobytes()
    doc.close()
    return out


def test_compile_custom_regex_specs_validation() -> None:
    specs = compile_custom_regex_specs(
        [
            {
                "label": "CODIGO_PROEG",
                "pattern": r"PROEG-\d{4}-\d{3}",
                "category": "PERSONAL_SENSITIVE",
                "action_mode": "PARTIAL_MASK",
                "case_sensitive": False,
            }
        ]
    )
    assert len(specs) == 1
    assert specs[0].entity_type == "CUSTOM:CODIGO_PROEG"
    assert specs[0].category == DataCategory.PERSONAL_SENSITIVE
    assert specs[0].preferred_action_mode == RedactionActionMode.PARTIAL_MASK
    assert specs[0].is_custom is True

    with pytest.raises(ValueError, match="Sintaxe inválida"):
        compile_custom_regex_specs([{"label": "BROKEN", "pattern": r"[unclosed"}])

    with pytest.raises(ValueError, match="casa texto vazio"):
        compile_custom_regex_specs([{"label": "EMPTY_MATCH", "pattern": r"a*"}])

    with pytest.raises(ValueError, match="ReDoS"):
        compile_custom_regex_specs([{"label": "REDOS", "pattern": r"(a+)+b"}])

    too_many = [{"label": f"R{i}", "pattern": r"ABC-\d+"} for i in range(21)]
    with pytest.raises(ValueError, match="Limite máximo"):
        compile_custom_regex_specs(too_many)


def test_custom_regex_detection_and_physical_redaction(tmp_path) -> None:
    pdf_bytes = _build_pdf_bytes(
        [
            "Documento Administrativo UFAM",
            "Protocolo interno: PROEG-2026-412 para conferencia.",
            "CPF do requerente: 348.729.160-69",
        ]
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_ext = extract_single_page(
            doc[0],
            job_id="custom_regex_unit",
            page_number=1,
            render_webp=False,
            base_temp_dir=tmp_path,
        )
    finally:
        doc.close()

    custom_specs = compile_custom_regex_specs(
        [
            {
                "label": "CODIGO_PROEG",
                "pattern": r"PROEG-\d{4}-\d{3}",
                "category": DataCategory.FINANCIAL_BANKING,
                "action_mode": RedactionActionMode.BLACKOUT,
            }
        ]
    )
    entities = detect_pii_in_single_page(
        page_ext,
        session_salt="custom_regex_unit",
        custom_regex_specs=custom_specs,
    )

    custom_hits = [e for e in entities if e.entity_type == "CUSTOM:CODIGO_PROEG"]
    cpf_hits = [e for e in entities if e.entity_type == "CPF"]

    assert len(custom_hits) == 1
    assert custom_hits[0].matched_text == "PROEG-2026-412"
    assert custom_hits[0].category == DataCategory.FINANCIAL_BANKING
    assert custom_hits[0].detector_source == "CUSTOM_REGEX"
    assert custom_hits[0].action_mode == RedactionActionMode.BLACKOUT
    assert len(cpf_hits) == 1

    orig_path = tmp_path / "orig.pdf"
    red_path = tmp_path / "redacted.pdf"
    orig_path.write_bytes(pdf_bytes)

    boxes = [
        RedactionBoxSpec(
            page_number=e.page_number,
            x0_norm=e.x0_norm,
            y0_norm=e.y0_norm,
            x1_norm=e.x1_norm,
            y1_norm=e.y1_norm,
            category=e.category,
            action_mode=RedactionActionMode.BLACKOUT,
        )
        for e in custom_hits
    ]
    apply_true_redactions_to_pdf(
        original_pdf_path=orig_path,
        boxes=boxes,
        output_pdf_path=red_path,
    )

    red_doc = fitz.open(str(red_path))
    try:
        red_text = red_doc[0].get_text("text")
    finally:
        red_doc.close()

    assert "PROEG-2026-412" not in red_text
    assert "Documento Administrativo UFAM" in red_text


def test_upload_endpoint_with_and_without_custom_regexes() -> None:
    pdf_bytes = _build_pdf_bytes(
        [
            "Solicitacao de Bolsa PROEG",
            "Identificador interno: BOLSA-UFAM-7788",
        ]
    )
    client = TestClient(app)

    # 1. Envio com regra personalizada valida
    rules_payload = json.dumps(
        [
            {
                "label": "BOLSA_ID",
                "pattern": r"BOLSA-UFAM-\d{4}",
                "category": "PERSONAL_COMMON",
                "action_mode": "PARTIAL_MASK",
                "case_sensitive": True,
            }
        ]
    )
    resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("custom_test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        data={"custom_regexes": rules_payload},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    occ_resp = client.get(f"/api/v1/documents/{job_id}/occurrences")
    assert occ_resp.status_code == 200
    occs = occ_resp.json()["occurrences"]
    custom_occs = [o for o in occs if o["entity_type"] == "CUSTOM:BOLSA_ID"]
    assert len(custom_occs) == 1
    assert custom_occs[0]["preview_text"] == "BOLSA-UFAM-7788"
    assert custom_occs[0]["action_mode"] == "PARTIAL_MASK"
    assert custom_occs[0]["detector_source"] == "CUSTOM_REGEX"

    # 2. Regra invalida retorna HTTP 422
    bad_resp = client.post(
        "/api/v1/documents/upload",
        files={"file": ("custom_bad.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        data={"custom_regexes": json.dumps([{"label": "BAD", "pattern": "[broken"}])},
    )
    assert bad_resp.status_code == 422

    # 3. Fluxo padrao sem custom_regexes permanece intacto
    plain_resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("plain_test.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert plain_resp.status_code == 202
