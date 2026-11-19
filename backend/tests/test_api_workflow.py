"""
Testes de Integração da Etapa 5 (`backend/tests/test_api_workflow.py`):
- Pipeline Progressivo First-Page-First (`analyze_document_task`, `PARTIAL_REVIEW_READY`, `page_ready` via SSE);
- Disponibilidade imediata da Página 1 (`page_1.webp` com `Cache-Control: private, max-age=3600`) antes da conclusão das páginas subsequentes;
- Endpoints REST e SSE (`/api/v1/documents/upload`, `/stream`, `/pages/{page_num}/image`, `/occurrences`, `/redact`, `/download/pdf`, `/download/report`);
- Telemetria Verbose Zero-PII em cada etapa (`verbose_logs` e eventos `verbose_step` via SSE);
- Motor Híbrido de Redação Definitiva (`BLACKOUT` vs. `PARTIAL_MASK` vs. `KEEP`) e Recomendação Inteligente (`document_profile`);
- Re-geração dinâmica no mesmo `job_id` (`BLACKOUT` -> `PARTIAL_MASK` -> `BLACKOUT`) e headers anti-cache no download;
- Geração do Relatório Institucional de Processamento Zero-PII em PDF (`report_generator.py`) e registro de `AuditLog` sem jamais armazenar textos literais de dados pessoais (CPF, Nome, CID, etc.);
- Headers CORS explícitos (`Access-Control-Allow-Origin`) no upload e em respostas de erro 500/422.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.enums import DocumentProfile, JobStatus, RedactionActionMode
from app.services.pdf_extractor.file_validator import validate_and_store_pdf
from app.services.pii_detector.entity_mapper import (
    classify_document_profile,
    generate_masked_preview,
)
from app.worker.tasks import (
    analyze_document_task,
    cleanup_expired_jobs_task,
    get_buffered_job_events,
    load_job_transient_state,
)


def _format_cpf(nine_digits: str) -> str:
    nums = [int(c) for c in nine_digits]
    s1 = sum(nums[i] * (10 - i) for i in range(9))
    r1 = (s1 * 10) % 11
    d1 = 0 if r1 == 10 else r1
    nums.append(d1)

    s2 = sum(nums[i] * (11 - i) for i in range(10))
    r2 = (s2 * 10) % 11
    d2 = 0 if r2 == 10 else r2
    nums.append(d2)

    full = "".join(str(x) for x in nums)
    return f"{full[:3]}.{full[3:6]}.{full[6:9]}-{full[9:]}"


def _build_multipage_ufam_pdf(cpf_p1: str, cpf_p2: str, cpf_p3: str) -> bytes:
    """Cria um PDF multipágina (3 páginas) simulando um processo administrativo da UFAM."""
    doc = fitz.open()

    # Página 1
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text(
        fitz.Point(50, 70),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - PROGESP - DESPACHO PÁGINA 1",
        fontsize=12,
    )
    p1.insert_text(
        fitz.Point(50, 115),
        f"Interessado: Servidor João Carlos Almeida, portador do CPF {cpf_p1} e Matrícula SIAPE 1357924.",
        fontsize=11,
    )

    # Página 2
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text(
        fitz.Point(50, 70),
        "LAUDO MÉDICO PERICIAL - SUBSISTEMA INTEGRADO DE ATENÇÃO À SAÚDE - PÁGINA 2",
        fontsize=12,
    )
    p2.insert_text(
        fitz.Point(50, 115),
        f"Atestado Médico referente ao CPF {cpf_p2} com diagnóstico CID-10 F41.1 e e-mail joao.almeida@ufam.edu.br.",
        fontsize=11,
    )

    # Página 3
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text(
        fitz.Point(50, 70),
        "DESPACHO FINANCEIRO - DADOS BANCÁRIOS - PÁGINA 3",
        fontsize=12,
    )
    p3.insert_text(
        fitz.Point(50, 115),
        f"Favorecido CPF {cpf_p3} - Agência: 3456-7 Conta Corrente: 89012-3.",
        fontsize=11,
    )

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_first_page_first_progressive_pipeline_and_zero_pii_workflow(tmp_path: Path, monkeypatch) -> None:
    """
    Critério de Aceite da Etapa 5:
    1. Faz upload de um PDF multipágina (3 páginas);
    2. Comprova que a Página 1 fica disponível imediatamente em estado `PARTIAL_REVIEW_READY`
       e sua imagem `page_1.webp` é servida com `Cache-Control: private, max-age=3600` ANTES
       que as páginas 2 e 3 sejam processadas;
    3. Conclui o processamento das páginas 2 e 3 (`AWAITING_REVIEW`), verifica os eventos SSE
       (`verbose_step`, `page_ready`, `progress`, `analysis_complete`) e a listagem em `/occurrences`;
    4. Executa o tarjamento (`POST /api/v1/documents/{job_id}/redact`), baixa o PDF tarjado e o
       Relatório Institucional em PDF (`/download/pdf` e `/download/report`);
    5. Comprova que todos os CPFs/e-mails/CID foram fisicamente destruídos do PDF final e que
       NEM o `AuditLog`, NEM o `verbose_logs`, NEM o Relatório Institucional em PDF contêm qualquer texto literal de PII.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)

    cpf_p1 = _format_cpf("321654987")
    cpf_p2 = _format_cpf("654987321")
    cpf_p3 = _format_cpf("789123456")
    pdf_bytes = _build_multipage_ufam_pdf(cpf_p1, cpf_p2, cpf_p3)

    client = TestClient(app)

    # 1. Demonstração determinística do estágio First-Page-First antes das páginas 2 e 3:
    upload_resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=true",
        files={"file": ("processo_ufam_3p.pdf", pdf_bytes, "application/pdf")},
        headers={"Origin": "http://localhost:3000"},
    )
    assert upload_resp.status_code == 202
    assert upload_resp.headers.get("access-control-allow-origin") in ("http://localhost:3000", "*")
    upload_data = upload_resp.json()
    job_id = upload_data["job_id"]
    assert upload_data["total_pages"] == 3
    assert upload_data["document_profile"] == DocumentProfile.RESTRICTED_PROCESS.value
    assert len(upload_data.get("verbose_logs", [])) >= 1

    # Verifica eventos emitidos
    events = get_buffered_job_events(job_id)
    verbose_events = [e for e in events if e["event"] == "verbose_step"]
    assert len(verbose_events) >= 3
    page_ready_events = [e for e in events if e["event"] == "page_ready"]
    assert len(page_ready_events) >= 1
    first_page_evt = page_ready_events[0]
    assert first_page_evt["data"]["page_number"] == 1
    assert first_page_evt["data"]["status"] == JobStatus.PARTIAL_REVIEW_READY.value
    assert first_page_evt["data"]["occurrences_count"] >= 2

    # 2. Testa explicitamente a parada após a Página 1 em um job isolado
    partial_job_uuid = uuid.uuid4()
    partial_job_id = str(partial_job_uuid)
    validate_and_store_pdf(
        file_bytes=pdf_bytes,
        job_id=partial_job_uuid,
        base_temp_dir=tmp_path,
    )
    partial_state = analyze_document_task(partial_job_id, start_page=1, end_page=1)
    assert partial_state["status"] == JobStatus.PARTIAL_REVIEW_READY.value
    assert partial_state["processed_pages"] == 1
    assert "1" in partial_state["pages"]
    assert "2" not in partial_state["pages"]
    assert (tmp_path / partial_job_id / "pages" / "page_1.webp").exists()
    assert not (tmp_path / partial_job_id / "pages" / "page_2.webp").exists()

    img_p1_resp = client.get(f"/api/v1/documents/{partial_job_id}/pages/1/image")
    assert img_p1_resp.status_code == 200
    assert img_p1_resp.headers["content-type"] == "image/webp"
    assert img_p1_resp.headers["cache-control"] == "private, max-age=3600"
    assert len(img_p1_resp.content) > 500

    occ_partial_resp = client.get(f"/api/v1/documents/{partial_job_id}/occurrences")
    assert occ_partial_resp.status_code == 200
    occ_partial_json = occ_partial_resp.json()
    assert occ_partial_json["status"] == JobStatus.PARTIAL_REVIEW_READY.value
    assert occ_partial_json["processed_pages"] == 1
    assert occ_partial_json["total_pages"] == 3

    # 3. No job principal (`job_id`), verifica que todas as 3 páginas concluíram (`AWAITING_REVIEW`)
    occ_full_resp = client.get(f"/api/v1/documents/{job_id}/occurrences")
    assert occ_full_resp.status_code == 200
    occ_full_data = occ_full_resp.json()
    assert occ_full_data["status"] == JobStatus.AWAITING_REVIEW.value
    assert occ_full_data["processed_pages"] == 3
    assert len(occ_full_data["occurrences"]) >= 4
    for occ_item in occ_full_data["occurrences"]:
        assert "action_mode" in occ_item
        assert "recommended_mode" in occ_item
        assert "masked_preview" in occ_item

    sse_resp = client.get(f"/api/v1/documents/{job_id}/stream")
    assert sse_resp.status_code == 200
    assert "text/event-stream" in sse_resp.headers["content-type"]
    sse_body = sse_resp.text
    assert "event: verbose_step" in sse_body
    assert "event: page_ready" in sse_body
    assert "event: analysis_complete" in sse_body

    # 4. Dispara o tarjamento definitivo (`POST /api/v1/documents/{job_id}/redact`)
    redact_resp = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "selected_ids": None,
            "manual_boxes": [
                {
                    "page_number": 1,
                    "x0_norm": 0.08,
                    "y0_norm": 0.12,
                    "x1_norm": 0.35,
                    "y1_norm": 0.16,
                    "category": "PERSONAL_COMMON",
                }
            ],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert redact_resp.status_code == 200
    redact_data = redact_resp.json()
    assert redact_data["status"] == JobStatus.COMPLETED.value
    assert redact_data["original_file_sha256"] != redact_data["redacted_file_sha256"]

    # Verifica que todas as etapas de 1 a 7 constam no log verbose sem nenhum dado pessoal literal
    verbose_stages = {v["stage"] for v in redact_data.get("verbose_logs", [])}
    for expected_stage in (
        "ETAPA_1_INGESTAO_SEGURA",
        "ETAPA_2_EXTRACAO_OCR",
        "ETAPA_3_DETECCAO_LGPD",
        "ETAPA_4_DECISAO_CONSOLIDADA",
        "ETAPA_5_REDACAO_FISICA",
        "ETAPA_6_VALIDACAO_CIRURGICA",
        "ETAPA_7_EXPORTACAO_AUDITORIA",
    ):
        assert expected_stage in verbose_stages, f"Etapa verbose ausente: {expected_stage}"

    serialized_verbose = json.dumps(redact_data.get("verbose_logs", []), ensure_ascii=False)
    for forbidden_pii in (cpf_p1, cpf_p2, cpf_p3, "João Carlos Almeida", "F41.1", "joao.almeida@ufam.edu.br"):
        assert forbidden_pii not in serialized_verbose, (
            f"VIOLAÇÃO ZERO-PII NO VERBOSE LOG: '{forbidden_pii}' vazou no log!"
        )

    # Verifica que o AuditLog possui apenas contadores quantitativos e NENHUM dado pessoal literal
    audit_log = redact_data["audit_log"]
    assert audit_log is not None
    assert audit_log["security_validation_passed"] is True
    assert audit_log["total_redactions_applied"] >= 5
    serialized_audit = json.dumps(audit_log, ensure_ascii=False)
    for forbidden_pii in (cpf_p1, cpf_p2, cpf_p3, "João Carlos Almeida", "F41.1", "joao.almeida@ufam.edu.br"):
        assert forbidden_pii not in serialized_audit, (
            f"VIOLAÇÃO ZERO-PII: Dado pessoal '{forbidden_pii}' encontrado no AuditLog!"
        )

    completed_state = load_job_transient_state(job_id)
    assert completed_state is not None
    serialized_state = json.dumps(completed_state, ensure_ascii=False)
    for forbidden_pii in (cpf_p1, cpf_p2, cpf_p3, "F41.1", "joao.almeida@ufam.edu.br"):
        assert forbidden_pii not in serialized_state

    # 5. Faz o download do PDF tarjado e comprova a destruição física dos dados pessoais
    pdf_dl_resp = client.get(f"/api/v1/documents/{job_id}/download/pdf")
    assert pdf_dl_resp.status_code == 200
    assert pdf_dl_resp.headers["content-type"] == "application/pdf"

    redacted_doc = fitz.open(stream=pdf_dl_resp.content, filetype="pdf")
    try:
        full_redacted_text = "\n".join(page.get_text("text") for page in redacted_doc)
        for forbidden_pii in (cpf_p1, cpf_p2, cpf_p3, "F41.1", "joao.almeida@ufam.edu.br"):
            assert forbidden_pii not in full_redacted_text, (
                f"Falha no tarjamento: '{forbidden_pii}' ainda existe na camada de texto do PDF baixado!"
            )
        assert "UNIVERSIDADE FEDERAL DO AMAZONAS" in full_redacted_text
    finally:
        redacted_doc.close()

    # 6. Faz o download do Relatório Institucional de Processamento em PDF
    report_dl_resp = client.get(f"/api/v1/documents/{job_id}/download/report")
    assert report_dl_resp.status_code == 200
    assert report_dl_resp.headers["content-type"] == "application/pdf"

    report_doc = fitz.open(stream=report_dl_resp.content, filetype="pdf")
    try:
        report_text = "\n".join(page.get_text("text") for page in report_doc)
        assert "UNIVERSIDADE FEDERAL DO AMAZONAS" in report_text
        assert "TERMO DE LIMITAÇÃO TÉCNICA E JURÍDICA" in report_text
        assert "Art. 5º, XI" in report_text or "Art. 5" in report_text
        assert "TOTAL CONSOLIDADO" in report_text
        assert redact_data["original_file_sha256"] in report_text
        assert redact_data["redacted_file_sha256"] in report_text

        for forbidden_pii in (cpf_p1, cpf_p2, cpf_p3, "João Carlos Almeida", "F41.1", "joao.almeida@ufam.edu.br"):
            assert forbidden_pii not in report_text, (
                f"VIOLAÇÃO ZERO-PII NO RELATÓRIO PDF: '{forbidden_pii}' apareceu no relatório!"
            )
    finally:
        report_doc.close()

    # 7. Testa a task periódica de limpeza (`cleanup_expired_jobs_task`)
    cleanup_stats = cleanup_expired_jobs_task()
    assert "removed_job_dirs" in cleanup_stats
    assert "removed_memory_keys" in cleanup_stats


def test_hybrid_redaction_partial_mask_and_document_profile(tmp_path: Path, monkeypatch) -> None:
    """
    Valida:
    1. Classificação automática de `document_profile` (`PUBLIC_TRANSPARENCY` vs. `RESTRICTED_PROCESS`);
    2. Formatação determinística de `masked_preview` (`***.729.160-**`, `***.9203-*`, `ca***@email.com`, etc.);
    3. Aplicação híbrida (`PARTIAL_MASK` para CPF e `BLACKOUT` para e-mail/dados bancários) no endpoint `/redact`,
       comprovando que o CPF completo foi fisicamente destruído e substituído por `***.729.160-**` no PDF final.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)

    # 1. Verifica generate_masked_preview
    assert generate_masked_preview("CPF", "348.729.160-69") == "***.729.160-**"
    assert generate_masked_preview("RG", "1849203-5") == "***.9203-*"
    assert generate_masked_preview("EMAIL", "carlos.mendonca84@email.com") == "ca***@email.com"
    assert generate_masked_preview("PHONE", "(92) 99184-7721") == "(92) *****-7721"
    assert generate_masked_preview("BANK_ACCOUNT", "48921-3") == "***21-*"
    assert generate_masked_preview("PERSON_NAME", "Alexandre Magno Fontes") == "Alexandre M. F. ***"

    # 2. Verifica classify_document_profile
    assert (
        classify_document_profile("UNIVERSIDADE FEDERAL DO AMAZONAS - EDITAL Nº 015/2026 - RESULTADO FINAL E CLASSIFICAÇÃO")
        == DocumentProfile.PUBLIC_TRANSPARENCY
    )
    assert (
        classify_document_profile("DESPACHO FUNCIONAL E FINANCEIRO PROGESP - PROCESSO SEI 23105.019283/2026-41")
        == DocumentProfile.RESTRICTED_PROCESS
    )

    # 3. Cria um PDF de Edital de Transparência Pública contendo CPF e E-mail
    cpf_edital = "348.729.160-69"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(50, 70),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - EDITAL Nº 012/2026 - LISTA DE APROVADOS",
        fontsize=12,
    )
    page.insert_text(
        fitz.Point(50, 120),
        f"1. Candidato Aprovado - CPF {cpf_edital} - Contato: carlos.mendonca84@email.com",
        fontsize=11,
    )
    pdf_bytes = doc.tobytes()
    doc.close()

    client = TestClient(app)
    upload_resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("edital_ufam.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload_resp.status_code == 202
    job_id = upload_resp.json()["job_id"]
    assert upload_resp.json()["document_profile"] == DocumentProfile.PUBLIC_TRANSPARENCY.value

    occ_resp = client.get(f"/api/v1/documents/{job_id}/occurrences")
    assert occ_resp.status_code == 200
    occurrences = occ_resp.json()["occurrences"]
    assert len(occurrences) >= 2

    cpf_occ = next(o for o in occurrences if o["entity_type"] == "CPF")
    assert cpf_occ["recommended_mode"] == RedactionActionMode.PARTIAL_MASK.value
    assert cpf_occ["masked_preview"] == "***.729.160-**"

    redaction_items = []
    for o in occurrences:
        if o["entity_type"] == "CPF":
            redaction_items.append(
                {
                    "id": o["id"],
                    "action_mode": RedactionActionMode.PARTIAL_MASK.value,
                    "masked_preview": o["masked_preview"],
                }
            )
        else:
            redaction_items.append(
                {
                    "id": o["id"],
                    "action_mode": RedactionActionMode.BLACKOUT.value,
                }
            )

    redact_resp = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "redaction_items": redaction_items,
            "manual_boxes": [],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert redact_resp.status_code == 200
    redact_data = redact_resp.json()
    assert redact_data["status"] == JobStatus.COMPLETED.value
    assert redact_data["audit_log"]["security_validation_passed"] is True

    # Verifica o PDF final: o CPF original não existe mais na camada de texto, mas a máscara sim
    pdf_dl = client.get(f"/api/v1/documents/{job_id}/download/pdf")
    assert pdf_dl.status_code == 200
    redacted_doc = fitz.open(stream=pdf_dl.content, filetype="pdf")
    try:
        p0 = redacted_doc[0]
        assert len(p0.search_for(cpf_edital)) == 0
        text_out = p0.get_text("text")
        assert cpf_edital not in text_out
        assert "carlos.mendonca84@email.com" not in text_out
        assert "***.729.160-**" in text_out
    finally:
        redacted_doc.close()


def test_dynamic_regeneration_blackout_to_partial_mask_and_nocache(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Garante alternância dinâmica ilimitada no mesmo `job_id`:
    1. Executa `POST /redact` com todas as ocorrências em `BLACKOUT` (`items`);
    2. Baixa `/download/pdf` verificando headers `Cache-Control: no-store...` e ausência de máscara;
    3. Re-executa `POST /redact` no MESMO `job_id` alterando todas as ocorrências para `PARTIAL_MASK`;
    4. Baixa `/download/pdf` novamente e comprova que o SHA-256 e os bytes mudaram, as máscaras
       (`***.729.160-**`, `ca***@email.com`, `***21-*`) apareceram no PDF novo e os originais seguem destruídos;
    5. Re-executa voltando para `BLACKOUT` via `global_mode="BLACKOUT"` e valida retorno à tarja preta.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)

    cpf_val = "348.729.160-69"
    email_val = "carlos.mendonca84@email.com"
    account_val = "48921-3"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        fitz.Point(50, 70),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - PROGESP - FOLHA DE PAGAMENTO",
        fontsize=12,
    )
    page.insert_text(
        fitz.Point(50, 115),
        f"Interessado: Servidor Alexandre Magno Fontes | CPF: {cpf_val}",
        fontsize=11,
    )
    page.insert_text(
        fitz.Point(50, 145),
        f"E-mail: {email_val} | Conta Corrente: {account_val}",
        fontsize=11,
    )
    pdf_bytes = doc.tobytes()
    doc.close()

    client = TestClient(app)
    upload_resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("folha_alternancia.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload_resp.status_code == 202
    job_id = upload_resp.json()["job_id"]

    occ_resp = client.get(f"/api/v1/documents/{job_id}/occurrences")
    assert occ_resp.status_code == 200
    occurrences = occ_resp.json()["occurrences"]
    assert any(o["entity_type"] == "CPF" for o in occurrences)
    assert any(o["entity_type"] == "EMAIL" for o in occurrences)
    assert any(o["entity_type"] == "BANK_ACCOUNT" for o in occurrences)

    # 1ª Geração: BLACKOUT em todas as ocorrências usando campo `items`
    items_blackout = [
        {
            "id": o["id"],
            "action_mode": RedactionActionMode.BLACKOUT.value,
            "masked_preview": o.get("masked_preview"),
        }
        for o in occurrences
    ]
    r1 = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "items": items_blackout,
            "manual_boxes": [],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert r1.status_code == 200
    r1_json = r1.json()
    assert r1_json["status"] == JobStatus.COMPLETED.value
    sha_blackout = r1_json["redacted_file_sha256"]

    dl1 = client.get(f"/api/v1/documents/{job_id}/download/pdf?rev=1")
    assert dl1.status_code == 200
    assert "no-store" in dl1.headers.get("cache-control", "").lower()
    assert dl1.headers.get("pragma", "").lower() == "no-cache"
    doc1 = fitz.open(stream=dl1.content, filetype="pdf")
    try:
        txt1 = doc1[0].get_text("text")
        assert cpf_val not in txt1
        assert email_val not in txt1
        assert account_val not in txt1
        assert "***.729.160-**" not in txt1
    finally:
        doc1.close()

    # 2ª Geração no MESMO job_id: PARTIAL_MASK em todas as ocorrências
    items_partial = [
        {
            "id": o["id"],
            "action_mode": RedactionActionMode.PARTIAL_MASK.value,
            "masked_preview": o.get("masked_preview"),
        }
        for o in occurrences
    ]
    r2 = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "items": items_partial,
            "manual_boxes": [],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert r2.status_code == 200
    r2_json = r2.json()
    assert r2_json["status"] == JobStatus.COMPLETED.value
    assert r2_json["audit_log"]["security_validation_passed"] is True
    sha_partial = r2_json["redacted_file_sha256"]
    assert sha_partial != sha_blackout

    dl2 = client.get(f"/api/v1/documents/{job_id}/download/pdf?rev=2")
    assert dl2.status_code == 200
    assert "no-store" in dl2.headers.get("cache-control", "").lower()
    assert dl2.content != dl1.content
    doc2 = fitz.open(stream=dl2.content, filetype="pdf")
    try:
        txt2 = doc2[0].get_text("text")
        assert cpf_val not in txt2
        assert email_val not in txt2
        assert account_val not in txt2
        assert "***.729.160-**" in txt2
        assert "ca***@email.com" in txt2
        assert "***21-*" in txt2
    finally:
        doc2.close()

    # Verifica também que o relatório possui cache-control anti-cache
    rep2 = client.get(f"/api/v1/documents/{job_id}/download/report?rev=2")
    assert rep2.status_code == 200
    assert "no-store" in rep2.headers.get("cache-control", "").lower()

    # 3ª Geração no MESMO job_id: retorna a BLACKOUT com `global_mode`
    r3 = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "global_mode": RedactionActionMode.BLACKOUT.value,
            "manual_boxes": [],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert r3.status_code == 200
    r3_json = r3.json()
    assert r3_json["redacted_file_sha256"] != sha_partial

    dl3 = client.get(f"/api/v1/documents/{job_id}/download/pdf?rev=3")
    assert dl3.status_code == 200
    doc3 = fitz.open(stream=dl3.content, filetype="pdf")
    try:
        txt3 = doc3[0].get_text("text")
        assert cpf_val not in txt3
        assert "***.729.160-**" not in txt3
    finally:
        doc3.close()


def test_cors_headers_on_preflight_and_unexpected_500_errors(monkeypatch) -> None:
    """
    Garante que o backend responda com `Access-Control-Allow-Origin` tanto no preflight OPTIONS
    quanto em respostas 500 capturadas pelo global exception handler.
    """
    client = TestClient(app, raise_server_exceptions=False)

    preflight = client.options(
        "/api/v1/documents/upload",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code in (200, 204)
    assert preflight.headers.get("access-control-allow-origin") in ("http://localhost:3000", "*")

    def _boom(*args, **kwargs):
        raise RuntimeError("Falha simulada para validar CORS no 500")

    monkeypatch.setattr("app.api.v1.endpoints.documents.validate_and_store_pdf", _boom)

    err_resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=true",
        files={"file": ("erro_simulado.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        headers={"Origin": "http://localhost:3000"},
    )
    assert err_resp.status_code == 500
    assert err_resp.headers.get("access-control-allow-origin") in ("http://localhost:3000", "*")
    err_json = err_resp.json()
    assert err_json["error"]["code"] == "INTERNAL_SERVER_ERROR"
