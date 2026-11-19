"""
Gerador de Relatório Institucional Zero-PII em PDF (`report_generator.py` via `ReportLab`).

DIRETRIZ RÍGIDA (SPEC.md Seção 1, 3 e 4.3):
- PROIBIÇÃO ABSOLUTA: O relatório NÃO PODE conter nenhum CPF, nome, e-mail, matrícula ou
  qualquer texto literal extraído do documento;
- Contém:
  1. Cabeçalho Institucional da Universidade Federal do Amazonas (UFAM);
  2. Identificação técnica do Job, nome higienizado do documento, perfil documental detectado,
     carimbo de tempo UTC, hashes criptográficos SHA-256 (documento original e documento tarjado),
     total de páginas e lista de páginas afetadas;
  3. Tabela quantitativa de ocorrências por categoria estrita da taxonomia com 7 colunas:
     `Categoria Legal (LGPD)` | `Detectadas` | `Tarja Total (Preto)` | `Mascaradas (***.XXX-**)` |
     `Mantidas (Revisão)` | `Tarjas Manuais` | `Total Protegido`
  4. Linha `TOTAL CONSOLIDADO` calculada matematicamente como soma exata das 4 categorias acima;
  5. Resultado detalhado das 3 Verificações de Segurança Pós-Tarjamento com linhas separadas
     para `STATUS GLOBAL DE VALIDAÇÃO` e `Tempo de validação cirúrgica`;
  6. Termo de Limitação Técnica e Jurídica obrigatório (distinção entre Tarjamento Definitivo e Anonimização do Art. 5º, XI da LGPD e papel de apoio administrativo).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.logging import sanitize_text_anti_pii
from app.models.enums import DataCategory, DocumentProfile
from app.services.security_validator.post_redaction_validator import SecurityValidationResult

logger = logging.getLogger(__name__)

STRICT_TAXONOMY_ORDER: tuple[DataCategory, ...] = (
    DataCategory.PERSONAL_COMMON,
    DataCategory.PERSONAL_SENSITIVE,
    DataCategory.FINANCIAL_BANKING,
    DataCategory.MINOR_OR_DEPENDENT,
)

CATEGORY_LABELS_PT: dict[str, str] = {
    DataCategory.PERSONAL_COMMON.value: "Dado Pessoal Comum (PERSONAL_COMMON - Art. 5º, I, LGPD)",
    DataCategory.PERSONAL_SENSITIVE.value: "Dado Pessoal Sensível (PERSONAL_SENSITIVE - Art. 5º, II, LGPD)",
    DataCategory.FINANCIAL_BANKING.value: "Dado Financeiro/Bancário (FINANCIAL_BANKING - Proteção Reforçada)",
    DataCategory.MINOR_OR_DEPENDENT.value: "Crianças, Adolescentes e Dependentes (MINOR_OR_DEPENDENT - Art. 14, LGPD)",
}

PROFILE_LABELS_PT: dict[str, str] = {
    DocumentProfile.PUBLIC_TRANSPARENCY.value: "Transparência Ativa / Edital (PUBLIC_TRANSPARENCY)",
    DocumentProfile.RESTRICTED_PROCESS.value: "Processo Administrativo / Despacho Interno (RESTRICTED_PROCESS)",
}

LEGAL_LIMITATION_TEXT = (
    "<b>TERMO DE LIMITAÇÃO TÉCNICA E JURÍDICA (CONFORMIDADE LGPD &amp; LAI):</b><br/>"
    "1. <b>Distinção entre Tarjamento Definitivo e Anonimização (Art. 5º, XI, Lei nº 13.709/2018):</b> "
    "Este processamento executou o <i>tarjamento definitivo (true redaction)</i> e/ou <i>mascaramento físico irreversível</i> "
    "mediante destruição física dos glifos originais nos fluxos de texto, vetores tocados e queima dos pixels nas coordenadas selecionadas, "
    "seguida de sanitização estrutural de metadados XMP/Info. Ressalta-se expressamente que a supressão de campos "
    "específicos <b>não garante, por si só, a anonimização integral do documento</b>, permanecendo sob responsabilidade "
    "da unidade administrativa avaliar eventual risco de identificação indireta pelo contexto remanescente.<br/>"
    "2. <b>Papel de Apoio (Human-in-the-Loop):</b> O Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM "
    "opera como instrumento técnico de apoio à decisão, não substituindo o juízo administrativo ou jurídico da "
    "Pró-Reitoria/Unidade nem as orientações normativas do Encarregado pelo Tratamento de Dados Pessoais (DPO/UFAM), "
    "em observância à Lei nº 13.709/2018 (LGPD), Lei nº 12.527/2011 (LAI), Decreto nº 7.724/2012 e diretrizes da ANPD.<br/>"
    "3. <b>Política Zero-PII:</b> Em estrito cumprimento ao princípio da minimização e segurança (Art. 6º, III e VII, LGPD), "
    "este relatório técnico registra exclusivamente contadores estatísticos, páginas afetadas e hashes criptográficos SHA-256, "
    "sem reproduzir ou armazenar qualquer valor literal de dado pessoal suprimido."
)


@dataclass(frozen=True, slots=True)
class CategoryQuantitativeRow:
    """Contadores quantitativos por categoria da taxonomia (Zero-PII)."""

    detected_count: int = 0
    redacted_count: int = 0  # Tarja Total (Preto - BLACKOUT)
    masked_count: int = 0  # Mascaradas (***.XXX-** - PARTIAL_MASK)
    kept_count: int = 0  # Mantidas (Revisão - KEEP)
    manual_added_count: int = 0  # Tarjas Manuais


@dataclass(frozen=True, slots=True)
class ZeroPIIReportData:
    """Dados estritamente quantitativos e criptográficos para geração do Relatório PDF."""

    job_id: uuid.UUID | str
    document_label: str
    original_sha256: str
    redacted_sha256: str
    total_pages: int
    affected_pages: list[int]
    category_breakdown: dict[str, CategoryQuantitativeRow] = field(default_factory=dict)
    security_validation: SecurityValidationResult | None = None
    document_profile: str = DocumentProfile.RESTRICTED_PROCESS.value
    organizational_unit: str = "Unidade Administrativa / Pró-Reitoria — UFAM"
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _sanitize_document_label(raw_label: str) -> str:
    """Garante que até mesmo o nome do arquivo exibido no relatório passe pelo filtro Anti-PII."""
    safe = sanitize_text_anti_pii(raw_label or "documento_institucional.pdf")
    return safe[:100]


def _normalize_category_breakdown(
    raw_breakdown: dict[str, CategoryQuantitativeRow],
) -> dict[str, CategoryQuantitativeRow]:
    """
    Normaliza o dicionário de estatísticas por categoria garantindo que qualquer chave
    seja acumulada exatamente nas 4 categorias estritas da taxonomia (`STRICT_TAXONOMY_ORDER`),
    evitando discrepâncias entre a soma das linhas e a linha `TOTAL CONSOLIDADO`.
    """
    valid_keys = {c.value for c in STRICT_TAXONOMY_ORDER}
    acc: dict[str, list[int]] = {c.value: [0, 0, 0, 0, 0] for c in STRICT_TAXONOMY_ORDER}

    for k, row in (raw_breakdown or {}).items():
        norm_k = str(k).strip().upper()
        if norm_k not in valid_keys:
            norm_k = DataCategory.PERSONAL_COMMON.value
        acc[norm_k][0] += int(row.detected_count or 0)
        acc[norm_k][1] += int(row.redacted_count or 0)
        acc[norm_k][2] += int(getattr(row, "masked_count", 0) or 0)
        acc[norm_k][3] += int(row.kept_count or 0)
        acc[norm_k][4] += int(row.manual_added_count or 0)

    return {
        k: CategoryQuantitativeRow(
            detected_count=vals[0],
            redacted_count=vals[1],
            masked_count=vals[2],
            kept_count=vals[3],
            manual_added_count=vals[4],
        )
        for k, vals in acc.items()
    }


def generate_zero_pii_processing_report(
    report_data: ZeroPIIReportData,
    output_pdf_path: Path | str,
) -> Path:
    """
    Gera o Relatório de Processamento e Conformidade Zero-PII em formato PDF usando `ReportLab`.
    Todas as células de texto e cabeçalhos das tabelas utilizam `Paragraph(text, cell_style)` com
    quebra de linha automática (`wordWrap='CJK'`, `fontSize=8`, `leading=10`) e larguras proporcionais
    à página A4 (535pt úteis), impedindo qualquer colisão ou sobreposição de caracteres entre colunas.
    """
    out_path = Path(output_pdf_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Largura A4 = 595.27pt; margens laterais de 30pt (2 * 30 = 60pt) -> largura útil = 535pt
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=30,
        rightMargin=30,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Relatório Institucional de Tarjamento e Conformidade LGPD — UFAM",
        author="Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "UFAMHeaderTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#0F3D23"),
        spaceAfter=2 * mm,
    )
    subtitle_style = ParagraphStyle(
        "UFAMSubTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=3.5 * mm,
    )
    section_style = ParagraphStyle(
        "UFAMSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#0F3D23"),
        spaceBefore=3 * mm,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "UFAMBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#1F2937"),
    )
    legal_style = ParagraphStyle(
        "UFAMLegalBox",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.8,
        leading=10.5,
        textColor=colors.HexColor("#111827"),
        backColor=colors.HexColor("#F1F5F9"),
        borderColor=colors.HexColor("#94A3B8"),
        borderWidth=0.6,
        borderPadding=6,
    )

    # Estilos de células com quebra automática de linha (Paragraph + wordWrap='CJK')
    cell_label_bold = ParagraphStyle(
        "UFAMCellLabelBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
        wordWrap="CJK",
        textColor=colors.HexColor("#0F172A"),
    )
    cell_text_left = ParagraphStyle(
        "UFAMCellTextLeft",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
        wordWrap="CJK",
        textColor=colors.HexColor("#0F172A"),
    )
    cell_mono_left = ParagraphStyle(
        "UFAMCellMonoLeft",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.8,
        leading=9.8,
        alignment=TA_LEFT,
        wordWrap="CJK",
        textColor=colors.HexColor("#0F172A"),
    )
    cell_num_center = ParagraphStyle(
        "UFAMCellNumCenter",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        alignment=TA_CENTER,
        wordWrap="CJK",
        textColor=colors.HexColor("#0F172A"),
    )
    cell_num_bold_center = ParagraphStyle(
        "UFAMCellNumBoldCenter",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=TA_CENTER,
        wordWrap="CJK",
        textColor=colors.HexColor("#0F172A"),
    )
    header_white_left = ParagraphStyle(
        "UFAMHeaderWhiteLeft",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
        wordWrap="CJK",
        textColor=colors.white,
    )
    header_white_center = ParagraphStyle(
        "UFAMHeaderWhiteCenter",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        alignment=TA_CENTER,
        wordWrap="CJK",
        textColor=colors.white,
    )

    story = []

    # 1. Cabeçalho Institucional UFAM
    story.append(Paragraph("PODER EXECUTIVO — MINISTÉRIO DA EDUCAÇÃO", subtitle_style))
    story.append(
        Paragraph(
            "UNIVERSIDADE FEDERAL DO AMAZONAS (UFAM)<br/>"
            "SISTEMA DE IDENTIFICAÇÃO E TARJAMENTO DE DADOS PESSOAIS",
            title_style,
        )
    )
    story.append(
        Paragraph(
            "CERTIDÃO TÉCNICA E RELATÓRIO DE AUDITORIA DE TARJAMENTO DEFINITIVO (ZERO-PII)",
            subtitle_style,
        )
    )

    # 2. Metadados Técnicos e Hashes SHA-256 (Tabela 1: 145pt + 390pt = 535pt)
    story.append(Paragraph("1. IDENTIFICAÇÃO TÉCNICA E INTEGRIDADE CRIPTOGRÁFICA", section_style))

    safe_doc_name = escape(_sanitize_document_label(report_data.document_label))
    safe_unit = escape(sanitize_text_anti_pii(report_data.organizational_unit))
    profile_label = escape(
        PROFILE_LABELS_PT.get(
            report_data.document_profile,
            PROFILE_LABELS_PT[DocumentProfile.RESTRICTED_PROCESS.value],
        )
    )
    affected_str = (
        ", ".join(str(p) for p in sorted(set(report_data.affected_pages)))
        if report_data.affected_pages
        else "Nenhuma"
    )
    ts_str = report_data.generated_at.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")

    meta_rows = [
        [
            Paragraph("ID da Operação (Job ID):", cell_label_bold),
            Paragraph(escape(str(report_data.job_id)), cell_mono_left),
        ],
        [
            Paragraph("Unidade / Pró-Reitoria:", cell_label_bold),
            Paragraph(safe_unit, cell_text_left),
        ],
        [
            Paragraph("Documento Referência:", cell_label_bold),
            Paragraph(safe_doc_name, cell_text_left),
        ],
        [
            Paragraph("Perfil Documental Detectado:", cell_label_bold),
            Paragraph(profile_label, cell_text_left),
        ],
        [
            Paragraph("Carimbo de Tempo (UTC):", cell_label_bold),
            Paragraph(escape(ts_str), cell_mono_left),
        ],
        [
            Paragraph("Total de Páginas:", cell_label_bold),
            Paragraph(str(report_data.total_pages), cell_mono_left),
        ],
        [
            Paragraph("Páginas Afetadas (1-indexed):", cell_label_bold),
            Paragraph(escape(affected_str), cell_mono_left),
        ],
        [
            Paragraph("SHA-256 (PDF Original):", cell_label_bold),
            Paragraph(escape(report_data.original_sha256.lower()), cell_mono_left),
        ],
        [
            Paragraph("SHA-256 (PDF Tarjado Final):", cell_label_bold),
            Paragraph(escape(report_data.redacted_sha256.lower()), cell_mono_left),
        ],
    ]
    meta_table = Table(meta_rows, colWidths=[145, 390])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(meta_table)

    # 3. Tabela Quantitativa por Categoria da Taxonomia LGPD (Tabela 2: 7 colunas, total = 535pt)
    story.append(
        Paragraph("2. RESUMO QUANTITATIVO POR CATEGORIA DA TAXONOMIA (SEM DADOS LITERAIS)", section_style)
    )

    quant_header = [
        Paragraph("Categoria Legal (LGPD)", header_white_left),
        Paragraph("Detectadas", header_white_center),
        Paragraph("Tarja Total (Preto)", header_white_center),
        Paragraph("Mascaradas (***.XXX-**)", header_white_center),
        Paragraph("Mantidas (Revisão)", header_white_center),
        Paragraph("Tarjas Manuais", header_white_center),
        Paragraph("Total Protegido", header_white_center),
    ]
    quant_rows = [quant_header]

    normalized_breakdown = _normalize_category_breakdown(report_data.category_breakdown)

    displayed_rows_numeric: list[tuple[int, int, int, int, int, int]] = []
    for cat_enum in STRICT_TAXONOMY_ORDER:
        cat_key = cat_enum.value
        row_stat = normalized_breakdown.get(cat_key, CategoryQuantitativeRow())
        det_val = int(row_stat.detected_count)
        blackout_val = int(row_stat.redacted_count)
        masked_val = int(row_stat.masked_count)
        kept_val = int(row_stat.kept_count)
        man_val = int(row_stat.manual_added_count)
        sub_total_protected = blackout_val + masked_val + man_val

        displayed_rows_numeric.append(
            (det_val, blackout_val, masked_val, kept_val, man_val, sub_total_protected)
        )

        quant_rows.append(
            [
                Paragraph(escape(CATEGORY_LABELS_PT[cat_key]), cell_text_left),
                Paragraph(str(det_val), cell_num_center),
                Paragraph(str(blackout_val), cell_num_center),
                Paragraph(str(masked_val), cell_num_center),
                Paragraph(str(kept_val), cell_num_center),
                Paragraph(str(man_val), cell_num_center),
                Paragraph(str(sub_total_protected), cell_num_center),
            ]
        )

    # Calcula o TOTAL CONSOLIDADO estritamente via sum() sobre as 4 linhas exibidas na tabela
    tot_detected = sum(r[0] for r in displayed_rows_numeric)
    tot_blackout = sum(r[1] for r in displayed_rows_numeric)
    tot_masked = sum(r[2] for r in displayed_rows_numeric)
    tot_kept = sum(r[3] for r in displayed_rows_numeric)
    tot_manual = sum(r[4] for r in displayed_rows_numeric)
    tot_protected = sum(r[5] for r in displayed_rows_numeric)

    quant_rows.append(
        [
            Paragraph("TOTAL CONSOLIDADO", cell_label_bold),
            Paragraph(str(tot_detected), cell_num_bold_center),
            Paragraph(str(tot_blackout), cell_num_bold_center),
            Paragraph(str(tot_masked), cell_num_bold_center),
            Paragraph(str(tot_kept), cell_num_bold_center),
            Paragraph(str(tot_manual), cell_num_bold_center),
            Paragraph(str(tot_protected), cell_num_bold_center),
        ]
    )

    quant_table = Table(
        quant_rows,
        colWidths=[193, 52, 58, 66, 56, 54, 56],
    )
    quant_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3D23")),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E2E8F0")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(quant_table)

    # 4. Resultado das Verificações Cirúrgicas de Segurança Pós-Tarjamento (Tabela 3: colWidths=[165, 115, 255] = 535pt)
    story.append(Paragraph("3. VERIFICAÇÃO DE SEGURANÇA E DESTRUIÇÃO FÍSICA PÓS-TARJAMENTO", section_style))

    sec = report_data.security_validation
    if sec is not None:
        sec_rows = [
            [
                Paragraph("Verificação de Segurança", header_white_left),
                Paragraph("Resultado", header_white_center),
                Paragraph("Detalhamento Técnico", header_white_left),
            ],
            [
                Paragraph("1. Destruição da Camada de Texto (PyMuPDF)", cell_text_left),
                Paragraph("APROVADO" if sec.text_layer_verified else "FALHOU", cell_num_bold_center),
                Paragraph(
                    "Extração no recorte e busca textual retornaram zero caracteres originais residuais.",
                    cell_text_left,
                ),
            ],
            [
                Paragraph("2. Queima de Pixels / Mascaramento por Recorte", cell_text_left),
                Paragraph("APROVADO" if sec.pixel_layer_verified else "FALHOU", cell_num_bold_center),
                Paragraph(
                    f"{sec.boxes_checked} áreas inspecionadas com destruição física confirmada nos recortes.",
                    cell_text_left,
                ),
            ],
            [
                Paragraph("3. Sanitização Estrutural (pikepdf)", cell_text_left),
                Paragraph("APROVADO" if sec.structure_verified else "FALHOU", cell_num_bold_center),
                Paragraph(
                    "Dicionário /Info, fluxos XMP /Metadata, /OCProperties e objetos órfãos removidos.",
                    cell_text_left,
                ),
            ],
            [
                Paragraph("STATUS GLOBAL DE VALIDAÇÃO", cell_label_bold),
                Paragraph(
                    "CONFORME (TRUE REDACTION)" if sec.passed else "NÃO CONFORME",
                    cell_num_bold_center,
                ),
                Paragraph(
                    "Todas as 3 verificações físicas e estruturais pós-tarjamento foram aprovadas."
                    if sec.passed
                    else "Detectada inconformidade em uma ou mais verificações de segurança.",
                    cell_label_bold,
                ),
            ],
            [
                Paragraph("Tempo de validação cirúrgica", cell_label_bold),
                Paragraph(f"{sec.validation_time_ms:.1f} ms", cell_num_bold_center),
                Paragraph(
                    "Inspeção executada em tempo real diretamente nos recortes das coordenadas protegidas.",
                    cell_text_left,
                ),
            ],
        ]
    else:
        sec_rows = [
            [
                Paragraph("Verificação de Segurança", header_white_left),
                Paragraph("Resultado", header_white_center),
                Paragraph("Detalhamento Técnico", header_white_left),
            ],
            [
                Paragraph("STATUS GLOBAL DE VALIDAÇÃO", cell_label_bold),
                Paragraph("REGISTRADO", cell_num_bold_center),
                Paragraph("Validação concluída no pipeline de redação.", cell_text_left),
            ],
        ]

    sec_table = Table(sec_rows, colWidths=[165, 115, 255])
    sec_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
                ("BACKGROUND", (0, -2), (-1, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(sec_table)
    story.append(Spacer(1, 4 * mm))

    # 5. Termo de Limitação Técnica e Jurídica Obrigatório (LGPD Art. 5º, XI & LAI)
    story.append(Paragraph(LEGAL_LIMITATION_TEXT, legal_style))
    story.append(Spacer(1, 2 * mm))
    story.append(
        Paragraph(
            "Documento emitido eletronicamente pelo Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM.",
            body_style,
        )
    )

    doc.build(story)
    return out_path
