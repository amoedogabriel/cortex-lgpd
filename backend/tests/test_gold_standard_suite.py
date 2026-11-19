"""
Suíte Automatizada "Padrão Ouro" (`test_gold_standard_suite.py`).

Valida de ponta a ponta (sem mocks) os 4 Cenários Obrigatórios de Aceite:
1. Cenário 1: Tabelas estreitas multi-linha (estilo PROEG) + Título de Eleitor (`0482 9103 2216`),
   preservação estrita de rótulos/CNPJ UFAM/SEI Geral/rodapés e destruição física 100% em `BLACKOUT`;
2. Cenário 2: PDF 100% escaneado a 200 DPI (zero camada nativa de texto), OCR ONNX + queima de pixels
   de nomes CAIXA ALTA/abreviados, dependente, filiação, endereço, CNH, PIS/PASEP, CID-10, sindicato,
   biometria e foto 3x4 escaneada, preservando cabeçalho digitalizado e palavra adjacente `"necessita"`;
3. Cenário 3: Fluxo API no mesmo `job_id` (`BLACKOUT` -> re-geração dinâmica para `PARTIAL_MASK` em CPF,
   RG e Título de Eleitor `**** 9103 22**`), comprovando alteração de SHA-256, destruição dos originais
   e fonte ampliada legível (`span["size"] >= 10.0pt`);
4. Cenário 4: Auditoria Zero-PII do `report.pdf` (nenhum dado pessoal literal, inclusive Título de Eleitor)
   e soma matemática exata na linha `TOTAL CONSOLIDADO` em todas as colunas quantitativas.
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import pytest
from fastapi.testclient import TestClient
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.core.ml_models import ml_registry
from app.main import app
from app.models.enums import DataCategory, DocumentProfile, JobStatus, RedactionActionMode
from app.services.pdf_extractor.extractor import extract_single_page
from app.services.pii_detector.entity_mapper import detect_pii_in_pages
from app.services.redactor.pdf_redactor import (
    RedactionBoxSpec,
    apply_true_redactions_to_pdf,
    generate_masked_preview,
)
from app.services.redactor.structural_sanitizer import sanitize_pdf_structure
from app.services.security_validator.post_redaction_validator import (
    validate_redacted_pdf_surgically,
)


@pytest.fixture(scope="module", autouse=True)
def warmup_models_once() -> None:
    """Pré-aquece RapidOCR e spaCy em memória antes da suíte Padrão Ouro."""
    ml_registry.warmup(load_ocr=True, load_spacy=True)


def _build_gold_scenario01_proeg_pdf(dest_path: Path) -> None:
    """Constrói PDF textual estilo PROEG com tabelas densas estreitas, Título de Eleitor e multi-página."""
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontSize = 8.8
    normal.leading = 11.0

    def _draw_footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            36,
            24,
            f"TABELAS DENSAS - RELATÓRIO PROEG - PÁGINA {canvas.getPageNumber()} DE 3",
        )
        canvas.restoreState()

    pdf_doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=28,
        rightMargin=28,
        topMargin=36,
        bottomMargin=62,
    )

    p1_table = Table(
        [
            [
                Paragraph("<b>Bolsista / Discente</b>", normal),
                Paragraph("<b>Documentos</b>", normal),
                Paragraph("<b>Dados Bancários / Bolsa</b>", normal),
                Paragraph("<b>Contato / Saúde</b>", normal),
            ],
            [
                Paragraph("Mateus<br/>Henrique Silva<br/>Cavalcante", normal),
                Paragraph("CPF:<br/>348.729.160-<br/>69<br/>Matrícula:<br/>22401982", normal),
                Paragraph(
                    "Ag: 3563-7<br/>Op. 013<br/>Conta Corrente:<br/>89412-6<br/>Bolsa: R$<br/>1.400,00",
                    normal,
                ),
                Paragraph(
                    "PIX: 9f8e7d6c-5b4a-<br/>4321-8fed-cba987654321<br/>CID-<br/>10: H90.3",
                    normal,
                ),
            ],
            [
                Paragraph("Gabrielly<br/>Tupinambá de<br/>Souza", normal),
                Paragraph(
                    "Inscrição:<br/>202699104<br/>Título Eleitor:<br/>0482 9103 2216",
                    normal,
                ),
                Paragraph("Ag: 0421-9<br/>Conta: 10293-4<br/>Bolsa: R$<br/>1.200,00", normal),
                Paragraph("Tel: +5592984310092<br/>Situação: Cadastrada", normal),
            ],
        ],
        colWidths=[118, 134, 134, 152],
    )
    p1_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    p2_table = Table(
        [
            [
                Paragraph("<b>Discente</b>", normal),
                Paragraph("<b>Inscrição</b>", normal),
                Paragraph("<b>Situação</b>", normal),
            ],
            [
                Paragraph("Gabrielly Tupinambá de Souza", normal),
                Paragraph("Inscrição: 202699104", normal),
                Paragraph("Cadastrada", normal),
            ],
        ],
        colWidths=[190, 145, 200],
    )
    p2_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
            ]
        )
    )

    elements = [
        Paragraph(
            "<b>UNIVERSIDADE FEDERAL DO AMAZONAS — CNPJ 04.378.626/0001-97 — TABELAS DENSAS</b>",
            styles["Heading4"],
        ),
        Paragraph(
            "Processo Administrativo Geral SEI nº 23105.009800/2026-01 (referência normativa). "
            "Processo Individual SEI nº 23105.088101/2026-19 referente ao pagamento de bolsas.",
            normal,
        ),
        Spacer(1, 8),
        p1_table,
        PageBreak(),
        Paragraph("<b>QUADRO COMPLEMENTAR DE BOLSAS — PÁGINA 2</b>", styles["Heading4"]),
        Spacer(1, 8),
        p2_table,
        PageBreak(),
        Paragraph("<b>PARECER SOCIAL E CLÍNICO — PÁGINA 3</b>", styles["Heading4"]),
        Spacer(1, 8),
        Paragraph(
            "A discente Gabrielly Tupinambá de Souza apresentou laudo indicando "
            "Surdez Bilateral Neurosensorial, Episódio Depressivo Moderado, Lombociatalgia Aguda, "
            "Hipertensão e Acompanhamento TEA, autodeclarada Indígena (Povo Tukano).",
            normal,
        ),
    ]
    pdf_doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    dest_path.write_bytes(buf.getvalue())


def _build_gold_scenario02_scanned_pdf(dest_path: Path) -> None:
    """Gera PDF 100% escaneado a 200 DPI sem qualquer camada nativa de texto."""
    text_doc = fitz.open()
    p1 = text_doc.new_page(width=595, height=842)
    p1.insert_text(fitz.Point(40, 48), "DOCUMENTO FÍSICO DIGITALIZADO (200 DPI)", fontsize=12)
    p1.insert_text(fitz.Point(40, 68), "UNIVERSIDADE FEDERAL DO AMAZONAS - ASSENTAMENTO FUNCIONAL", fontsize=10)
    p1.insert_text(fitz.Point(40, 92), "Processo Individual SEI nº 23105.019482/2014-77", fontsize=10)

    photo_rect = fitz.Rect(465, 50, 550, 163)
    p1.draw_rect(photo_rect, color=(0, 0, 0), width=1.6)
    p1.draw_circle(fitz.Point(507, 92), 18, color=(0.35, 0.35, 0.35), fill=(0.55, 0.55, 0.55))
    p1.draw_rect(fitz.Rect(486, 114, 529, 140), color=(0.35, 0.35, 0.35), fill=(0.45, 0.45, 0.45))
    p1.insert_text(fitz.Point(473, 156), "FOTO 3X4 ESCANEADA", fontsize=6.8)

    p1.insert_text(fitz.Point(40, 130), "Servidor(a): ROBERTO CARLOS NOGUEIRA DA SILVA", fontsize=11)
    p1.insert_text(fitz.Point(40, 154), "Filiação: Antônio Nogueira da Silva e Francisca Lima da Silva", fontsize=11)
    p1.insert_text(fitz.Point(40, 178), "Portador do CPF 528.419.630-98, necessita de 30 dias de afastamento.", fontsize=11)
    p1.insert_text(fitz.Point(40, 202), "CNH: 03819274610 | PIS/PASEP: 120.48291.72-9", fontsize=11)
    p1.insert_text(fitz.Point(40, 226), "Endereço: Av. Tefé, nº 1840, Bairro Japiim, Manaus/AM, CEP 69078-000", fontsize=11)
    p1.insert_text(fitz.Point(40, 254), "Diagnóstico: Lombociatalgia Aguda (CID-10: M54.4)", fontsize=11)
    p1.insert_text(fitz.Point(40, 278), "Filiação Sindical: Filiado ao SINTEST-AM", fontsize=11)
    p1.insert_text(fitz.Point(40, 302), "Biometria ID: BIO-AM-774102", fontsize=11)

    p2 = text_doc.new_page(width=595, height=842)
    p2.insert_text(fitz.Point(40, 60), "FICHA COMPLEMENTAR - ARQUIVO E PROTOCOLO", fontsize=11)
    p2.insert_text(fitz.Point(40, 100), "Titular: Roberto Carlos N. da Silva (Servidor)", fontsize=12)
    p2.insert_text(fitz.Point(40, 134), "Dependente: Beatriz Lima N. da Silva (Filha)", fontsize=12)

    scan_doc = fitz.open()
    try:
        for src_p in text_doc:
            pix = src_p.get_pixmap(dpi=200, alpha=False)
            new_p = scan_doc.new_page(width=595, height=842)
            new_p.insert_image(new_p.rect, stream=pix.tobytes("png"))
        dest_path.write_bytes(scan_doc.tobytes())
    finally:
        scan_doc.close()
        text_doc.close()


def test_gold_scenario01_proeg_multiline_titulo_eleitor_and_blackout(tmp_path: Path) -> None:
    """
    Cenário 1 (Padrão Ouro):
    - Detecta Título de Eleitor (`0482 9103 2216`) como `PERSONAL_COMMON` sem incluir `"Título Eleitor:"`;
    - Detecta todas as entidades multi-linha, nomes compostos completos com `group_id` unificado
      entre páginas, patologias sensíveis e dados financeiros em `FINANCIAL_BANKING`;
    - `BLACKOUT` destrói todos os valores sensíveis/pessoais e preserva `"Título Eleitor:"`,
      `"Cadastrada"`, `"04.378.626/0001-97"`, `"23105.009800/2026-01"` e `"TABELAS DENSAS"`.
    """
    src_path = tmp_path / "gold_scenario01_proeg.pdf"
    _build_gold_scenario01_proeg_pdf(src_path)

    doc = fitz.open(str(src_path))
    try:
        ext_pages = [
            extract_single_page(doc[i], page_number=i + 1, render_webp=False)
            for i in range(len(doc))
        ]
    finally:
        doc.close()

    entities = detect_pii_in_pages(ext_pages, session_salt="gold_s1_salt")

    # 1. Título de Eleitor estritamente PERSONAL_COMMON, máscara **** 9103 22**, sem incluir rótulo
    titulos = [e for e in entities if e.entity_type == "TITULO_ELEITOR"]
    assert titulos, f"Título de Eleitor não detectado! Tipos: {[e.entity_type for e in entities]}"
    assert all(t.category == DataCategory.PERSONAL_COMMON for t in titulos)
    joined_titulo = " ".join(" ".join(t.matched_text.split()) for t in titulos)
    assert "0482" in joined_titulo and "9103" in joined_titulo and "2216" in joined_titulo
    assert all("Título" not in t.matched_text and "Eleitor" not in t.matched_text for t in titulos)
    assert any(t.masked_preview == "**** 9103 22**" for t in titulos), [t.masked_preview for t in titulos]

    # 2. Demais entidades multi-linha, nomes compostos e categorias
    by_type: dict[str, list] = {}
    for e in entities:
        by_type.setdefault(e.entity_type, []).append(e)

    assert any("22401982" in e.matched_text for e in by_type.get("MATRICULA_UFAM", []))
    assert any("202699104" in e.matched_text for e in by_type.get("MATRICULA_UFAM", []))
    assert any("H90.3" in e.matched_text for e in by_type.get("CID_MEDICAL", []))
    assert any("89412-6" in e.matched_text for e in by_type.get("BANK_ACCOUNT", []))
    assert any("984310092" in e.matched_text for e in by_type.get("PHONE", []))
    assert any("1.400,00" in " ".join(e.matched_text.split()) for e in by_type.get("REMUNERATION", []))

    sensitive_groups: dict[tuple[int, str, str], list[str]] = {}
    for e in entities:
        if e.category == DataCategory.PERSONAL_SENSITIVE:
            key = (e.page_number, e.entity_type, e.group_id or e.matched_text)
            sensitive_groups.setdefault(key, []).append(" ".join(e.matched_text.split()))
    sensitive_txt = " | ".join(" ".join(parts) for parts in sensitive_groups.values())
    for sens_term in (
        "Surdez Bilateral Neurosensorial",
        "Episódio Depressivo Moderado",
        "Lombociatalgia Aguda",
        "Hipertensão",
        "Acompanhamento TEA",
    ):
        assert sens_term.lower() in sensitive_txt.lower(), f"Ausente em PERSONAL_SENSITIVE: {sens_term}"

    # Propagação de Gabrielly nas páginas 1, 2 e 3 com mesmo group_id e Mateus completo
    gab_gids = {
        e.page_number: e.group_id
        for e in entities
        if e.entity_type == "PERSON_NAME" and ("Gabrielly" in e.matched_text or "Tupinambá" in e.matched_text or "Souza" in e.matched_text)
    }
    assert set(gab_gids.keys()) == {1, 2, 3}, gab_gids
    assert len(set(gab_gids.values())) == 1, gab_gids

    p1_mateus = " ".join(
        e.matched_text
        for e in entities
        if e.page_number == 1 and e.entity_type == "PERSON_NAME" and ("Mateus" in e.matched_text or "Cavalcante" in e.matched_text)
    )
    assert "Mateus" in p1_mateus and "Cavalcante" in p1_mateus

    # 3. BLACKOUT físico e verificação cirúrgica (must_destroy vs must_preserve)
    specs = [
        RedactionBoxSpec(
            page_number=e.page_number,
            x0_norm=e.x0_norm,
            y0_norm=e.y0_norm,
            x1_norm=e.x1_norm,
            y1_norm=e.y1_norm,
            category=e.category,
            action_mode=RedactionActionMode.BLACKOUT,
            entity_type=e.entity_type,
            expected_text=e.matched_text,
        )
        for e in entities
    ]
    raw_out = tmp_path / "gold_s1_raw.pdf"
    final_out = tmp_path / "gold_s1_redacted.pdf"
    apply_true_redactions_to_pdf(src_path, specs, raw_out)
    sanitize_pdf_structure(raw_out, final_out)

    val_res = validate_redacted_pdf_surgically(final_out, specs)
    assert val_res.passed is True

    redacted_doc = fitz.open(str(final_out))
    try:
        redacted_text = "\n".join(p.get_text("text") for p in redacted_doc)
    finally:
        redacted_doc.close()

    must_destroy = (
        "0482",
        "9103",
        "2216",
        "22401982",
        "202699104",
        "348.729.160-",
        "H90.3",
        "89412-6",
        "cba987654321",
        "+5592984310092",
        "1.400,00",
        "Gabrielly",
        "Tupinambá",
        "Mateus",
        "Cavalcante",
        "23105.088101/2026-19",
    )
    for item in must_destroy:
        assert item not in redacted_text, f"Falha BLACKOUT: '{item}' permaneceu no PDF tarjado!"

    must_preserve = (
        "Título Eleitor:",
        "Cadastrada",
        "04.378.626/0001-97",
        "23105.009800/2026-01",
        "TABELAS DENSAS",
    )
    for item in must_preserve:
        assert item in redacted_text, f"Texto institucional/rótulo '{item}' foi indevidamente apagado!"


def test_gold_scenario02_scanned_200dpi_ocr_and_pixel_burn(tmp_path: Path) -> None:
    """
    Cenário 2 (Padrão Ouro):
    PDF 100% escaneado a 200 DPI (zero texto nativo), OCR ONNX + queima física de pixels + re-OCR
    comprovando preservação de `"DOCUMENTO FÍSICO DIGITALIZADO (200 DPI)"` e `"necessita"`.
    """
    src_path = tmp_path / "gold_scenario02_scanned.pdf"
    _build_gold_scenario02_scanned_pdf(src_path)

    doc = fitz.open(str(src_path))
    try:
        assert not doc[0].get_text("words")
        ext_pages = [
            extract_single_page(doc[i], page_number=i + 1, render_webp=False)
            for i in range(len(doc))
        ]
    finally:
        doc.close()

    assert all(p.ocr_executed for p in ext_pages)
    entities = detect_pii_in_pages(ext_pages, session_salt="gold_s2_salt")

    by_type: dict[str, list] = {}
    for e in entities:
        by_type.setdefault(e.entity_type, []).append(e)

    assert any("03819274610" in e.matched_text for e in by_type.get("CNH", []))
    assert any("120.48291.72-9" in e.matched_text for e in by_type.get("PIS_PASEP", []))
    assert any("M54.4" in e.matched_text for e in by_type.get("CID_MEDICAL", []) + by_type.get("HEALTH_DATA", []))
    assert any("SINTEST" in e.matched_text for e in by_type.get("UNION_AFFILIATION", []))
    assert any("BIO-AM-774102" in e.matched_text for e in by_type.get("BIOMETRY_ID", []))
    assert any("Tef" in e.matched_text and "1840" in e.matched_text for e in by_type.get("ADDRESS", []))
    assert any("Francisca Lima da Silva" in e.matched_text for e in by_type.get("FILIACAO", []))
    assert any(
        "Beat" in e.matched_text and "Silva" in e.matched_text
        for e in by_type.get("DEPENDENT_DATA", [])
    )
    assert len(by_type.get("FACIAL_PHOTO", [])) >= 1

    # group_id unificado entre ROBERTO CARLOS NOGUEIRA DA SILVA (pág 1) e Roberto Carlos N. da Silva (pág 2)
    p1_rob = {e.group_id for e in entities if e.page_number == 1 and "ROBERTO CARLOS" in e.matched_text.upper()}
    p2_rob = {e.group_id for e in entities if e.page_number == 2 and "ROBERTO CARLOS" in e.matched_text.upper()}
    assert p1_rob and p2_rob and p1_rob == p2_rob

    # Palavra adjacente "necessita" não pode ser invadida pelo CPF
    cpf_ent = next(e for e in by_type["CPF"] if "528.419.630-98" in e.matched_text)
    nec_tok = next(t for t in ext_pages[0].tokens if "necessita" in t.text.lower())
    assert cpf_ent.x1_norm <= nec_tok.x0_norm + 0.002

    specs = [
        RedactionBoxSpec(
            page_number=e.page_number,
            x0_norm=e.x0_norm,
            y0_norm=e.y0_norm,
            x1_norm=e.x1_norm,
            y1_norm=e.y1_norm,
            category=e.category,
            action_mode=RedactionActionMode.BLACKOUT,
            entity_type=e.entity_type,
            expected_text=e.matched_text,
        )
        for e in entities
    ]
    raw_out = tmp_path / "gold_s2_raw.pdf"
    final_out = tmp_path / "gold_s2_redacted.pdf"
    apply_true_redactions_to_pdf(src_path, specs, raw_out)
    sanitize_pdf_structure(raw_out, final_out)

    val_res = validate_redacted_pdf_surgically(final_out, specs)
    assert val_res.passed is True

    # Re-OCR da página 1 tarjada: cabeçalho e "necessita" intactos, PII destruída
    chk_doc = fitz.open(str(final_out))
    try:
        p1_chk = chk_doc[0]
        pix = p1_chk.get_pixmap(dpi=200, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        raw_ocr, _ = ml_registry.get_ocr_engine()(arr)
        ocr_text = "\n".join(str(item[1]) for item in (raw_ocr or []))

        pw, ph = float(p1_chk.rect.width), float(p1_chk.rect.height)
        nec_clip = fitz.Rect(
            (nec_tok.x0_norm - 0.002) * pw,
            (nec_tok.y0_norm - 0.004) * ph,
            min(pw, (nec_tok.x1_norm + 0.18) * pw),
            min(ph, (nec_tok.y1_norm + 0.004) * ph),
        )
        nec_pix = p1_chk.get_pixmap(clip=nec_clip, dpi=200, alpha=False)
        nec_arr = np.frombuffer(nec_pix.samples, dtype=np.uint8).reshape(nec_pix.height, nec_pix.width, nec_pix.n)
        raw_nec, _ = ml_registry.get_ocr_engine()(nec_arr)
        nec_ocr_text = " ".join(str(item[1]) for item in (raw_nec or []))
    finally:
        chk_doc.close()

    assert "DOCUMENTO" in ocr_text.upper() and "200 DPI" in ocr_text.upper(), ocr_text
    assert "necessita" in nec_ocr_text.lower(), f"'necessita' foi destruída no PDF escaneado: {nec_ocr_text}"
    for burned in ("528.419.630-98", "03819274610", "120.48291.72-9", "BIO-AM-774102", "M54.4"):
        assert burned not in ocr_text, f"PII '{burned}' ainda legível via re-OCR após BLACKOUT!"


def test_gold_scenario03_and_04_regeneration_font_size_zero_pii_and_report_math(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Cenários 3 e 4 (Padrão Ouro):
    - Executa `BLACKOUT` no job e re-gera o MESMO `job_id` com `PARTIAL_MASK` para CPF (`***.729.160-**`),
      RG (`***.4928-*`) e Título de Eleitor (`**** 9103 22**`);
    - Comprova mudança de SHA-256, destruição dos originais, presença das máscaras e `span["size"] >= 10.0`;
    - Audita o `report.pdf`: Zero-PII (inclusive sem Título de Eleitor) e soma matemática exata na linha
      `TOTAL CONSOLIDADO` em todas as colunas quantitativas.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)

    cpf_val = "348.729.160-69"
    rg_val = "3184928-1"
    titulo_val = "0482 9103 2216"

    assert generate_masked_preview("CPF", cpf_val) == "***.729.160-**"
    assert generate_masked_preview("RG", rg_val) == "***.4928-*"
    assert generate_masked_preview("TITULO_ELEITOR", titulo_val) == "**** 9103 22**"

    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text(
        fitz.Point(50, 60),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - EDITAL Nº 044/2026 - HOMOLOGAÇÃO",
        fontsize=12,
    )
    p1.insert_text(
        fitz.Point(50, 110),
        f"Servidor(a): Marcos Vinícius Almeida | CPF: {cpf_val} | RG: {rg_val}",
        fontsize=11,
    )
    p1.insert_text(
        fitz.Point(50, 140),
        f"Título Eleitor:\n{titulo_val}",
        fontsize=11,
    )
    p1.insert_text(
        fitz.Point(50, 180),
        "Laudo Clínico: Surdez Bilateral Neurosensorial | Conta Corrente: 89412-6",
        fontsize=11,
    )
    p1.insert_text(
        fitz.Point(50, 210),
        "Dependente Funcional: Beatriz Lima N. da Silva",
        fontsize=11,
    )
    pdf_bytes = doc.tobytes()
    doc.close()

    client = TestClient(app)
    up_resp = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("edital_gold_s3.pdf", pdf_bytes, "application/pdf")},
    )
    assert up_resp.status_code == 202
    job_id = up_resp.json()["job_id"]

    occ_resp = client.get(f"/api/v1/documents/{job_id}/occurrences")
    assert occ_resp.status_code == 200
    occurrences = occ_resp.json()["occurrences"]

    assert any(o["entity_type"] == "CPF" for o in occurrences)
    assert any(o["entity_type"] == "RG" for o in occurrences)
    titulo_occs = [o for o in occurrences if o["entity_type"] == "TITULO_ELEITOR"]
    assert titulo_occs, occurrences
    assert all(o["category"] == DataCategory.PERSONAL_COMMON.value for o in titulo_occs)
    assert any(o["masked_preview"] == "**** 9103 22**" for o in titulo_occs)

    # Passo 1 (Cenário 3): aplica BLACKOUT global no job
    blk_resp = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "global_mode": RedactionActionMode.BLACKOUT.value,
            "manual_boxes": [],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert blk_resp.status_code == 200
    blk_data = blk_resp.json()
    assert blk_data["status"] == JobStatus.COMPLETED.value
    sha_blackout = blk_data["redacted_file_sha256"]

    pdf_blk_resp = client.get(f"/api/v1/documents/{job_id}/download/pdf")
    assert pdf_blk_resp.status_code == 200
    blk_doc = fitz.open(stream=pdf_blk_resp.content, filetype="pdf")
    try:
        blk_text = "\n".join(p.get_text("text") for p in blk_doc)
    finally:
        blk_doc.close()
    assert cpf_val not in blk_text
    assert rg_val not in blk_text
    assert "0482" not in blk_text and "9103" not in blk_text and "2216" not in blk_text
    assert "**** 9103 22**" not in blk_text

    # Passo 2 (Cenário 3): re-gera o MESMO job_id aplicando PARTIAL_MASK para CPF, RG e TITULO_ELEITOR
    partial_items = []
    for o in occurrences:
        if o["entity_type"] in ("CPF", "RG", "TITULO_ELEITOR"):
            partial_items.append(
                {
                    "id": o["id"],
                    "action_mode": RedactionActionMode.PARTIAL_MASK.value,
                    "masked_preview": o["masked_preview"],
                }
            )
        else:
            partial_items.append(
                {
                    "id": o["id"],
                    "action_mode": RedactionActionMode.BLACKOUT.value,
                }
            )

    mask_resp = client.post(
        f"/api/v1/documents/{job_id}/redact",
        json={
            "items": partial_items,
            "manual_boxes": [],
            "anonymization_disclaimer_acknowledged": True,
        },
    )
    assert mask_resp.status_code == 200
    mask_data = mask_resp.json()
    assert mask_data["status"] == JobStatus.COMPLETED.value
    assert mask_data["audit_log"]["security_validation_passed"] is True

    sha_partial = mask_data["redacted_file_sha256"]
    assert sha_partial != sha_blackout
    assert sha_partial != mask_data["original_file_sha256"]

    pdf_mask_resp = client.get(f"/api/v1/documents/{job_id}/download/pdf")
    assert pdf_mask_resp.status_code == 200
    assert hashlib.sha256(pdf_mask_resp.content).hexdigest() == sha_partial

    mask_doc = fitz.open(stream=pdf_mask_resp.content, filetype="pdf")
    try:
        mask_text = "\n".join(p.get_text("text") for p in mask_doc)
        assert cpf_val not in mask_text
        assert rg_val not in mask_text
        assert "0482" not in mask_text and "2216" not in mask_text
        assert "Título Eleitor:" in mask_text

        span_sizes: list[tuple[str, float]] = []
        p_dict = mask_doc[0].get_text("dict")
        for blk in p_dict.get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    stxt = str(span.get("text", "")).strip()
                    if "***" in stxt:
                        span_sizes.append((stxt, float(span.get("size", 0.0))))

        assert any("***.729.160-**" in t and sz >= 10.0 for t, sz in span_sizes), span_sizes
        assert any("***.4928-*" in t and sz >= 10.0 for t, sz in span_sizes), span_sizes
        assert any("**** 9103 22**" in t and sz >= 10.0 for t, sz in span_sizes), span_sizes
    finally:
        mask_doc.close()

    # Cenário 4: Auditoria Zero-PII do report.pdf e soma matemática exata de TOTAL CONSOLIDADO
    rep_resp = client.get(f"/api/v1/documents/{job_id}/download/report")
    assert rep_resp.status_code == 200
    assert rep_resp.headers["content-type"] == "application/pdf"

    rep_doc = fitz.open(stream=rep_resp.content, filetype="pdf")
    try:
        rep_text = "\n".join(p.get_text("text") for p in rep_doc)
    finally:
        rep_doc.close()

    for forbidden in (
        cpf_val,
        rg_val,
        titulo_val,
        "0482 9103",
        "9103 2216",
        "Marcos Vinícius Almeida",
        "Beatriz Lima",
        "Surdez Bilateral",
        "89412-6",
    ):
        assert forbidden not in rep_text, f"VIOLAÇÃO ZERO-PII no report.pdf: '{forbidden}' vazou!"

    rep_without_hashes = (
        rep_text.replace(mask_data["original_file_sha256"], "")
        .replace(sha_partial, "")
        .replace(job_id, "")
    )
    for short_block in ("0482", "9103", "2216"):
        assert short_block not in rep_without_hashes, (
            f"VIOLAÇÃO ZERO-PII no report.pdf: bloco '{short_block}' vazou fora de hashes!"
        )

    assert "TOTAL CONSOLIDADO" in rep_text

    # Extrai os 6 números das 4 categorias e da linha TOTAL CONSOLIDADO
    cat_markers = (
        "PERSONAL_COMMON",
        "PERSONAL_SENSITIVE",
        "FINANCIAL_BANKING",
        "MINOR_OR_DEPENDENT",
    )
    cat_vectors: list[list[int]] = []
    for marker in cat_markers:
        m = re.search(
            re.escape(marker) + r"[\s\S]{0,120}?((?:\d+\s+){5}\d+)",
            rep_text,
        )
        assert m is not None, f"Linha quantitativa de '{marker}' não encontrada em:\n{rep_text}"
        nums = [int(x) for x in m.group(1).split()]
        assert len(nums) == 6
        cat_vectors.append(nums)

    m_tot = re.search(r"TOTAL CONSOLIDADO\s+((?:\d+\s+){5}\d+)", rep_text)
    assert m_tot is not None, f"Linha TOTAL CONSOLIDADO não encontrada em:\n{rep_text}"
    tot_vector = [int(x) for x in m_tot.group(1).split()]
    assert len(tot_vector) == 6

    expected_sums = [sum(vec[col] for vec in cat_vectors) for col in range(6)]
    assert tot_vector == expected_sums, (
        f"Divergência matemática em TOTAL CONSOLIDADO: obtido={tot_vector}, esperado={expected_sums}"
    )
    assert tot_vector[0] >= 6
    assert tot_vector[2] == 3  # CPF + RG + Título de Eleitor mascarados


def test_gold_scenario05_birth_dates_wrapped_names_sensitive_lgpd_and_blocklist(
    tmp_path: Path,
) -> None:
    """
    Cenário 5 (Regressão Forense Completa):
    - Datas de nascimento em texto e colunas de tabela ('Nascimento' / 'Data Nasc') sem capturar idade;
    - Nomes quebrados em múltiplas linhas (Title Case, UPPERCASE e preposições 'de/da') + propagação global;
    - Cobertura integral Art. 5º, II LGPD (6 diagnósticos clínicos, HLA-B27, biometria/foto, raça, sindicato,
      crença religiosa) + guarda contra citação abstrata da LGPD ('sindicato ou a');
    - Eliminação de over-redaction (blocklist estrutural, cargo, naturalidade, SIAPE estrito e Insc.).
    """
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontSize = 8.8
    normal.leading = 11.0

    pdf_doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=28, rightMargin=28, topMargin=32, bottomMargin=36)

    tbl_dados = Table(
        [
            [
                Paragraph("<b>Servidor / Dependente</b>", normal),
                Paragraph("<b>Data Nasc</b>", normal),
                Paragraph("<b>Matrícula SIAPE</b>", normal),
                Paragraph("<b>Condição Médica</b>", normal),
            ],
            [
                Paragraph("Carlos Eduardo Mendonça de<br/>Oliveira", normal),
                Paragraph("14/08/1984 (41 anos)", normal),
                Paragraph("1948273", normal),
                Paragraph("Dor Lombar Baixa Crônica com Radiculopatia", normal),
            ],
            [
                Paragraph("Patrícia Helena Martins<br/>Fontes (Pensionista)", normal),
                Paragraph("28/06/1975", normal),
                Paragraph("2847190", normal),
                Paragraph("Reação Aguda ao Estresse e Burnout", normal),
            ],
            [
                Paragraph("Camila Farias de<br/>Queiroz", normal),
                Paragraph("22/05/2016 (Filha)", normal),
                Paragraph("Insc.: 20261001", normal),
                Paragraph("Visão Monocular e Retinopatia", normal),
            ],
            [
                Paragraph("Otávio Augusto Peixoto de<br/>Alencar", normal),
                Paragraph("09/11/2012", normal),
                Paragraph("Válido Sintético", normal),
                Paragraph("Transtorno de Ansiedade Generalizada", normal),
            ],
        ],
        colWidths=[150, 100, 100, 188],
    )
    tbl_dados.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    tbl_nasc_extra = Table(
        [
            [
                Paragraph("<b>Nome Completo do Menor</b>", normal),
                Paragraph("<b>Nascimento</b>", normal),
            ],
            [Paragraph("Lucas Medeiros (Filho)", normal), Paragraph("19/02/2004", normal)],
            [Paragraph("Ana Clara Souza (Filha)", normal), Paragraph("11/07/2003", normal)],
            [Paragraph("Pedro Lucas Lima", normal), Paragraph("25/09/2009", normal)],
            [Paragraph("Mariana Alves Costa", normal), Paragraph("15/04/2018", normal)],
            [Paragraph("João Vítor Pereira", normal), Paragraph("28/09/2015", normal)],
        ],
        colWidths=[270, 268],
    )
    tbl_nasc_extra.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.75, colors.black)]))

    elements = [
        Paragraph("<b>RELATÓRIO DE S E MENORES DE IDADE E DEPENDENTES MENORES</b>", styles["Heading4"]),
        Paragraph(
            "Nos termos do art. 5º, II, da Lei nº 13.709/2018 (LGPD), considera-se dado pessoal sensível "
            "aquele referente à origem racial ou étnica, convicção religiosa, opinião política, filiação a "
            "sindicato ou a organização de caráter religioso, filosófico ou político, dado referente à saúde.",
            normal,
        ),
        Spacer(1, 6),
        tbl_dados,
        Spacer(1, 8),
        tbl_nasc_extra,
        Spacer(1, 8),
        Paragraph(
            "Cargo: Analista de Tecnologia da Informação, natural de Manaus/AM. "
            "Ação de Pensão Alimentícia | Ciência do Interessado | Assinatura do Declarante. "
            "Processo Reservado SEI nº 23105.044210/2025-88. "
            "Sindicância SEI nº 23105.033120/2025-11.",
            normal,
        ),
        Paragraph(
            "Registro Facial ID: BIO-UFAM-994827-AF82 | Foto: #IMG-8821 | [FOTO 3x4 - REQUERENTE]. "
            "Marcador genético positivo para HLA-B27. "
            "Diagnóstico adicional: Episódio Depressivo Moderado e Lombociatalgia Aguda.",
            normal,
        ),
        Paragraph(
            "Autodeclaração étnico-racial homologada como Parda / Negra (também registros Preto / Quilombola e Indígena / Pardo). "
            "Filiada ativa à ADUA — Seção Sindical (Ficha Sindical nº 4.812) e filiado ao SINTEST-AM. "
            "Convicção religiosa declarada: Adventista do Sétimo Dia.",
            normal,
        ),
        PageBreak(),
        Paragraph(
            "Na Seção 3, confirma-se que Carlos Eduardo Mendonça de Oliveira, Camila Farias de Queiroz e "
            "Otávio Augusto Peixoto de Alencar permanecem vinculados ao atendimento clínico.",
            normal,
        ),
    ]
    pdf_doc.build(elements)
    src_path = tmp_path / "gold_scenario05_forensic.pdf"
    src_path.write_bytes(buf.getvalue())

    doc = fitz.open(str(src_path))
    try:
        ext_pages = [
            extract_single_page(doc[i], page_number=i + 1, render_webp=False)
            for i in range(len(doc))
        ]
    finally:
        doc.close()

    entities = detect_pii_in_pages(ext_pages, session_salt="gold_s5_salt")

    # 1. Datas de nascimento completas sem incluir "(41 anos)" ou "(Filha)"
    birth_texts = " | ".join(
        e.matched_text for e in entities if e.entity_type == "BIRTH_DATE"
    )
    for expected_date in (
        "14/08/1984",
        "22/05/2016",
        "09/11/2012",
        "19/02/2004",
        "11/07/2003",
        "25/09/2009",
        "15/04/2018",
        "28/09/2015",
        "28/06/1975",
    ):
        assert expected_date in birth_texts, f"Data ausente em BIRTH_DATE: {expected_date} -> {birth_texts}"
    assert all("anos" not in e.matched_text.lower() for e in entities if e.entity_type == "BIRTH_DATE")

    # 2. Nomes quebrados em múltiplas linhas e propagação multi-página
    person_entities = [e for e in entities if e.entity_type == "PERSON_NAME"]
    all_person_like = [e for e in entities if e.entity_type in ("PERSON_NAME", "DEPENDENT_DATA")]
    grouped_person_names: dict[tuple[int, str], list[str]] = {}
    for e in sorted(all_person_like, key=lambda x: (x.page_number, x.y0_norm, x.x0_norm)):
        grouped_person_names.setdefault((e.page_number, e.group_id or e.matched_text), []).append(
            " ".join(e.matched_text.split())
        )
    grouped_texts = " | ".join(" ".join(parts) for parts in grouped_person_names.values())
    for full_name in (
        "Carlos Eduardo Mendonça de Oliveira",
        "Patrícia Helena Martins Fontes",
        "Camila Farias de Queiroz",
        "Otávio Augusto Peixoto de Alencar",
    ):
        assert full_name in grouped_texts, f"Nome completo ausente: {full_name} -> {grouped_texts}"
    assert any("Carlos Eduardo" in e.matched_text for e in person_entities)
    assert any("Patrícia Helena" in e.matched_text for e in person_entities)
    assert all("Pensionista" not in e.matched_text for e in all_person_like)

    # Carlos Eduardo na pág 1 (quebrado) e pág 2 compartilham o mesmo group_id e cobrem todas as linhas
    carlos_p1 = [e for e in person_entities if e.page_number == 1 and "Carlos Eduardo" in e.matched_text]
    carlos_p2 = [e for e in person_entities if e.page_number == 2 and "Carlos Eduardo" in e.matched_text]
    assert carlos_p1 and carlos_p2
    assert {e.group_id for e in carlos_p1} == {e.group_id for e in carlos_p2}

    # 3. Cobertura integral Art. 5º, II + guarda de citação legal
    sens_by_type: dict[str, str] = {}
    sens_groups: dict[tuple[int, str, str], list[str]] = {}
    for e in sorted(entities, key=lambda x: (x.page_number, x.y0_norm, x.x0_norm)):
        if e.category == DataCategory.PERSONAL_SENSITIVE:
            sens_groups.setdefault(
                (e.page_number, e.entity_type, e.group_id or e.matched_text), []
            ).append(" ".join(e.matched_text.split()))
            sens_by_type[e.entity_type] = (
                sens_by_type.get(e.entity_type, "") + " | " + " ".join(e.matched_text.split())
            )

    all_sens = " | ".join(" ".join(parts) for parts in sens_groups.values())
    sens_by_type["UNION_AFFILIATION"] = " | ".join(" ".join(parts) for (_, et, _), parts in sens_groups.items() if et == "UNION_AFFILIATION")
    for diag in (
        "Dor Lombar Baixa Crônica com Radiculopatia",
        "Reação Aguda ao Estresse e Burnout",
        "Visão Monocular e Retinopatia",
        "Transtorno de Ansiedade Generalizada",
        "Episódio Depressivo Moderado",
        "Lombociatalgia Aguda",
    ):
        assert diag.lower() in all_sens.lower(), f"Diagnóstico ausente: {diag}"

    assert "HLA-B27" in sens_by_type.get("GENETIC_DATA", "")
    assert "BIO-UFAM-994827-AF82" in sens_by_type.get("BIOMETRY_ID", "")
    assert "#IMG-8821" in sens_by_type.get("BIOMETRY_ID", "")
    assert "FOTO 3x4" in sens_by_type.get("FACIAL_PHOTO", "")
    ethnic_txt = sens_by_type.get("ETHNIC_RACIAL_DECLARATION", "")
    for ethnic in ("Parda / Negra", "Preto / Quilombola", "Indígena / Pardo"):
        assert ethnic in ethnic_txt, ethnic_txt
    assert "ADUA" in sens_by_type.get("UNION_AFFILIATION", "")
    assert "4.812" in sens_by_type.get("UNION_AFFILIATION", "")
    assert "SINTEST-AM" in sens_by_type.get("UNION_AFFILIATION", "")
    assert "Adventista" in sens_by_type.get("RELIGIOUS_BELIEF", "")

    # Citação legal abstrata da LGPD NÃO pode gerar falso positivo "sindicato ou a"
    assert all("sindicato ou a" not in e.matched_text.lower() for e in entities)

    # 4. Strict SIAPE, Insc. e SEI Reservado/Sindicância -> PERSONAL_COMMON
    matriculas = " | ".join(
        e.matched_text for e in entities if e.entity_type in ("MATRICULA_UFAM", "SIAPE")
    )
    assert "1948273" in matriculas
    assert "2847190" in matriculas
    assert "20261001" in matriculas

    sei_entities = [e for e in entities if e.entity_type == "PROCESSO_SEI"]
    assert any("23105.044210/2025-88" in e.matched_text for e in sei_entities)
    assert any("23105.033120/2025-11" in e.matched_text for e in sei_entities)
    assert all(e.category == DataCategory.PERSONAL_COMMON for e in sei_entities)

    # 5. Blocklist de over-redaction: nenhum termo estrutural/cargo/naturalidade deve ser detectado
    all_matched = " || ".join(" ".join(e.matched_text.split()) for e in entities)
    for forbidden_fp in (
        "Nome Completo do Menor",
        "Condição Médica",
        "S E MENORES DE IDADE",
        "DEPENDENTES MENORES",
        "Válido Sintético",
        "Pensão Alimentícia",
        "Ciência do Interessado",
        "Assinatura do",
        "Registro Facial ID:",
        "Analista de Tecnologia da Informação",
        "natural de Manaus/",
    ):
        assert forbidden_fp.lower() not in all_matched.lower(), (
            f"Falso positivo detectado (over-redaction): '{forbidden_fp}' em {all_matched}"
        )
    assert all(e.matched_text.strip().lower() not in {"filho", "filha"} for e in entities)

    # 6. Valida que BLACKOUT destrói 100% os nomes quebrados (inclusive "Oliveira" e "Queiroz")
    specs = [
        RedactionBoxSpec(
            page_number=e.page_number,
            x0_norm=e.x0_norm,
            y0_norm=e.y0_norm,
            x1_norm=e.x1_norm,
            y1_norm=e.y1_norm,
            category=e.category,
            action_mode=RedactionActionMode.BLACKOUT,
            entity_type=e.entity_type,
            expected_text=e.matched_text,
        )
        for e in entities
    ]
    raw_out = tmp_path / "gold_s5_raw.pdf"
    final_out = tmp_path / "gold_s5_redacted.pdf"
    apply_true_redactions_to_pdf(src_path, specs, raw_out)
    sanitize_pdf_structure(raw_out, final_out)
    assert validate_redacted_pdf_surgically(final_out, specs).passed is True

    chk = fitz.open(str(final_out))
    try:
        txt = "\n".join(p.get_text("text") for p in chk)
    finally:
        chk.close()
    for destroyed in ("Oliveira", "Fontes", "Queiroz", "Alencar", "HLA-B27", "14/08/1984"):
        assert destroyed not in txt, f"Restou texto após BLACKOUT: {destroyed}"
    assert "Pensionista" in txt
    assert "Analista de Tecnologia da Informação" in txt


def test_gold_scenario06_public_transparency_contextual_minor_and_sensitive_blackout(
    tmp_path: Path,
) -> None:
    """
    Cenário 6 (Regra Contextual PUBLIC_TRANSPARENCY):
    - Em Edital de Transparência Ativa: nome de adulto -> KEEP; CPF/RG de adulto -> PARTIAL_MASK;
    - Qualquer menor/dependente (inclusive CPF do menor) OU titular no mesmo parágrafo de CID/saúde
      recebe obrigatoriamente recommended_mode == BLACKOUT e action_mode == BLACKOUT.
    """
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text(
        fitz.Point(45, 55),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - EDITAL Nº 088/2026 - TRANSPARÊNCIA ATIVA - RESULTADO",
        fontsize=11.5,
    )
    p1.insert_text(
        fitz.Point(45, 110),
        "Candidata Aprovada: Mariana Costa Nogueira | CPF: 412.536.748-50 | RG: 2849103-5.",
        fontsize=10.5,
    )
    p1.insert_text(
        fitz.Point(45, 165),
        "Dependente Menor: Lucas Henrique Silva (Filho, 10 anos) | CPF: 673.184.250-80.",
        fontsize=10.5,
    )
    p1.insert_text(
        fitz.Point(45, 225),
        "Servidor: Rafael Mendes Araújo | CPF: 590.817.326-68",
        fontsize=10.5,
    )
    p1.insert_text(
        fitz.Point(45, 240),
        "Diagnóstico: Transtorno de Ansiedade Generalizada (CID-10: F41.1).",
        fontsize=10.5,
    )
    src_path = tmp_path / "gold_scenario06_edital.pdf"
    doc.save(str(src_path))
    doc.close()

    chk = fitz.open(str(src_path))
    try:
        ext_pages = [extract_single_page(chk[0], page_number=1, render_webp=False)]
    finally:
        chk.close()

    entities = detect_pii_in_pages(
        ext_pages,
        session_salt="gold_s6_salt",
        document_profile=DocumentProfile.PUBLIC_TRANSPARENCY,
    )

    mariana_names = [e for e in entities if e.entity_type == "PERSON_NAME" and "Mariana" in e.matched_text]
    mariana_cpf = [e for e in entities if e.entity_type == "CPF" and "412.536.748-50" in e.matched_text]
    mariana_rg = [e for e in entities if e.entity_type == "RG" and "2849103-5" in e.matched_text]
    assert mariana_names and all(e.recommended_mode == RedactionActionMode.KEEP for e in mariana_names)
    assert mariana_cpf and all(e.recommended_mode == RedactionActionMode.PARTIAL_MASK for e in mariana_cpf)
    assert mariana_rg and all(e.recommended_mode == RedactionActionMode.PARTIAL_MASK for e in mariana_rg)

    minor_ents = [e for e in entities if e.category == DataCategory.MINOR_OR_DEPENDENT]
    minor_cpf = [e for e in entities if e.entity_type == "CPF" and "673.184.250-80" in e.matched_text]
    assert minor_ents and all(e.recommended_mode == RedactionActionMode.BLACKOUT for e in minor_ents)
    assert minor_cpf and all(e.recommended_mode == RedactionActionMode.BLACKOUT for e in minor_cpf)

    rafael_ents = [
        e
        for e in entities
        if ("Rafael" in e.matched_text or "590.817.326-68" in e.matched_text or "F41.1" in e.matched_text)
    ]
    assert len(rafael_ents) >= 3
    assert all(e.recommended_mode == RedactionActionMode.BLACKOUT for e in rafael_ents), [
        (e.entity_type, e.matched_text, e.recommended_mode) for e in rafael_ents
    ]

    # Verifica endpoint HTTP de upload + occurrences respeitando action_mode contextual
    client = TestClient(app)
    up = client.post(
        "/api/v1/documents/upload?defer_remaining_pages=false",
        files={"file": ("edital_transparencia_s6.pdf", src_path.read_bytes(), "application/pdf")},
    )
    assert up.status_code == 202
    job_id = up.json()["job_id"]
    occ_res = client.get(f"/api/v1/documents/{job_id}/occurrences").json()["occurrences"]

    api_mariana = [o for o in occ_res if o["entity_type"] == "PERSON_NAME" and "Mariana" in (o.get("masked_preview") or "")]
    api_mariana_cpf = [o for o in occ_res if o["entity_type"] == "CPF" and "536.748" in (o.get("masked_preview") or "")]
    api_minor_cpf = [o for o in occ_res if o["entity_type"] == "CPF" and "184.250" in (o.get("masked_preview") or "")]
    api_rafael_cpf = [o for o in occ_res if o["entity_type"] == "CPF" and "817.326" in (o.get("masked_preview") or "")]
    assert api_mariana and all(o["action_mode"] == "KEEP" for o in api_mariana)
    assert api_mariana_cpf and all(o["action_mode"] == "PARTIAL_MASK" for o in api_mariana_cpf)
    assert api_minor_cpf and all(o["action_mode"] == "BLACKOUT" for o in api_minor_cpf)
    assert api_rafael_cpf and all(o["action_mode"] == "BLACKOUT" for o in api_rafael_cpf)


def test_gold_scenario07_ocr_bilateral_safe_margin_metadata_and_clamping(tmp_path: Path) -> None:
    """
    Cenário 7: Verifica que entidades OCR carregam `is_ocr=True` e limites horizontais seguros
    (`safe_x0_norm`, `safe_x1_norm`), expandindo ±4pt na queima sem jamais invadir `"necessita"`.
    """
    src_path = tmp_path / "gold_scenario07_ocr_clamp.pdf"
    _build_gold_scenario02_scanned_pdf(src_path)

    doc = fitz.open(str(src_path))
    try:
        ext_pages = [extract_single_page(doc[0], page_number=1, render_webp=False)]
    finally:
        doc.close()

    entities = detect_pii_in_pages(ext_pages, session_salt="gold_s7_salt")
    cpf_ent = next(e for e in entities if e.entity_type == "CPF" and "528.419.630-98" in e.matched_text)
    nec_tok = next(t for t in ext_pages[0].tokens if "necessita" in t.text.lower())

    assert cpf_ent.is_ocr is True
    assert cpf_ent.safe_x0_norm is not None and cpf_ent.safe_x1_norm is not None
    assert cpf_ent.safe_x1_norm <= nec_tok.x0_norm + 0.002

    specs = [
        RedactionBoxSpec(
            page_number=cpf_ent.page_number,
            x0_norm=cpf_ent.x0_norm,
            y0_norm=cpf_ent.y0_norm,
            x1_norm=cpf_ent.x1_norm,
            y1_norm=cpf_ent.y1_norm,
            category=cpf_ent.category,
            action_mode=RedactionActionMode.BLACKOUT,
            entity_type=cpf_ent.entity_type,
            expected_text=cpf_ent.matched_text,
            is_ocr=cpf_ent.is_ocr,
            safe_x0_norm=cpf_ent.safe_x0_norm,
            safe_x1_norm=cpf_ent.safe_x1_norm,
        )
    ]
    raw_out = tmp_path / "gold_s7_raw.pdf"
    final_out = tmp_path / "gold_s7_redacted.pdf"
    apply_true_redactions_to_pdf(src_path, specs, raw_out)
    sanitize_pdf_structure(raw_out, final_out)

    chk = fitz.open(str(final_out))
    try:
        p1 = chk[0]
        pw, ph = float(p1.rect.width), float(p1.rect.height)
        nec_clip = fitz.Rect(
            (nec_tok.x0_norm - 0.002) * pw,
            (nec_tok.y0_norm - 0.004) * ph,
            min(pw, (nec_tok.x1_norm + 0.18) * pw),
            min(ph, (nec_tok.y1_norm + 0.004) * ph),
        )
        nec_pix = p1.get_pixmap(clip=nec_clip, dpi=200, alpha=False)
        nec_arr = np.frombuffer(nec_pix.samples, dtype=np.uint8).reshape(nec_pix.height, nec_pix.width, nec_pix.n)
        raw_nec, _ = ml_registry.get_ocr_engine()(nec_arr)
        nec_text = " ".join(str(item[1]) for item in (raw_nec or []))
    finally:
        chk.close()
    assert "necessita" in nec_text.lower(), nec_text
