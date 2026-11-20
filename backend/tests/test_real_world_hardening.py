"""
Testes de Endurecimento para Documentos Reais (`test_real_world_hardening.py`).

Valida com dados 100% sintéticos (Zero-PII) as 10 correções estruturais oriundas
de auditorias de processos judiciais, cédulas de RG/CPF, faturas bancárias e
PDFs gerados por `Microsoft: Print To PDF` (texto vetorizado):
1. Detecção de páginas com texto vetorizado (0 palavras nativas + desenhos vetoriais -> SCANNED);
2. Proteção de brasões/cabeçalhos institucionais contra falso positivo visual;
3. Filtro anti-explosão de molduras 3x4 sobre células de tabela com texto impresso;
4. Âncoras biométricas de RG (`POLEGAR DIREITO`, foto 3x4 e assinatura);
5. Padrões resilientes a colagem OCR (RG+órgão, CEP colado/sem hífen, e-mail colado,
   cartão de crédito, telefone truncado, matrícula com DV, nascimento 2 dígitos e multi-coluna);
6. Bloqueio de invasão de coluna financeira em regex de endereço;
7. Supressão de falsos positivos de NLP (frases minúsculas, termos PROJUDI/RG/fatura),
   SAC bancário (`Deficiência Auditiva`), valores de ação cível (`REMUNERATION`) e endereços corporativos;
8. Propagação cruzada de nomes tolerante a espaços colados e 1 erro de OCR com `group_id` unificado;
9. Proteção Fail-Closed no envio antecipado (`First-Page-First`) impedindo que páginas ainda não
   revisadas sejam marcadas como `KEEP`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from app.models.enums import DataCategory, DocumentProfile, RedactionActionMode
from app.services.pdf_extractor.extractor import (
    PageContentType,
    PageExtractionResult,
    UnifiedLine,
    UnifiedToken,
    VisualElementKind,
    _detect_rg_card_visuals,
    classify_page_type,
)
from app.services.pii_detector.context_filter import should_suppress_detection
from app.services.pii_detector.entity_mapper import (
    build_person_canonical_map,
    detect_pii_in_pages,
)
from app.services.pii_detector.nlp_analyzer import (
    RawTextSpanDetection,
    analyze_pages_batch,
    propagate_person_names_across_batch,
)
from app.services.redactor.pdf_redactor import generate_masked_preview
from app.worker.tasks import apply_redaction_task, store_job_transient_state


def _make_synthetic_page(page_number: int, text: str, *, page_type: PageContentType = PageContentType.SCANNED) -> PageExtractionResult:
    """Constrói uma PageExtractionResult sintética com tokens posicionados para testes determinísticos."""
    lines_raw = text.splitlines()
    lines: list[UnifiedLine] = []
    tokens: list[UnifiedToken] = []
    y_cursor = 0.08
    w_no = 0
    for l_idx, ln_txt in enumerate(lines_raw):
        ln_clean = ln_txt.strip()
        if not ln_clean:
            y_cursor += 0.02
            continue
        x_cursor = 0.08
        words = ln_clean.split()
        for w in words:
            w_width = min(0.25, max(0.02, len(w) * 0.009))
            tokens.append(
                UnifiedToken(
                    text=w,
                    page_number=page_number,
                    x0_norm=round(x_cursor, 6),
                    y0_norm=round(y_cursor, 6),
                    x1_norm=round(min(0.95, x_cursor + w_width), 6),
                    y1_norm=round(min(0.90, y_cursor + 0.014), 6),
                    confidence=0.95,
                    source="OCR" if page_type == PageContentType.SCANNED else "NATIVE",
                    block_no=0,
                    line_no=l_idx,
                    word_no=w_no,
                )
            )
            x_cursor = min(0.92, x_cursor + w_width + 0.008)
            w_no += 1
        lines.append(
            UnifiedLine(
                text=ln_clean,
                page_number=page_number,
                x0_norm=0.08,
                y0_norm=round(y_cursor, 6),
                x1_norm=round(min(0.95, x_cursor), 6),
                y1_norm=round(min(0.90, y_cursor + 0.014), 6),
                confidence=0.95,
                source="OCR" if page_type == PageContentType.SCANNED else "NATIVE",
            )
        )
        y_cursor += 0.022

    return PageExtractionResult(
        page_number=page_number,
        page_type=page_type,
        page_width_pts=595.0,
        page_height_pts=842.0,
        tokens=tokens,
        lines=lines,
        ocr_executed=(page_type == PageContentType.SCANNED),
    )


def test_classify_page_type_detects_vector_outlined_text_pdf() -> None:
    """PDFs impressos como texto vetorizado (0 palavras, 0 imagens, >= 15 desenhos) devem ir para OCR."""
    assert classify_page_type(0, [], vector_drawing_count=151) == PageContentType.SCANNED
    assert classify_page_type(0, [], vector_drawing_count=20) == PageContentType.SCANNED
    # Página genuinamente em branco ou com texto nativo permanece TEXTUAL (Zero-Waste OCR)
    assert classify_page_type(0, [], vector_drawing_count=0) == PageContentType.TEXTUAL
    assert classify_page_type(25, [], vector_drawing_count=500) == PageContentType.TEXTUAL


def test_rg_card_visual_anchors_detect_thumbprint_photo_and_signature() -> None:
    """Frente de RG com POLEGAR DIREITO + CARTEIRA DE IDENTIDADE deve detectar biometria, foto e assinatura."""
    lines = [
        UnifiedLine("REPUBLICA FEDERATIVA DO BRASIL", 1, 0.10, 0.25, 0.16, 0.70, 0.96, "OCR"),
        UnifiedLine("CARTEIRA DE IDENTIDADE", 1, 0.80, 0.35, 0.86, 0.65, 0.96, "OCR"),
        UnifiedLine("POLEGAR DIREITO", 1, 0.40, 0.57, 0.59, 0.59, 0.95, "OCR"),
    ]
    visuals = _detect_rg_card_visuals(1, lines, [])
    methods = {v.detection_method for v in visuals}
    assert "OCR_ANCHOR_BIOMETRIC_THUMBPRINT" in methods
    assert "OCR_ANCHOR_RG_PORTRAIT" in methods
    assert "OCR_ANCHOR_RG_SIGNATURE" in methods
    assert any(v.kind == VisualElementKind.FACIAL_PHOTO for v in visuals)
    assert any(v.kind == VisualElementKind.VISUAL_SIGNATURE for v in visuals)


def test_glued_ocr_regex_patterns_and_credit_card_detection() -> None:
    """Valida RG colado, CEP colado/sem hífen, e-mail colado, cartão de crédito, matrícula com DV e WhatsApp truncado."""
    sample_text = (
        "REQUERENTE: CARLOS EDUARDO MENDONCA, RG:1234567SSP/AM, residente e domiciliado na "
        "Rua das Flores, n 120, CEP69000-123, enderecoeletronicoteste.usuario@exemplo.org.br.\n"
        "Fatura cartao: 4111 XXXX XXXX 1111\n"
        "WhatsApp cabecalho: +55 92 981...\n"
        "Matricula: 2024567-1 CEP:69000456\n"
        "Data do Nascimento\n"
        "123456789-00\n"
        "05/03/85\n"
    )
    res = analyze_pages_batch([(1, sample_text, None)])[1]
    by_type: dict[str, list[str]] = {}
    for det in res:
        by_type.setdefault(det.entity_type, []).append(det.matched_text)

    assert any("1234567" in v for v in by_type.get("RG", [])), by_type
    assert any("69000-123" in v for v in by_type.get("CEP", [])), by_type
    assert any("69000456" in v for v in by_type.get("CEP", [])), by_type
    assert by_type.get("EMAIL") == ["teste.usuario@exemplo.org.br"], by_type
    assert any("4111 XXXX XXXX 1111" in v for v in by_type.get("CREDIT_CARD", [])), by_type
    assert any("+55 92 981..." in v for v in by_type.get("PHONE", [])), by_type
    assert any("2024567-1" in v for v in by_type.get("MATRICULA_UFAM", [])), by_type
    assert any("05/03/85" in v for v in by_type.get("BIRTH_DATE", [])), by_type
    assert generate_masked_preview("CREDIT_CARD", "4111 XXXX XXXX 1111") == "**** **** **** 1111"


def test_rg_back_card_standalone_number_filiacao_and_birth_date() -> None:
    """Verso de RG: captura RG `0123456-7`, mãe após `FILIACAO`, nascimento `05/03/1985`, sem pegar `15/06/2019`."""
    rg_text = (
        "VALIDA EM TODO O TERRITORIO NACIONAL\n"
        "0123456-7\n"
        "15/06/2019\n"
        "REGISTRO GERAL\n"
        "DATA DE EXPEDICAO\n"
        "CARLOS EDUARDO MENDONCA\n"
        "NOME\n"
        "JOAO MENDONCA\n"
        "FILIACAO\n"
        "MARIA HELENA MENDONCA\n"
        "05/03/1985\n"
        "MANAUS-AM\n"
        "NATURALIDADE\n"
    )
    res = analyze_pages_batch([(1, rg_text, None)])[1]
    by_type: dict[str, list[str]] = {}
    for det in res:
        by_type.setdefault(det.entity_type, []).append(det.matched_text)

    assert any("0123456-7" in v for v in by_type.get("RG", [])), by_type
    assert any("MARIA HELENA MENDONCA" in v for v in by_type.get("FILIACAO", [])), by_type
    assert any("05/03/1985" in v for v in by_type.get("BIRTH_DATE", [])), by_type
    assert not any("15/06/2019" in v for v in by_type.get("BIRTH_DATE", [])), by_type
    # Nenhum rótulo estrutural do RG pode vazar como PERSON_NAME
    all_persons = " | ".join(by_type.get("PERSON_NAME", []))
    for forbidden in ("NOME", "MANAUS-AM", "NATURALIDADE", "FILIACAO"):
        assert forbidden not in all_persons.upper(), all_persons


def test_address_does_not_bleed_into_invoice_numeric_column() -> None:
    """Endereço na coluna esquerda de fatura não pode absorver parcelas/valores da coluna direita."""
    invoice_text = (
        "RUA EXEMPLO SINTETICO 120 QD 04\n"
        "510,20 +12×115,40\n"
        "Pagamento Minimo R$ 245,00\n"
    )
    res = analyze_pages_batch([(1, invoice_text, None)])[1]
    addresses = [d.matched_text for d in res if d.entity_type == "ADDRESS"]
    assert addresses, res
    assert all("510,20" not in a and "115" not in a for a in addresses), addresses


def test_anti_false_positive_suppression_bank_sac_civil_amounts_and_spacy_noise() -> None:
    """Suprime SAC Deficiência Auditiva, valores de ação cível, ruído NLP e endereços corporativos/judiciais."""
    # 1. SAC bancário Deficiência Auditiva -> suprimido
    sac_text = "Banco Bradesco S/A - SAC Deficiencia Auditiva ou de Fala: 0800 722 0099"
    idx_def = sac_text.index("Deficiencia Auditiva")
    assert should_suppress_detection("HEALTH_DATA", "Deficiencia Auditiva", sac_text, idx_def, idx_def + 20) is True

    # 2. Ação cível R$ 900,00 / R$ 15.900,00 -> suprimido; Remuneração UFAM -> preservado
    civil_text = "Da-se a causa o valor de R$ 15.900,00 a titulo de restituicao de R$ 900,00 e danos morais."
    idx_900 = civil_text.index("R$ 900,00")
    assert should_suppress_detection("REMUNERATION", "R$ 900,00", civil_text, idx_900, idx_900 + 9) is True

    payroll_text = "Folha de Pagamento UFAM - Remuneracao Bruta: R$ 14.520,80"
    idx_sal = payroll_text.index("R$ 14.520,80")
    assert should_suppress_detection("REMUNERATION", "R$ 14.520,80", payroll_text, idx_sal, idx_sal + 12) is False

    # 3. Ruído de PERSON_NAME (minúsculas, colagens OCR, termos PROJUDI/fatura)
    for noise in (
        "pessoa juridica de direito",
        "opagamentotenha ocorridodeforma",
        "JUNTADA DEPETICAO DEINICIAL.Arq",
        "Vossa Excelencia",
        "POLEGAR DIREITO",
        "MANAUS-AM",
        "Outubro/2024 CETAnual306.88",
        "3004-8858 Demais regioes:0800",
        "VISA PLATINUM",
        "Nucleo Cidade de Deus",
    ):
        assert should_suppress_detection("PERSON_NAME", noise, noise, 0, len(noise)) is True, noise

    # 4. Endereço/CEP corporativo ou de tribunal -> suprimido
    corp_text = "Banco Bradesco S/A CNPJ: 60.746.948/0001-12 Nucleo Cidade de Deus CEP 06029-900"
    idx_cep = corp_text.index("06029-900")
    assert should_suppress_detection("CEP", "06029-900", corp_text, idx_cep, idx_cep + 9) is True


def test_fuzzy_and_glued_cross_page_person_propagation_and_group_id() -> None:
    """Propagação entre páginas captura variantes com espaços colados e 1 erro de OCR no mesmo group_id."""
    p1 = _make_synthetic_page(1, "REQUERENTE: CARLOS EDUARDO MENDONCA SILVA, brasileiro.")
    p2 = _make_synthetic_page(2, "Fatura titular: CARLOS EDUARDO MENDDNCA SILVA")
    p3 = _make_synthetic_page(3, "Assinatura digital: Carlos EduardoMendoncaSilva")

    entities = detect_pii_in_pages([p1, p2, p3], session_salt="test_fuzzy_prop")
    persons_by_page = {
        p: [e for e in entities if e.page_number == p and e.entity_type == "PERSON_NAME"]
        for p in (1, 2, 3)
    }
    assert persons_by_page[1], entities
    assert persons_by_page[2], entities
    assert persons_by_page[3], entities

    # Todas as 3 variantes devem compartilhar o mesmo group_id canônico
    gids = {persons_by_page[p][0].group_id for p in (1, 2, 3)}
    assert len(gids) == 1, gids

    cmap = build_person_canonical_map(["CARLOS EDUARDO MENDONCA SILVA", "CARLOS EDUARDO MENDDNCA SILVA", "Carlos EduardoMendoncaSilva"])
    assert cmap.get("CARLOS EDUARDO MENDDNCA SILVA") == "CARLOS EDUARDO MENDONCA SILVA"
    assert cmap.get("CARLOS EDUARDOMENDONCASILVA") == "CARLOS EDUARDO MENDONCA SILVA"


def test_early_submit_unreviewed_pages_stay_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Se o cliente enviar `selected_ids` contendo apenas ocorrências da Página 1 enquanto páginas 2..N
    foram analisadas no catch-up (`unreviewed_pages=[2]`), as ocorrências da Página 2 NÃO podem
    virar `KEEP` — devem ser tarjadas conforme `recommended_mode`.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)

    job_id = str(uuid.uuid4())
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    orig_pdf = job_dir / "original.pdf"

    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 100), "CPF Titular Pagina 1: 111.222.333-96", fontsize=12)
    p2 = doc.new_page()
    p2.insert_text((72, 100), "CPF Titular Pagina 2: 987.654.321-00", fontsize=12)
    doc.save(str(orig_pdf))
    doc.close()

    occ_p1 = {
        "id": "occ_p1",
        "page_number": 1,
        "entity_type": "CPF",
        "category": DataCategory.PERSONAL_COMMON.value,
        "x0_norm": 0.30,
        "y0_norm": 0.10,
        "x1_norm": 0.55,
        "y1_norm": 0.13,
        "confidence_score": 0.99,
        "confidence_level": "HIGH",
        "group_id": "g1",
        "detector_source": "REGEX",
        "selected": True,
        "action_mode": RedactionActionMode.BLACKOUT.value,
        "recommended_mode": RedactionActionMode.BLACKOUT.value,
        "masked_preview": "***.222.333-**",
        "preview_text": "111.222.333-96",
        "is_continuation": False,
    }
    occ_p2 = {
        "id": "occ_p2",
        "page_number": 2,
        "entity_type": "CPF",
        "category": DataCategory.PERSONAL_COMMON.value,
        "x0_norm": 0.30,
        "y0_norm": 0.10,
        "x1_norm": 0.55,
        "y1_norm": 0.13,
        "confidence_score": 0.99,
        "confidence_level": "HIGH",
        "group_id": "g2",
        "detector_source": "REGEX",
        "selected": True,
        "action_mode": RedactionActionMode.BLACKOUT.value,
        "recommended_mode": RedactionActionMode.BLACKOUT.value,
        "masked_preview": "***.654.321-**",
        "preview_text": "987.654.321-00",
        "is_continuation": False,
    }

    state = {
        "job_id": job_id,
        "status": "AWAITING_REVIEW",
        "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
        "original_file_sha256": "a" * 64,
        "total_pages": 2,
        "processed_pages": 2,
        "pages": {
            "1": {"page_number": 1, "occurrences": [dict(occ_p1)]},
            "2": {"page_number": 2, "occurrences": [dict(occ_p2)]},
        },
        "occurrences": [occ_p1, occ_p2],
        "verbose_logs": [],
    }
    store_job_transient_state(job_id, state)

    updated = apply_redaction_task(
        job_id=job_id,
        selected_ids=["occ_p1"],  # Apenas página 1 foi enviada pelo frontend
        manual_boxes=[],
        redaction_items=[{"id": "occ_p1", "action_mode": "BLACKOUT"}],
        unreviewed_pages=[2],  # Página 2 ainda não havia sido exibida ao revisor
    )

    modes_by_id = {o["id"]: o["action_mode"] for o in updated["occurrences"]}
    assert modes_by_id["occ_p1"] == RedactionActionMode.BLACKOUT.value
    assert modes_by_id["occ_p2"] == RedactionActionMode.BLACKOUT.value, modes_by_id
    assert updated["audit_log"]["total_redactions_applied"] == 2


def test_sse_stream_replays_cross_process_pages_and_analysis_complete() -> None:
    """
    Regressão do travamento 22/25:
    Quando o buffer em memória do FastAPI só possui a Página 1 (First-Page-First),
    mas o worker Celery concluiu as páginas 2..25 em outro processo (estado AWAITING_REVIEW),
    uma reconexão SSE deve reenviar as páginas faltantes sem duplicar a 1 e emitir `analysis_complete`.
    """
    import asyncio
    import json
    from uuid import UUID, uuid4

    from app.api.v1.endpoints.documents import stream_document_events
    from app.worker.tasks import publish_job_event, store_job_transient_state

    job_uuid = uuid4()
    job_id = str(job_uuid)

    # Simula o buffer local da API contendo apenas o evento page_ready da página 1
    publish_job_event(
        job_id,
        "page_ready",
        {
            "page_number": 1,
            "total_pages": 25,
            "status": "PARTIAL_REVIEW_READY",
            "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
            "image_url": f"/api/v1/documents/{job_id}/pages/1/image.webp",
            "occurrences_count": 0,
            "occurrences": [],
        },
    )

    # Simula o worker Celery tendo concluído todas as 25 páginas no estado compartilhado
    pages_map = {
        str(p): {
            "page_number": p,
            "page_type": "SCANNED",
            "width_px": 1240,
            "height_px": 1754,
            "image_url": f"/api/v1/documents/{job_id}/pages/{p}/image.webp",
            "ocr_executed": True,
            "processing_time_ms": 100.0,
            "occurrences": [],
        }
        for p in range(1, 26)
    }
    store_job_transient_state(
        job_id,
        {
            "job_id": job_id,
            "status": "AWAITING_REVIEW",
            "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
            "original_file_sha256": "b" * 64,
            "total_pages": 25,
            "processed_pages": 25,
            "pages": pages_map,
            "occurrences": [],
            "verbose_logs": [],
        },
    )

    async def _collect_events() -> list[tuple[str, dict]]:
        response = await stream_document_events(UUID(job_id))
        collected: list[tuple[str, dict]] = []
        async for chunk in response.body_iterator:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            if text.startswith(":"):
                continue
            ev_name = ""
            ev_data = {}
            for line in text.strip().splitlines():
                if line.startswith("event:"):
                    ev_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    ev_data = json.loads(line.split(":", 1)[1].strip())
            if ev_name:
                collected.append((ev_name, ev_data))
        return collected

    events = asyncio.run(_collect_events())
    page_ready_nums = [
        payload["data"]["page_number"]
        for name, payload in events
        if name == "page_ready"
    ]
    assert page_ready_nums == list(range(1, 26)), page_ready_nums
    assert events[-1][0] == "analysis_complete", events[-1]
    assert events[-1][1]["data"]["total_pages"] == 25


def _build_synthetic_sei_despacho_pre_signature_pdf() -> bytes:
    """
    Gera PDF sintético (100% fictício, Zero-PII) no formato real de Despacho do SEI/UFAM
    ANTES da assinatura eletrônica e geração de QR Code.
    """
    doc = fitz.open()
    try:
        # Página 1 — Timbre, Interessado, Corpo com CPF, Assinatura pré-eletrônica e Rodapé SEI multi-linha
        p1 = doc.new_page(width=595, height=842)
        p1.insert_text(fitz.Point(225, 118), "Ministério da Educação", fontsize=11)
        p1.insert_text(fitz.Point(198, 132), "Universidade Federal do Amazonas", fontsize=11)
        p1.insert_text(fitz.Point(188, 146), "Escritório de Proteção de Dados Pessoais", fontsize=10)

        p1.insert_text(fitz.Point(42, 185), "Processo nº: 23105.000123/2026-01", fontsize=10)
        p1.insert_text(fitz.Point(42, 200), "Interessado: Mariana Costa Fictícia", fontsize=10)
        p1.insert_text(fitz.Point(268, 238), "DESPACHO", fontsize=11)
        p1.insert_text(
            fitz.Point(42, 280),
            "Em atendimento à solicitação da interessada Mariana Costa Fictícia, portadora do CPF 528.419.630-98,",
            fontsize=10,
        )
        p1.insert_text(
            fitz.Point(42, 296),
            "encaminha-se o presente processo para análise e providências administrativas cabíveis.",
            fontsize=10,
        )

        # Bloco final de despacho pré-assinatura (centralizado)
        p1.insert_text(fitz.Point(220, 555), "Manaus, 12 de março de 2026.", fontsize=10)
        p1.insert_text(fitz.Point(195, 595), "JOÃO ALBERTO NOGUEIRA FICTÍCIO", fontsize=10)
        p1.insert_text(fitz.Point(175, 615), "Encarregado pelo Tratamento de Dados Pessoais", fontsize=10)
        p1.insert_text(fitz.Point(230, 629), "Portaria 101/2026 (9001234)", fontsize=10)

        # Rodapé padrão SEI/UFAM começando em y≈0.885 (acima do corte legado 0.93)
        p1.insert_text(
            fitz.Point(42, 755),
            "Avenida General Rodrigo Octávio, 6200 - Bairro Coroado I Campus Universitário Senador Arthur Virgílio",
            fontsize=8.5,
        )
        p1.insert_text(fitz.Point(42, 769), "Filho - Telefone: (92) 3305-1181", fontsize=8.5)
        p1.insert_text(
            fitz.Point(42, 783),
            "CEP 69080-900, Manaus/AM, escritorio@ufam.edu.br\u200b",
            fontsize=8.5,
        )
        p1.insert_text(
            fitz.Point(42, 830),
            "Despacho 9001234 SEI 23105.000123/2026-01 / pg. 1",
            fontsize=8,
        )

        # Página 2 — Cabeçalho de continuação SEI e rodapé de paginação
        p2 = doc.new_page(width=595, height=842)
        p2.insert_text(fitz.Point(42, 40), "Referência: Processo nº 23105.000123/2026-01", fontsize=8.5)
        p2.insert_text(fitz.Point(480, 49), "SEI nº 9001234", fontsize=8.5)
        p2.insert_text(
            fitz.Point(42, 830),
            "Despacho 9001234 SEI 23105.000123/2026-01 / pg. 2",
            fontsize=8,
        )
        return doc.tobytes()
    finally:
        doc.close()


def test_sei_despacho_pre_signature_header_footer_and_closing_ignored_by_default() -> None:
    """
    Valida que, com `ignore_sei_standard_blocks=True` (padrão), o timbre do cabeçalho SEI,
    o bloco de assinatura final de despacho pré-assinatura eletrônica e o rodapé institucional
    multi-linha do SEI/UFAM são ignorados — preservando 100% dos dados pessoais da interessada
    no corpo (`Interessado:` e `CPF`) — e que desativar a regra (`False`) volta a detectá-los.
    """
    from app.services.pdf_extractor.extractor import extract_single_page
    from app.services.pii_detector.entity_mapper import detect_pii_in_pages
    from app.services.pii_detector.sei_boilerplate import analyze_sei_boilerplate_zones

    pdf_bytes = _build_synthetic_sei_despacho_pre_signature_pdf()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = [
            extract_single_page(doc[i], job_id="sei_pre_sig", page_number=i + 1, render_webp=False)
            for i in range(len(doc))
        ]
    finally:
        doc.close()

    z1 = analyze_sei_boilerplate_zones(pages[0])
    z2 = analyze_sei_boilerplate_zones(pages[1])
    assert z1.has_sei_header and z1.has_sei_footer and z1.has_despacho_signature
    assert z2.has_sei_header and z2.has_sei_footer

    # Regra ATIVA (padrão)
    ents_on = detect_pii_in_pages(pages, session_salt="sei_pre_on", ignore_sei_standard_blocks=True)
    types_on = {e.entity_type for e in ents_on}
    texts_on = [e.matched_text.upper() for e in ents_on]

    assert "CPF" in types_on
    assert any("MARIANA COSTA" in t for t in texts_on), texts_on
    assert not any("JOÃO ALBERTO" in t or "JOAO ALBERTO" in t for t in texts_on), texts_on
    assert not any("ENCARREGADO" in t for t in texts_on), texts_on
    assert not any(e.entity_type == "PHONE" for e in ents_on), ents_on
    assert not any(e.entity_type == "EMAIL" for e in ents_on), ents_on
    assert not any(e.entity_type == "CEP" for e in ents_on), ents_on

    # Regra DESATIVADA (`False`) volta a detectar signatário, telefone e e-mail do rodapé
    ents_off = detect_pii_in_pages(pages, session_salt="sei_pre_off", ignore_sei_standard_blocks=False)
    types_off = {e.entity_type for e in ents_off}
    texts_off = [e.matched_text.upper() for e in ents_off]
    assert any("JOÃO ALBERTO" in t or "JOAO ALBERTO" in t for t in texts_off), texts_off
    assert "PHONE" in types_off
    assert "EMAIL" in types_off


def test_sei_despacho_post_signature_electronic_block_qrcode_and_lgpd_failsafe() -> None:
    """
    Valida o despacho SEI pós-assinatura eletrônica (Decreto nº 8.539/2015, bloco de autenticidade,
    CRC, Criado por e QR Code visual) e garante o Fail-Safe LGPD: CPF ou e-mail pessoal não
    institucional jamais são suprimidos mesmo se próximos ao fechamento.
    """
    from app.services.pdf_extractor.extractor import (
        DetectedVisualElement,
        VisualElementKind,
        extract_single_page,
    )
    from app.services.pii_detector.entity_mapper import detect_pii_in_pages

    doc = fitz.open()
    try:
        p = doc.new_page(width=595, height=842)
        p.insert_text(fitz.Point(42, 220), "Atenciosamente,", fontsize=10)
        p.insert_text(fitz.Point(42, 245), "CARLOS EDUARDO MENDES FICTÍCIO", fontsize=10)
        p.insert_text(fitz.Point(42, 260), "Pró-Reitor de Administração", fontsize=10)
        p.insert_text(fitz.Point(42, 275), "Contato pessoal vazado: fulano.pessoal@gmail.com | CPF 528.419.630-98", fontsize=9)

        # Bloco eletrônico SEI + autenticidade
        p.insert_text(
            fitz.Point(42, 640),
            "Documento assinado eletronicamente por Carlos Eduardo Mendes Fictício, Pró-Reitor,",
            fontsize=9,
        )
        p.insert_text(
            fitz.Point(42, 654),
            "em 12/03/2026, às 14:22, conforme horário oficial de Brasília, com fundamento no art. 6º,",
            fontsize=9,
        )
        p.insert_text(
            fitz.Point(42, 668),
            "§ 1º, do Decreto nº 8.539, de 8 de outubro de 2015.",
            fontsize=9,
        )
        p.insert_text(
            fitz.Point(120, 700),
            "A autenticidade deste documento pode ser conferida no site https://sei.ufam.edu.br/sei/controlador_externo.php,",
            fontsize=8.5,
        )
        p.insert_text(
            fitz.Point(120, 714),
            "informando o código verificador 9001234 e o código CRC ABCD1234.",
            fontsize=8.5,
        )
        p.insert_text(
            fitz.Point(42, 742),
            "Criado por usuario_ficticio, versão 2 por usuario_ficticio em 12/03/2026 14:20:00.",
            fontsize=8,
        )
        page_ext = extract_single_page(p, job_id="sei_post_sig", page_number=1, render_webp=False)
    finally:
        doc.close()

    # Simula QR Code de autenticidade detectado ao lado do texto de conferência
    page_ext.visual_elements.append(
        DetectedVisualElement(
            page_number=1,
            kind=VisualElementKind.VISUAL_SIGNATURE,
            x0_norm=0.06,
            y0_norm=0.81,
            x1_norm=0.18,
            y1_norm=0.88,
            confidence=0.90,
            detection_method="QR_SIMULATED",
        )
    )

    ents = detect_pii_in_pages([page_ext], session_salt="sei_post_sig", ignore_sei_standard_blocks=True)
    texts_up = [e.matched_text.upper() for e in ents]
    types = {e.entity_type for e in ents}

    assert not any("CARLOS EDUARDO" in t for t in texts_up), texts_up
    assert "VISUAL_SIGNATURE" not in types, ents
    # Fail-Safe LGPD: CPF e e-mail pessoal não institucional permanecem detectados
    assert "CPF" in types, types
    assert any(e.entity_type == "EMAIL" and "gmail.com" in e.matched_text.lower() for e in ents), ents


def test_upload_endpoint_respects_ignore_sei_standard_blocks_form_flag() -> None:
    """Valida o parâmetro configurável `ignore_sei_standard_blocks` no endpoint `/upload`."""
    from fastapi.testclient import TestClient
    from app.main import app

    pdf_bytes = _build_synthetic_sei_despacho_pre_signature_pdf()
    client = TestClient(app)

    # 1. Padrão (True): ignora rodapé/cabeçalho/assinatura SEI
    res_on = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("despacho_sei.pdf", pdf_bytes, "application/pdf")},
        data={"ignore_sei_standard_blocks": "true"},
    )
    assert res_on.status_code == 202, res_on.text
    body_on = res_on.json()
    assert body_on["ignore_sei_standard_blocks"] is True
    occs_on = client.get(f"/api/v1/documents/{body_on['job_id']}/occurrences").json()["occurrences"]
    assert not any(o["entity_type"] in ("PHONE", "EMAIL") for o in occs_on), occs_on

    # 2. Desativado (False): inclui telefone e e-mail do rodapé SEI
    res_off = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("despacho_sei.pdf", pdf_bytes, "application/pdf")},
        data={"ignore_sei_standard_blocks": "false"},
    )
    assert res_off.status_code == 202, res_off.text
    body_off = res_off.json()
    assert body_off["ignore_sei_standard_blocks"] is False
    occs_off = client.get(f"/api/v1/documents/{body_off['job_id']}/occurrences").json()["occurrences"]
    assert any(o["entity_type"] == "PHONE" for o in occs_off), occs_off
    assert any(o["entity_type"] == "EMAIL" for o in occs_off), occs_off
