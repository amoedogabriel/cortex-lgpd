"""
Testes automatizados da Etapa 3 (`test_pii_detector.py`):
1. Validação matemática real de dígitos verificadores (CPF válido vs. inválido, CNPJ, CNH, PIS/PASEP);
2. Precisão em textos administrativos da UFAM:
   - CPF válido detectado vs. CPF inválido rejeitado;
   - Matrícula SIAPE e Matrícula Acadêmica UFAM (`PERSONAL_COMMON`);
   - Processo SEI organizacional isolado/Geral descartado vs. Processo SEI nominal/Individual retido;
   - CNPJ institucional da UFAM (`04.378.626/0001-97`) e datas de publicação descartados;
   - Código médico CID-10 / CID-11 (inclusive quebrado `CID-\\n10`), condições clínicas (Surdez, PCD, TEA), filiação sindical e autodeclaração étnico-racial (`Povo ...`, `Autodeclarad[oa] ...`) classificados como `PERSONAL_SENSITIVE`;
   - Dados bancários (Agência `Ag:`, Operação `Op.`, Conta, Chave PIX, Remuneração/Bolsa inclusive `R$\\n1.400,00`) classificados estritamente como `FINANCIAL_BANKING` (jamais como `PERSONAL_SENSITIVE`);
   - Dependentes e menores classificados como `MINOR_OR_DEPENDENT`;
3. Dados com quebra de linha em células estreitas de tabela e texto corrido (CPF, PIX UUID, Conta Corrente, Telefone incluindo `+55`, CID, Nomes Compostos de 2-3 linhas, CNH, PIS/PASEP, Título de Eleitor, Matrícula/Inscrição, Data de Nascimento e Logradouro sem `nº`), propagação cruzada de nomes completos entre páginas (`Page 3 -> Pages 1 & 2` com `group_id` unificado), filtro de títulos em caixa alta e rodapé (`y0_norm > 0.93`);
4. Aplicação física em `BLACKOUT` e `PARTIAL_MASK` com fonte legível (`>= 10.0pt`) alinhada à linha de base;
5. Benchmark de alta performance: análise completa de 10 páginas textuais em menos de 500ms.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.core.ml_models import ml_registry
from app.models.enums import ConfidenceLevel, DataCategory, DocumentProfile, RedactionActionMode
from app.services.pdf_extractor.extractor import extract_single_page
from app.services.pii_detector.entity_mapper import (
    compute_entity_group_id,
    detect_pii_in_pages,
)
from app.services.pii_detector.validators import (
    validate_cnh,
    validate_cnpj,
    validate_cpf,
    validate_pis_pasep_nit,
)
from app.services.redactor.pdf_redactor import RedactionBoxSpec, apply_true_redactions_to_pdf
from app.services.redactor.structural_sanitizer import sanitize_pdf_structure
from app.services.security_validator.post_redaction_validator import (
    validate_redacted_pdf_surgically,
)
from app.worker.tasks import (
    analyze_document_task,
    process_single_page_into_state,
    store_job_transient_state,
)


def _generate_valid_cpf(nine_digits: str) -> str:
    """Calcula os dois dígitos verificadores reais para uma base de 9 dígitos e retorna formatado."""
    nums = [int(c) for c in nine_digits]
    s1 = sum(nums[i] * (10 - i) for i in range(9))
    r1 = (s1 * 10) % 11
    d1 = 0 if r1 == 10 else r1
    nums.append(d1)

    s2 = sum(nums[i] * (11 - i) for i in range(10))
    r2 = (s2 * 10) % 11
    d2 = 0 if r2 == 10 else r2
    nums.append(d2)

    d = "".join(str(x) for x in nums)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


VALID_CPF_1 = _generate_valid_cpf("529982247")  # 529.982.247-25
INVALID_CPF_1 = "529.982.247-99"  # Dígito verificador incorreto


@pytest.fixture(scope="module", autouse=True)
def warmup_nlp_once() -> None:
    """Garante que o modelo spaCy + EntityRuler estejam pré-aquecidos em RAM antes dos testes."""
    ml_registry.warmup(load_ocr=False, load_spacy=True)


def test_mathematical_check_digit_validators() -> None:
    """Valida cálculo real de dígitos verificadores para CPF, CNPJ, CNH e PIS/PASEP."""
    assert validate_cpf(VALID_CPF_1) is True
    assert validate_cpf(INVALID_CPF_1) is False
    assert validate_cpf("111.111.111-11") is False

    # CNPJ oficial da UFAM (04.378.626/0001-97)
    assert validate_cnpj("04.378.626/0001-97") is True
    assert validate_cnpj("04.378.626/0001-00") is False

    # PIS/PASEP válido (pesos 3,2,9,8,7,6,5,4,3,2 -> base 1205687418: 207 % 11 = 9 -> 11 - 9 = 2 -> 120.56874.18-2)
    assert validate_pis_pasep_nit("120.56874.18-2") is True
    assert validate_pis_pasep_nit("120.56874.18-9") is False

    # CNH válida (base 123456789 -> dv1=0, dv2=0 => 12345678900)
    assert validate_cnh("12345678900") is True
    assert validate_cnh("12345678901") is False


def test_ufam_administrative_document_precision_and_taxonomy() -> None:
    """
    Valida precisão de detecção, taxonomia LGPD estrita, supressão de falsos positivos
    (CNPJ UFAM, processo SEI isolado vs. nominal, CPF inválido) e agrupamento por `group_id`.
    """
    doc = fitz.open()

    # Página 1: Dados funcionais, processo SEI isolado vs. nominal, laudo médico CID e dados bancários
    p1 = doc.new_page(width=595, height=842)
    p1_lines = [
        "UNIVERSIDADE FEDERAL DO AMAZONAS - CNPJ 04.378.626/0001-97",
        "Portaria PROGESP N 104/2026 - Publicada em 14/03/2026",
        "Referencia Organizacional: Processo 23105.001122/2026-10 sobre calendario institucional.",
        f"Interessado(a): Carlos Eduardo Amazonas - CPF: {VALID_CPF_1} - Processo 23105.045678/2026-88",
        f"Teste de CPF com digito verificador invalido que deve ser ignorado: {INVALID_CPF_1}",
        "Matricula SIAPE: 1847291 | Matricula UFAM: 2021004512 | RG: 1458923-4",
        "E-mail: carlos.amazonas@ufam.edu.br | Telefone: (92) 98123-4567 | CEP: 69067-005",
        "Laudo Pericial de Saude - Diagnostico clinico CID: F32.1 - Filiação Sindical: ADUA",
        "Autodeclaracao etnico-racial: Pardo(a)",
        "Dados de Pagamento: Agencia: 3563-7 | Conta Corrente: 482910-5 | Chave PIX: a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        "Remuneracao: R$ 14.520,80",
        "Dependente Funcional: Lucas Gabriel Amazonas",
    ]
    y_cursor = 50
    for line in p1_lines:
        p1.insert_text(fitz.Point(40, y_cursor), line, fontsize=10)
        y_cursor += 28

    # Página 2: Repete o mesmo CPF (sem pontuação via rótulo) e o mesmo Servidor para validar group_id
    p2 = doc.new_page(width=595, height=842)
    cpf_digits_only = VALID_CPF_1.replace(".", "").replace("-", "")
    p2.insert_text(
        fitz.Point(40, 60),
        f"Requerente: Carlos Eduardo Amazonas | CPF: {cpf_digits_only} | SIAPE: 1847291",
        fontsize=10,
    )

    try:
        ext_pages = [
            extract_single_page(doc[0], page_number=1, render_webp=False),
            extract_single_page(doc[1], page_number=2, render_webp=False),
        ]
    finally:
        doc.close()

    entities = detect_pii_in_pages(ext_pages, session_salt="test_session_ufam")

    # 1. CPF válido deve ser detectado nas páginas 1 e 2; CPF inválido deve ser rejeitado
    cpf_entities = [e for e in entities if e.entity_type == "CPF"]
    assert len(cpf_entities) == 2
    for ce in cpf_entities:
        assert INVALID_CPF_1 not in ce.matched_text
        assert ce.category == DataCategory.PERSONAL_COMMON
        assert ce.confidence_level == ConfidenceLevel.HIGH

    # O group_id do CPF formatado na pág. 1 deve ser idêntico ao do CPF só-dígitos na pág. 2
    assert cpf_entities[0].group_id == cpf_entities[1].group_id
    assert cpf_entities[0].group_id == compute_entity_group_id("CPF", VALID_CPF_1, session_salt="test_session_ufam")

    # 2. SIAPE e Matrícula Acadêmica UFAM detectados
    siape_entities = [e for e in entities if e.entity_type == "SIAPE"]
    assert len(siape_entities) == 2
    assert siape_entities[0].group_id == siape_entities[1].group_id

    matricula_entities = [e for e in entities if e.entity_type == "MATRICULA_UFAM"]
    assert len(matricula_entities) == 1
    assert matricula_entities[0].matched_text == "2021004512"

    # 3. Processo SEI: o organizacional isolado (23105.001122/2026-10) deve ser SUPRIMIDO,
    #    enquanto o processo SEI vinculado ao interessado (23105.045678/2026-88) deve ser RETIDO
    sei_entities = [e for e in entities if e.entity_type == "PROCESSO_SEI"]
    sei_values = {e.matched_text for e in sei_entities}
    assert "23105.045678/2026-88" in sei_values
    assert "23105.001122/2026-10" not in sei_values

    # 4. CNPJ institucional da UFAM jamais deve ser classificado como PII
    all_matched_texts = {e.matched_text for e in entities}
    assert "04.378.626/0001-97" not in all_matched_texts

    # 5. CID médico, filiação sindical e autodeclaração étnico-racial -> PERSONAL_SENSITIVE
    cid_entities = [e for e in entities if e.entity_type == "CID_MEDICAL"]
    assert len(cid_entities) == 1
    assert cid_entities[0].matched_text == "F32.1"
    assert cid_entities[0].category == DataCategory.PERSONAL_SENSITIVE

    sensitive_entities = [e for e in entities if e.category == DataCategory.PERSONAL_SENSITIVE]
    assert len(sensitive_entities) >= 3

    # 6. Dados bancários e financeiros -> FINANCIAL_BANKING (jamais PERSONAL_SENSITIVE)
    financial_entities = [e for e in entities if e.category == DataCategory.FINANCIAL_BANKING]
    fin_types = {e.entity_type for e in financial_entities}
    assert {"BANK_AGENCY", "BANK_ACCOUNT", "PIX_KEY", "REMUNERATION"}.issubset(fin_types)
    for fe in financial_entities:
        assert fe.category == DataCategory.FINANCIAL_BANKING
        assert fe.category != DataCategory.PERSONAL_SENSITIVE

    # 7. Dependente / Menor -> MINOR_OR_DEPENDENT
    dependent_entities = [e for e in entities if e.category == DataCategory.MINOR_OR_DEPENDENT]
    assert len(dependent_entities) >= 1
    assert any("Lucas Gabriel" in d.matched_text for d in dependent_entities)

    # 8. Todas as Bounding Boxes mapeadas devem estar estritamente no intervalo normalizado [0.0, 1.0]
    for ent in entities:
        assert 0.0 <= ent.x0_norm < ent.x1_norm <= 1.0
        assert 0.0 <= ent.y0_norm < ent.y1_norm <= 1.0


def test_multiline_table_banking_wrapped_pii_and_redaction(tmp_path: Path) -> None:
    """
    Reproduz cenários reais do documento "Processo Folha Multilinha e Dados Bancarios.pdf":
    - Tabela ReportLab GRID multi-coluna com quebra de linha dentro das células;
    - CPF quebrado (`348.729.160-\\n69`);
    - Chave PIX UUID em 2/3 linhas (`9f8e7d6c-5b4a-\\n4321-8fed-cba987654321`);
    - `Conta Corrente:\\n89412-6` (somente o código bancário, sem marcar o rótulo nem "Valor Líquido");
    - Telefone `(92)\\n98177-6543` e `CID-10:\\nF84.0`;
    - Nomes compostos em 2 linhas (`Enzo Gabriel Fontes Castelo Branco`, `Terezinha\\nde Jesus Nogueira Castelo Branco`);
    - CNH, PIS/PASEP, Data de Nascimento e Endereço residencial sem `nº`;
    - Sub-retângulos por linha com `is_continuation=True` e `masked_preview=""` nas linhas extras;
    - Destruição física 100% comprovada em ambos os modos `BLACKOUT` e `PARTIAL_MASK`.
    """
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontSize = 8.5
    normal.leading = 10.5

    pdf_doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=28,
        rightMargin=28,
        topMargin=32,
        bottomMargin=32,
    )
    elements = [
        Paragraph(
            "<b>UNIVERSIDADE FEDERAL DO AMAZONAS - FOLHA DE PAGAMENTO E PROCESSO MULTILINHA</b>",
            styles["Heading4"],
        ),
        Spacer(1, 8),
        Paragraph(
            "Servidor requerente: <b>Terezinha<br/>de Jesus Nogueira Castelo Branco</b>, "
            "nascido em 22/11/1991, CNH: 04829173650, PIS/PASEP: 120.56874.18-2, "
            "residente na Avenida Djalma Batista, Condomínio Sol Nascente, Bloco B, Apto 302.",
            normal,
        ),
        Spacer(1, 10),
    ]

    table_data = [
        [
            Paragraph("<b>Servidor / Beneficiário</b>", normal),
            Paragraph("<b>CPF / Nascimento</b>", normal),
            Paragraph("<b>Dados Bancários e PIX</b>", normal),
            Paragraph("<b>Contato / CID</b>", normal),
        ],
        [
            Paragraph("Enzo Gabriel Fontes Castelo Branco", normal),
            Paragraph("348.729.160-<br/>69<br/>Data Nasc.: 14/05/1988", normal),
            Paragraph(
                "Conta Corrente:<br/>89412-6<br/>Valor Líquido: R$ 9.450,00<br/>"
                "PIX: 9f8e7d6c-5b4a-<br/>4321-8fed-cba987654321",
                normal,
            ),
            Paragraph("Telefone: (92)<br/>98177-6543<br/>CID-10:<br/>F84.0", normal),
        ],
    ]
    tbl = Table(table_data, colWidths=[115, 115, 165, 115])
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(tbl)
    pdf_doc.build(elements)
    raw_pdf_bytes = buf.getvalue()

    src_path = tmp_path / "multilinha_src.pdf"
    src_path.write_bytes(raw_pdf_bytes)

    doc = fitz.open(str(src_path))
    try:
        ext_page = extract_single_page(doc[0], page_number=1, render_webp=False)
    finally:
        doc.close()

    entities = detect_pii_in_pages([ext_page], session_salt="test_multiline_salt")
    all_matched_norm = [" ".join(e.matched_text.split()) for e in entities]

    # Agrupa sub-retângulos por (entity_type, group_id) para validar texto completo reconstruído
    grouped_texts: dict[tuple[str, str], list] = {}
    for e in entities:
        grouped_texts.setdefault((e.entity_type, e.group_id), []).append(e)

    by_type_joined: dict[str, list[str]] = {}
    for (etype, _), items in grouped_texts.items():
        joined = " ".join(" ".join(it.matched_text.split()) for it in items)
        by_type_joined.setdefault(etype, []).append(joined)

    # 1. Entidades com quebra de linha detectadas e agrupadas por group_id
    assert any("348.729.160-" in v and "69" in v for v in by_type_joined.get("CPF", []))
    assert any("9f8e7d6c" in v and "cba987654321" in v for v in by_type_joined.get("PIX_KEY", []))
    assert any("89412-6" in v for v in by_type_joined.get("BANK_ACCOUNT", []))
    assert any("98177-6543" in v for v in by_type_joined.get("PHONE", []))
    assert any("F84.0" in v for v in by_type_joined.get("CID_MEDICAL", []))
    assert any("04829173650" in v for v in by_type_joined.get("CNH", []))
    assert any("120.56874.18-2" in v for v in by_type_joined.get("PIS_PASEP", []))
    assert any("14/05/1988" in v for v in by_type_joined.get("BIRTH_DATE", []))
    assert any("22/11/1991" in v for v in by_type_joined.get("BIRTH_DATE", []))
    assert any("Avenida" in v and "Djalma Batista" in v for v in by_type_joined.get("ADDRESS", []))

    # Verifica continuação em branco na 2ª linha de entidades quebradas (ex.: CPF, PIX) e máscara CNH
    cpf_group = next(items for (etype, _), items in grouped_texts.items() if etype == "CPF")
    assert len(cpf_group) == 2
    assert cpf_group[0].is_continuation is False
    assert cpf_group[0].masked_preview == "***.729.160-**"
    assert cpf_group[1].is_continuation is True
    assert cpf_group[1].masked_preview == ""

    cnh_entity = next(e for e in entities if e.entity_type == "CNH")
    assert cnh_entity.masked_preview == "***2917****"

    pix_group = next(items for (etype, _), items in grouped_texts.items() if etype == "PIX_KEY")
    assert len(pix_group) >= 2
    assert pix_group[0].is_continuation is False
    assert pix_group[0].masked_preview == "****-****-4321"
    assert pix_group[1].is_continuation is True
    assert pix_group[1].masked_preview == ""

    # 2. Nomes compostos completos (incluindo segunda linha) detectados e sem falsos positivos administrativos
    person_names = by_type_joined.get("PERSON_NAME", [])
    assert any("Enzo" in n and "Branco" in n for n in person_names), f"Nomes detectados: {person_names}"
    assert any("Terezinha" in n and "Branco" in n for n in person_names), f"Nomes detectados: {person_names}"
    for text in all_matched_norm:
        assert "Conta Corrente" not in text
        assert "Valor Líquido" not in text

    # 3. Todas as sub-caixas devem ser compactas por linha (nenhuma tarja gigante cobrindo parágrafo)
    for ent in entities:
        assert (ent.y1_norm - ent.y0_norm) < 0.06, f"Caixa vertical excessiva em {ent}"

    # 4. Aplica BLACKOUT e depois PARTIAL_MASK a partir do mesmo original e valida destruição 100%
    for mode in (RedactionActionMode.BLACKOUT, RedactionActionMode.PARTIAL_MASK):
        raw_out = tmp_path / f"raw_{mode.value}.pdf"
        out_path = tmp_path / f"out_{mode.value}.pdf"
        specs = [
            RedactionBoxSpec(
                page_number=e.page_number,
                x0_norm=e.x0_norm,
                y0_norm=e.y0_norm,
                x1_norm=e.x1_norm,
                y1_norm=e.y1_norm,
                action_mode=mode,
                masked_preview=e.masked_preview,
                entity_type=e.entity_type,
                expected_text=e.matched_text,
            )
            for e in entities
        ]
        apply_true_redactions_to_pdf(
            original_pdf_path=src_path,
            boxes=specs,
            output_pdf_path=raw_out,
        )
        sanitize_pdf_structure(raw_out, out_path)
        val_res = validate_redacted_pdf_surgically(
            redacted_pdf_path=out_path,
            boxes=specs,
        )
        assert val_res.passed is True

        check_doc = fitz.open(str(out_path))
        try:
            full_txt = "\n".join(p.get_text("text") for p in check_doc)
            for forbidden in (
                "348.729.160-",
                "cba987654321",
                "89412-6",
                "98177-6543",
                "F84.0",
                "04829173650",
                "120.56874.18-2",
                "14/05/1988",
                "22/11/1991",
                "Castelo Branco",
                "Djalma Batista",
            ):
                assert forbidden not in full_txt, f"[{mode.value}] Sobrou texto proibido: {forbidden}"
            if mode == RedactionActionMode.PARTIAL_MASK:
                assert "***.729.160-**" in full_txt
                assert "****-****-4321" in full_txt
        finally:
            check_doc.close()


def _build_scenario03_pdf(dest_path: Path) -> None:
    """Gera PDF sintético reproduzindo o Cenário de Teste 03 (PROEG Bolsas em 3 páginas)."""
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontSize = 9.0
    normal.leading = 11.5

    def _draw_footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        # Região inferior (> 93% da altura A4: y_pdf < 55 => y0_norm > 0.93)
        canvas.drawString(
            36,
            24,
            f"RELATÓRIO OFICIAL - FOLHA DE BOLSAS - PÁGINA {canvas.getPageNumber()} DE 3",
        )
        canvas.restoreState()

    pdf_doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=30,
        rightMargin=30,
        topMargin=36,
        bottomMargin=64,
    )

    p1_table = Table(
        [
            [
                Paragraph("<b>Beneficiário / Discente</b>", normal),
                Paragraph("<b>Documentos</b>", normal),
                Paragraph("<b>Dados Bancários / Bolsa</b>", normal),
                Paragraph("<b>Contato / Saúde</b>", normal),
            ],
            [
                Paragraph("Mateus<br/>Henrique Silva<br/>Cavalcante", normal),
                Paragraph("CPF: 348.729.160-69<br/>Matrícula: 22150198", normal),
                Paragraph("Ag: 3563-7<br/>Op. 013<br/>Conta: 48921-3<br/>Bolsa: R$<br/>1.400,00", normal),
                Paragraph("PIX: +5592984310092<br/>CID-<br/>10: H90.3", normal),
            ],
            [
                Paragraph("Gabrielly<br/>Tupinambá de<br/>Souza", normal),
                Paragraph("Inscrição: 99810198<br/>Título de Eleitor:<br/>1234 5678 2291", normal),
                Paragraph("Ag: 0421-9<br/>Conta: 10293-4<br/>Bolsa: R$<br/>1.200,00", normal),
                Paragraph("Atendimento prioritário", normal),
            ],
        ],
        colWidths=[125, 135, 140, 125],
    )
    p1_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    p2_table = Table(
        [
            [
                Paragraph("<b>Discente</b>", normal),
                Paragraph("<b>Inscrição / CPF</b>", normal),
                Paragraph("<b>Observação Administrativa</b>", normal),
            ],
            [
                Paragraph("Gabrielly Tupinambá de Souza", normal),
                Paragraph("Inscrição: 99810198", normal),
                Paragraph("Renovação semestral conferida", normal),
            ],
        ],
        colWidths=[180, 140, 205],
    )
    p2_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F8FAFC")),
            ]
        )
    )

    elements = [
        Paragraph(
            "<b>CENÁRIO DE TESTE 03 — TABELAS DENSAS, MÚLTIPLAS PÁGINAS E REPETIÇÕES DE DADOS ACADÊMICOS</b>",
            styles["Heading4"],
        ),
        Paragraph(
            "UNIVERSIDADE FEDERAL DO AMAZONAS — PRÓ-REITORIA DE ENSINO DE GRADUAÇÃO — PROEG",
            normal,
        ),
        Spacer(1, 6),
        Paragraph(
            "Processo Administrativo Geral SEI nº 23105.009800/2026-01 (referência normativa). "
            "Processo Individual SEI nº 23105.088101/2026-19 referente ao pagamento de bolsas.",
            normal,
        ),
        Spacer(1, 10),
        p1_table,
        PageBreak(),
        Paragraph("<b>QUADRO COMPLEMENTAR DE BOLSAS — PÁGINA 2</b>", styles["Heading4"]),
        Spacer(1, 8),
        p2_table,
        PageBreak(),
        Paragraph("<b>PARECER SOCIAL E CLÍNICO — PÁGINA 3</b>", styles["Heading4"]),
        Spacer(1, 8),
        Paragraph(
            "A discente Gabrielly Tupinambá de Souza apresentou documentação indicando "
            "Surdez Bilateral Neurosensorial, autodeclarada Indígena pertencente ao Povo Tukano, "
            "enquadrada como Pessoa com Deficiência (PCD), com registro de dependente em Acompanhamento TEA.",
            normal,
        ),
    ]

    pdf_doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    dest_path.write_bytes(buf.getvalue())


def test_scenario03_proeg_bolsas_all_six_improvements(tmp_path: Path) -> None:
    """
    Valida integralmente as 6 melhorias do Cenário de Teste 03:
    1. Fonte do Mascaramento Parcial (`insert_text` com `hebo` >= 10.0pt e alinhamento de linha de base);
    2. Regex multilinhas (`R$\\n1.400,00`, `CID-\\n10: H90.3`, `+5592984310092`, `Ag:`, `Op.`, Título Eleitor 12 dígitos, Matrícula/Inscrição `***0198*`);
    3. Nomes compostos em células com quebra de linha (`Mateus\\nHenrique Silva\\nCavalcante` inteiro desde o 1º nome) e propagação cruzada (`Página 3 -> Páginas 1 e 2` com `group_id` unificado);
    4. Dados sensíveis clínicos/étnicos (`Povo Tukano`, `Autodeclarada Indígena`, `Surdez Bilateral Neurosensorial`, `Pessoa com Deficiência (PCD)`, `Acompanhamento TEA`) em `PERSONAL_SENSITIVE`;
    5. Regras contextuais SEI (`Geral` descartado vs. `Individual` retido);
    6. Blocklist de títulos/cabeçalhos em caixa alta e filtro da zona de rodapé (`y0_norm > 0.93`).
    """
    src_path = tmp_path / "scenario03_proeg.pdf"
    _build_scenario03_pdf(src_path)

    doc = fitz.open(str(src_path))
    try:
        ext_pages = [
            extract_single_page(doc[i], page_number=i + 1, render_webp=False)
            for i in range(len(doc))
        ]
    finally:
        doc.close()

    entities = detect_pii_in_pages(ext_pages, session_salt="scenario03_salt")

    grouped: dict[tuple[int, str, str], list] = {}
    for e in entities:
        grouped.setdefault((e.page_number, e.entity_type, e.group_id), []).append(e)

    joined_by_type: dict[str, list[str]] = {}
    for (_p, etype, _gid), items in grouped.items():
        full_txt = " ".join(" ".join(it.matched_text.split()) for it in items)
        joined_by_type.setdefault(etype, []).append(full_txt)

    # Item 2: Regex multilinhas e padrões faltantes
    assert any("1.400,00" in v for v in joined_by_type.get("REMUNERATION", [])), joined_by_type
    assert any("H90.3" in v for v in joined_by_type.get("CID_MEDICAL", [])), joined_by_type
    phone_items = [e for e in entities if e.entity_type == "PHONE"]
    assert any("+5592984310092" in e.matched_text or "984310092" in e.matched_text for e in phone_items)
    assert any(e.masked_preview == "(92) *****-0092" for e in phone_items), [e.masked_preview for e in phone_items]
    assert any("3563-7" in v for v in joined_by_type.get("BANK_AGENCY", [])), joined_by_type
    assert any("013" in v for v in joined_by_type.get("BANK_OPERATION", [])), joined_by_type
    assert any("1234 5678 2291" in v for v in joined_by_type.get("TITULO_ELEITOR", [])), joined_by_type
    matriculas = [e for e in entities if e.entity_type == "MATRICULA_UFAM"]
    assert any(e.matched_text == "22150198" and e.masked_preview == "***0198*" for e in matriculas)
    assert any(e.matched_text == "99810198" and e.masked_preview == "***0198*" for e in matriculas)

    # Item 3: Mateus completo desde a 1ª palavra + Gabrielly propagada nas 3 páginas com mesmo group_id
    person_groups_by_page: dict[int, list[tuple[str, str]]] = {}
    for (p_num, etype, gid), items in grouped.items():
        if etype == "PERSON_NAME":
            full_txt = " ".join(" ".join(it.matched_text.split()) for it in items)
            person_groups_by_page.setdefault(p_num, []).append((gid, full_txt))

    p1_names = person_groups_by_page.get(1, [])
    assert any(
        "Mateus" in txt and "Henrique" in txt and "Cavalcante" in txt and "Saúde" not in txt
        for _gid, txt in p1_names
    ), f"Esperava Mateus completo (sem Saúde) na Pág 1; obtido: {p1_names}"

    gab_gids = {
        p_num: gid
        for p_num in (1, 2, 3)
        for gid, txt in person_groups_by_page.get(p_num, [])
        if "Gabrielly" in txt and "Souza" in txt
    }
    assert set(gab_gids.keys()) == {1, 2, 3}, f"Propagação incompleta entre páginas: {person_groups_by_page}"
    assert len(set(gab_gids.values())) == 1, f"group_id divergente entre páginas: {gab_gids}"

    # Item 4: Expressões sensíveis clínicas/étnicas -> PERSONAL_SENSITIVE
    sensitive_all = " | ".join(
        " ".join(e.matched_text.split())
        for e in entities
        if e.category == DataCategory.PERSONAL_SENSITIVE
    )
    for expected_term in ("Surdez Bilateral Neurosensorial", "Tukano", "PCD", "TEA", "Indígena"):
        assert expected_term.lower() in sensitive_all.lower(), f"Ausente em PERSONAL_SENSITIVE: {expected_term} ({sensitive_all})"

    # Item 5: Regras contextuais SEI
    sei_values = set(joined_by_type.get("PROCESSO_SEI", []))
    assert "23105.088101/2026-19" in sei_values
    assert "23105.009800/2026-01" not in sei_values

    # Item 6: Blocklist de títulos em caixa alta e rodapé (y0_norm > 0.93)
    for e in entities:
        assert e.y0_norm <= 0.93, f"Elemento em rodapé não ignorado: {e}"
        upper_txt = e.matched_text.upper()
        for blocked in ("TABELAS", "REPETIÇÕES", "CENÁRIO", "PRÓ-REITORIA", "RELATÓRIO OFICIAL"):
            assert blocked not in upper_txt, f"Falso positivo de título/cabeçalho detectado: {e}"

    # Item 1: Mascaramento Parcial com fonte legível >= 10.0pt via insert_text (não encolhido dentro do redact annot)
    raw_out = tmp_path / "scenario03_raw_mask.pdf"
    out_path = tmp_path / "scenario03_masked.pdf"
    specs = [
        RedactionBoxSpec(
            page_number=e.page_number,
            x0_norm=e.x0_norm,
            y0_norm=e.y0_norm,
            x1_norm=e.x1_norm,
            y1_norm=e.y1_norm,
            action_mode=RedactionActionMode.PARTIAL_MASK,
            masked_preview=e.masked_preview,
            entity_type=e.entity_type,
            expected_text=e.matched_text,
        )
        for e in entities
    ]
    apply_true_redactions_to_pdf(original_pdf_path=src_path, boxes=specs, output_pdf_path=raw_out)
    sanitize_pdf_structure(raw_out, out_path)
    val_res = validate_redacted_pdf_surgically(redacted_pdf_path=out_path, boxes=specs)
    assert val_res.passed is True

    masked_doc = fitz.open(str(out_path))
    try:
        p1_dict = masked_doc[0].get_text("dict")
        masked_span_sizes: list[tuple[str, float]] = []
        for blk in p1_dict.get("blocks", []):
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    stext = str(span.get("text", "")).strip()
                    if "***" in stext:
                        masked_span_sizes.append((stext, float(span.get("size", 0.0))))

        assert any("***.729.160-**" in t and sz >= 10.0 for t, sz in masked_span_sizes), masked_span_sizes
        assert any("***0198*" in t and sz >= 10.0 for t, sz in masked_span_sizes), masked_span_sizes
        assert any("**** 5678 22**" in t and sz >= 10.0 for t, sz in masked_span_sizes), masked_span_sizes
        full_masked_txt = "\n".join(p.get_text("text") for p in masked_doc)
        assert "348.729.160-69" not in full_masked_txt
        assert "22150198" not in full_masked_txt
        assert "Tupinambá" not in full_masked_txt
        assert "Mateus H. S. C. ***" in full_masked_txt
        assert "Gabrielly T. S. ***" in full_masked_txt
    finally:
        masked_doc.close()


def test_progressive_analyze_document_task_propagates_names_from_page3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Garante que mesmo no fluxo progressivo (`defer_remaining_pages=True`, Pág. 1 no upload e Págs. 2..N no worker),
    nomes completos confirmados na Página 3 propagam para as Páginas 1 e 2 mantendo `group_id` único.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)
    job_id = "job-scenario03-prog-test"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job_dir / "original.pdf"
    _build_scenario03_pdf(pdf_path)

    state: dict = {
        "job_id": job_id,
        "status": "ANALYZING",
        "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
        "pages": {},
        "occurrences": [],
        "verbose_logs": [],
        "processed_pages": 0,
        "total_pages": 3,
    }
    doc = fitz.open(str(pdf_path))
    try:
        process_single_page_into_state(
            doc=doc,
            page_num=1,
            job_id=job_id,
            state=state,
            base_temp_dir=tmp_path,
            flush_spool_to_disk=True,
        )
    finally:
        doc.close()

    store_job_transient_state(job_id, state)

    final_state = analyze_document_task(job_id, start_page=2)
    assert final_state["status"] == "AWAITING_REVIEW"

    gab_gids_by_page: dict[int, set[str]] = {}
    for p_str in ("1", "2", "3"):
        p_occs = final_state["pages"][p_str]["occurrences"]
        by_gid: dict[str, list[str]] = {}
        for o in p_occs:
            if o["entity_type"] == "PERSON_NAME":
                by_gid.setdefault(o["group_id"], []).append(str(o.get("preview_text", "")))
        for gid, parts in by_gid.items():
            joined = " ".join(parts)
            if "Gabrielly" in joined and "Souza" in joined:
                gab_gids_by_page.setdefault(int(p_str), set()).add(gid)

    assert set(gab_gids_by_page.keys()) == {1, 2, 3}, gab_gids_by_page
    unified_gids = set().union(*gab_gids_by_page.values())
    assert len(unified_gids) == 1, gab_gids_by_page


def test_ten_textual_pages_pii_analysis_under_500ms() -> None:
    """
    Critério de Aceite de Performance da Etapa 3:
    Comprova que a análise de 10 páginas textuais administrativas da UFAM ocorre em menos de 500ms.
    """
    doc = fitz.open()
    for page_idx in range(10):
        page = doc.new_page(width=595, height=842)
        for row in range(12):
            y = 50 + row * 55
            page.insert_text(
                fitz.Point(40, y),
                f"UNIVERSIDADE FEDERAL DO AMAZONAS - Portaria N {page_idx * 10 + row}/2026 - Processo 23105.000100/2026-11",
                fontsize=10,
            )
            page.insert_text(
                fitz.Point(40, y + 16),
                f"Servidor(a): Maria Fernanda Souza - CPF: {VALID_CPF_1} - SIAPE: 1928374 - CID: M54.5",
                fontsize=10,
            )
            page.insert_text(
                fitz.Point(40, y + 32),
                "Dados Bancarios: Agencia: 3563-7 | Conta Corrente: 123456-8 | Telefone: (92) 99112-3344",
                fontsize=10,
            )

    try:
        extracted_pages = [
            extract_single_page(doc[i], page_number=i + 1, render_webp=False) for i in range(10)
        ]
    finally:
        doc.close()

    t0 = time.perf_counter()
    detected_entities = detect_pii_in_pages(extracted_pages, session_salt="perf_10_pages")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert len(detected_entities) >= 600
    assert elapsed_ms < 500.0, f"Análise de 10 páginas textuais levou {elapsed_ms:.2f}ms (limite: 500ms)"


def _build_scanned_ocr_five_fixes_pdf(dest_path: Path) -> None:
    """Gera PDF 100% escaneado (200 DPI rasterizado sem camada de texto) reproduzindo os 5 problemas de OCR."""
    text_doc = fitz.open()
    # Página 1
    p1 = text_doc.new_page(width=595, height=842)
    p1.insert_text(fitz.Point(40, 48), "DOCUMENTO FÍSICO DIGITALIZADO (200 DPI)", fontsize=12)
    p1.insert_text(fitz.Point(40, 68), "UNIVERSIDADE FEDERAL DO AMAZONAS - ASSENTAMENTO FUNCIONAL", fontsize=10)
    p1.insert_text(fitz.Point(40, 92), "Processo Individual SEI nº 23105.019482/2014-77", fontsize=10)

    # Moldura 3x4 na direita superior com silhueta e legenda
    photo_rect = fitz.Rect(465, 50, 550, 163)  # 85x113pt (~3x4cm, aspect 0.752, w_norm 0.143)
    p1.draw_rect(photo_rect, color=(0, 0, 0), width=1.6)
    p1.draw_circle(fitz.Point(507, 92), 18, color=(0.35, 0.35, 0.35), fill=(0.55, 0.55, 0.55))
    p1.draw_rect(fitz.Rect(486, 114, 529, 140), color=(0.35, 0.35, 0.35), fill=(0.45, 0.45, 0.45))
    p1.insert_text(fitz.Point(473, 156), "FOTO 3X4 ESCANEADA", fontsize=6.8)

    p1.insert_text(fitz.Point(40, 130), "Servidor(a): ROBERTO CARLOS NOGUEIRA DA SILVA", fontsize=10)
    p1.insert_text(fitz.Point(40, 152), "Filiação: Antônio Nogueira da Silva e Francisca Lima da Silva", fontsize=10)
    p1.insert_text(fitz.Point(40, 174), "DA SILVA, portador do CPF 528.419.630-98, necessita de 30 dias de afastamento.", fontsize=10)
    p1.insert_text(fitz.Point(40, 198), "CNH: 04829173650 | PIS/PASEP: 120.56874.18-2 | Data de Nascimento: 12/05/1982", fontsize=10)
    p1.insert_text(fitz.Point(40, 222), "Telefone: (92) 99184-7721 | Ag: 1408 | Conta: 48921-3", fontsize=10)
    p1.insert_text(fitz.Point(40, 246), "Endereço: Av. Tefé, nº 1840, Bairro Japiim, Manaus/AM, CEP 69078-000", fontsize=10)

    p1.insert_text(fitz.Point(40, 290), "SEÇÃO 3 - DADOS PESSOAIS SENSÍVEIS", fontsize=10)
    p1.insert_text(fitz.Point(40, 314), "Diagnóstico: Lombociatalgia Aguda (CID-10: M54.4) e Hipertensão Arterial (CID-10: 110)", fontsize=10)
    p1.insert_text(fitz.Point(40, 338), "Filiação Sindical: Filiado ao SINTEST-AM, Ficha Sindical nº 4421", fontsize=10)
    p1.insert_text(fitz.Point(40, 362), "Autodeclaração Étnico-Racial: Parda", fontsize=10)
    p1.insert_text(fitz.Point(40, 386), "Biometria ID: BIO-AM-774102", fontsize=10)

    # Página 2 com forma abreviada + dependente
    p2 = text_doc.new_page(width=595, height=842)
    p2.insert_text(fitz.Point(40, 60), "FICHA COMPLEMENTAR - LOTE 08/2026 - ARQUIVO E PROTOCOLO", fontsize=10)
    p2.insert_text(fitz.Point(40, 100), "Titular: Roberto Carlos N. da Silva (Servidor)", fontsize=10)
    p2.insert_text(fitz.Point(40, 130), "Dependente: Beatriz Lima N. da Silva (Filha)", fontsize=10)

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


def test_scanned_ocr_five_fixes_coordinates_fields_photo_and_blocklist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Valida os 5 ajustes críticos para documentos 100% escaneados sem camada nativa de texto:
    1. Precisão proporcional de palavras OCR sem rótulo (`Filiação:`, `Endereço:`) e sem invadir `necessita`;
    2. Nomes em CAIXA ALTA (`ROBERTO CARLOS NOGUEIRA DA SILVA`) e abreviados (`Roberto Carlos N. da Silva`) com `group_id` unificado;
    3. Detecção de SEI nominal, CNH, PIS/PASEP, Nascimento, Telefone, Endereço completo, Agência `Ag:`,
       Patologias+CID (inclusive `CID-10: 110`), Sindicato, Autodeclaração Étnico-Racial e Biometria ID;
    4. Detecção automática da moldura/foto 3x4 como `FACIAL_PHOTO` (`PERSONAL_SENSITIVE`, `BLACKOUT`);
    5. Blocklist de cabeçalhos digitalizados (`DOCUMENTO FÍSICO DIGITALIZADO (200 DPI)` etc.).
    """
    ml_registry.warmup(load_ocr=True, load_spacy=True)
    src_path = tmp_path / "scanned_five_fixes.pdf"
    _build_scanned_ocr_five_fixes_pdf(src_path)

    doc = fitz.open(str(src_path))
    try:
        assert not doc[0].get_text("words"), "PDF de teste deve ser 100% escaneado sem texto nativo"
        ext_pages = [
            extract_single_page(doc[i], page_number=i + 1, render_webp=False)
            for i in range(len(doc))
        ]
    finally:
        doc.close()

    assert all(p.ocr_executed for p in ext_pages)
    entities = detect_pii_in_pages(ext_pages, session_salt="ocr_5_fixes_salt")

    # 1. Nenhuma entidade deve incluir rótulos nem falsos positivos de cabeçalho
    for e in entities:
        txt_up = e.matched_text.upper()
        for blocked in ("DIGITALIZADO", "FISICO", "FÍSICO", "200 DPI", "ASSENTAMENTO", "ARQUIVO", "PROTOCOLO", "ESCANEADA"):
            assert blocked not in txt_up, f"Falso positivo de cabeçalho digitalizado: {e}"
        for lbl_prefix in ("FILIAÇÃO:", "ENDEREÇO:", "MATRÍCULA", "AUTODECLARAÇÃO ÉTNICO-RACIAL:"):
            assert not txt_up.startswith(lbl_prefix), f"Rótulo vazou para caixa de redação: {e}"

    # 2. Foto 3x4 automática (`FACIAL_PHOTO`) em PERSONAL_SENSITIVE / BLACKOUT
    photos = [e for e in entities if e.entity_type == "FACIAL_PHOTO"]
    assert len(photos) >= 1, f"Foto 3x4 não detectada em página escaneada: {[e.entity_type for e in entities]}"
    assert all(p.category == DataCategory.PERSONAL_SENSITIVE for p in photos)
    assert all(p.action_mode == RedactionActionMode.BLACKOUT for p in photos)

    # 3. Todos os campos faltantes detectados corretamente
    by_type: dict[str, list] = {}
    for e in entities:
        by_type.setdefault(e.entity_type, []).append(e)

    assert any("23105.019482" in e.matched_text for e in by_type.get("PROCESSO_SEI", [])), by_type.keys()
    assert any("528.419.630-98" in e.matched_text for e in by_type.get("CPF", [])), by_type.get("CPF")
    assert any("04829173650" in e.matched_text for e in by_type.get("CNH", [])), by_type.get("CNH")
    assert any("120.56874" in e.matched_text for e in by_type.get("PIS_PASEP", [])), by_type.get("PIS_PASEP")
    assert any("12/05/1982" in e.matched_text for e in by_type.get("BIRTH_DATE", [])), by_type.get("BIRTH_DATE")
    assert any("99184" in e.matched_text for e in by_type.get("PHONE", [])), by_type.get("PHONE")
    assert any("1408" in e.matched_text for e in by_type.get("BANK_AGENCY", [])), by_type.get("BANK_AGENCY")
    assert any("Tef" in e.matched_text and "Jap" in e.matched_text for e in by_type.get("ADDRESS", [])), by_type.get("ADDRESS")
    assert any("Lombociatalgia" in e.matched_text or "M54.4" in e.matched_text for e in by_type.get("HEALTH_DATA", []) + by_type.get("CID_MEDICAL", []))
    assert any("110" in e.matched_text or "Hipertens" in e.matched_text for e in by_type.get("HEALTH_DATA", []) + by_type.get("CID_MEDICAL", []))
    assert any("SINTEST" in e.matched_text for e in by_type.get("UNION_AFFILIATION", [])), by_type.get("UNION_AFFILIATION")
    assert any("Parda" in e.matched_text for e in by_type.get("ETHNIC_RACIAL_DECLARATION", [])), by_type.get("ETHNIC_RACIAL_DECLARATION")
    assert any("BIO-AM-774102" in e.matched_text for e in by_type.get("BIOMETRY_ID", [])), by_type.get("BIOMETRY_ID")

    # 4. Nomes em CAIXA ALTA + abreviados + filiação completa até "Silva" sem cortar
    p1_names = " | ".join(e.matched_text for e in entities if e.page_number == 1 and e.entity_type in ("PERSON_NAME", "FILIACAO"))
    assert "ROBERTO CARLOS" in p1_names.upper(), p1_names
    assert "Antônio Nogueira da Silva" in p1_names or "Nogueira da Silva" in p1_names, p1_names
    assert "Francisca Lima da Silva" in p1_names, p1_names

    p1_roberto_gids = {e.group_id for e in entities if e.page_number == 1 and "ROBERTO CARLOS" in e.matched_text.upper()}
    p2_roberto_gids = {e.group_id for e in entities if e.page_number == 2 and "ROBERTO CARLOS" in e.matched_text.upper()}
    assert p1_roberto_gids and p2_roberto_gids and p1_roberto_gids == p2_roberto_gids, (p1_roberto_gids, p2_roberto_gids)

    p2_beatriz = [e for e in entities if e.page_number == 2 and "Beat" in e.matched_text]
    assert p2_beatriz and any("Silva" in e.matched_text for e in p2_beatriz), p2_beatriz

    # 5. Precisão horizontal OCR: CPF `528.419.630-98` em PARTIAL_MASK não pode invadir a palavra adjacente `, necessita`
    cpf_hit = next(e for e in by_type["CPF"] if "528.419.630-98" in e.matched_text)
    necessita_tok = next(t for t in ext_pages[0].tokens if "necessita" in t.text.lower())
    assert cpf_hit.x1_norm <= necessita_tok.x0_norm + 0.002, (cpf_hit.x1_norm, necessita_tok.x0_norm)

    # Aplica PARTIAL_MASK nas textuais e BLACKOUT na foto e valida ausência cirúrgica + sobrevivência de "necessita"
    specs = [
        RedactionBoxSpec(
            page_number=e.page_number,
            x0_norm=e.x0_norm,
            y0_norm=e.y0_norm,
            x1_norm=e.x1_norm,
            y1_norm=e.y1_norm,
            action_mode=RedactionActionMode.BLACKOUT if e.entity_type == "FACIAL_PHOTO" else RedactionActionMode.PARTIAL_MASK,
            masked_preview=e.masked_preview,
            entity_type=e.entity_type,
            expected_text=e.matched_text,
        )
        for e in entities
    ]
    raw_out = tmp_path / "scanned_raw_masked.pdf"
    final_out = tmp_path / "scanned_final_masked.pdf"
    apply_true_redactions_to_pdf(original_pdf_path=src_path, boxes=specs, output_pdf_path=raw_out)
    sanitize_pdf_structure(raw_out, final_out)
    val_res = validate_redacted_pdf_surgically(redacted_pdf_path=final_out, boxes=specs)
    assert val_res.passed is True

    # Verifica no arquivo tarjado final que nenhum rótulo (`Autodeclaração Étnico-Racial`, `Filiação`, `Endereço`) foi apagado
    # e que a palavra adjacente "necessita" após o CPF mascarado `***.419.630-**` permanece 100% legível
    assert not any("autodeclar" in e.matched_text.lower() for e in entities), [
        e for e in entities if e.entity_type == "ETHNIC_RACIAL_DECLARATION"
    ]
    chk_doc = fitz.open(str(final_out))
    try:
        p1_chk = chk_doc[0]
        assert "***.419.630-**" in p1_chk.get_text("text")
        pw, ph = float(p1_chk.rect.width), float(p1_chk.rect.height)
        nec_clip = fitz.Rect(
            (necessita_tok.x0_norm - 0.004) * pw,
            (necessita_tok.y0_norm - 0.004) * ph,
            min(pw, (necessita_tok.x1_norm + 0.22) * pw),
            min(ph, (necessita_tok.y1_norm + 0.004) * ph),
        )
        nec_pix = p1_chk.get_pixmap(clip=nec_clip, dpi=200, alpha=False)
        import numpy as _np
        nec_arr = _np.frombuffer(nec_pix.samples, dtype=_np.uint8).reshape(nec_pix.height, nec_pix.width, nec_pix.n)
        raw_nec, _ = ml_registry.get_ocr_engine()(nec_arr)
        nec_joined = " ".join(str(item[1]) for item in (raw_nec or [])).lower()
        assert "necessita" in nec_joined, f"Palavra adjacente 'necessita' foi invadida/apagada: {nec_joined!r}"
    finally:
        chk_doc.close()

    # 6. Fluxo progressivo First-Page-First unifica group_id de nome completo + abreviado entre páginas
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)
    job_id = "job-ocr-five-fixes-prog"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    prog_pdf = job_dir / "original.pdf"
    prog_pdf.write_bytes(src_path.read_bytes())
    prog_state: dict = {
        "job_id": job_id,
        "status": "ANALYZING",
        "document_profile": DocumentProfile.RESTRICTED_PROCESS.value,
        "pages": {},
        "occurrences": [],
        "verbose_logs": [],
        "processed_pages": 0,
        "total_pages": 2,
    }
    prog_doc = fitz.open(str(prog_pdf))
    try:
        process_single_page_into_state(
            doc=prog_doc,
            page_num=1,
            job_id=job_id,
            state=prog_state,
            base_temp_dir=tmp_path,
            flush_spool_to_disk=True,
        )
    finally:
        prog_doc.close()
    store_job_transient_state(job_id, prog_state)
    final_prog_state = analyze_document_task(job_id, start_page=2)
    p1_prog_gids = {
        o["group_id"]
        for o in final_prog_state["pages"]["1"]["occurrences"]
        if o["entity_type"] == "PERSON_NAME" and "ROBERTO CARLOS" in str(o.get("preview_text", "")).upper()
    }
    p2_prog_gids = {
        o["group_id"]
        for o in final_prog_state["pages"]["2"]["occurrences"]
        if o["entity_type"] == "PERSON_NAME" and "ROBERTO CARLOS" in str(o.get("preview_text", "")).upper()
    }
    assert p1_prog_gids and p2_prog_gids and p1_prog_gids == p2_prog_gids, (p1_prog_gids, p2_prog_gids)
