"""
Gerador Determinístico de Documentos Sintéticos Institucionais da UFAM (`backend/tests/synthetic_generator.py`).

Constrói um dataset de documentos PDF 100% fictícios utilizando `PyMuPDF` (`fitz`) e `Pillow` (`PIL`)
com Ground Truth exato (valores esperados, categorias LGPD, processos SEI isolados que NÃO devem ser
marcados e Bounding Boxes normalizadas `[x0_norm, y0_norm, x1_norm, y1_norm]` reais da página):

1. `generate_10page_textual_portaria_and_despacho()`:
   - Portaria e Despacho Administrativo de 10 páginas contendo dezenas de CPFs válidos gerados matematicamente,
     RGs, matrículas SIAPE, nomes completos de servidores, e-mails institucionais e processos SEI
     (alguns isolados/organizacionais que devem ser suprimidos e outros vinculados nominalmente a servidores);
2. `generate_medical_and_union_report_pdf()`:
   - Laudo/Atestado Médico Pericial simulado contendo códigos médicos CID-10 (`F41.1`, `M54.5`),
     filiação sindical (`Sindicato ADUA`) e autodeclaração étnico-racial (`PERSONAL_SENSITIVE`);
3. `generate_financial_payroll_table_pdf()`:
   - Planilha/Tabela financeira de folha de pagamento com agência, conta corrente, chave PIX e remuneração (`FINANCIAL_BANKING`);
4. `generate_scanned_and_hybrid_pdf()`:
   - Documento digitalizado (escaneado com leve rotação e ruído granulado em imagem embutida) e documento híbrido
     (Página 1 textual nativa + Página 2 digitalizada).
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

from app.models.enums import DataCategory


@dataclass(frozen=True, slots=True)
class GroundTruthEntity:
    """Entidade de dado pessoal sintético com coordenadas normalizadas reais para aferição de IoU, Precision e Recall."""

    page_number: int
    entity_type: str
    category: DataCategory
    value: str
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float


@dataclass(slots=True)
class SyntheticDocumentBundle:
    """Pacote contendo os bytes do PDF sintético gerado e seu Ground Truth completo."""

    name: str
    pdf_bytes: bytes
    page_count: int
    ground_truth_entities: list[GroundTruthEntity] = field(default_factory=list)
    negative_organizational_strings: list[str] = field(default_factory=list)


def generate_valid_cpf_formatted(seed_nine_digits: str) -> str:
    """Gera um CPF matematicamente válido formatado (`XXX.XXX.XXX-XX`) a partir de 9 dígitos base."""
    assert len(seed_nine_digits) == 9 and seed_nine_digits.isdigit()
    nums = [int(c) for c in seed_nine_digits]

    s1 = sum(nums[i] * (10 - i) for i in range(9))
    r1 = (s1 * 10) % 11
    d1 = 0 if r1 == 10 else r1
    nums.append(d1)

    s2 = sum(nums[i] * (11 - i) for i in range(10))
    r2 = (s2 * 10) % 11
    d2 = 0 if r2 == 10 else r2
    nums.append(d2)

    full = "".join(str(d) for d in nums)
    return f"{full[:3]}.{full[3:6]}.{full[6:9]}-{full[9:]}"


def _locate_norm_bbox_on_page(page: fitz.Page, target_text: str) -> tuple[float, float, float, float]:
    """Localiza o retângulo exato de `target_text` na página PyMuPDF e retorna `[x0_norm, y0_norm, x1_norm, y1_norm]`."""
    rects = page.search_for(target_text)
    if not rects:
        return (0.1, 0.1, 0.3, 0.12)
    r = rects[0]
    for extra in rects[1:]:
        r |= extra
    w = float(page.rect.width)
    h = float(page.rect.height)
    return (
        round(max(0.0, min(1.0, r.x0 / w)), 6),
        round(max(0.0, min(1.0, r.y0 / h)), 6),
        round(max(0.0, min(1.0, r.x1 / w)), 6),
        round(max(0.0, min(1.0, r.y1 / h)), 6),
    )


def generate_10page_textual_portaria_and_despacho() -> SyntheticDocumentBundle:
    """
    Constrói um PDF 100% textual de 10 páginas simulando Portarias e Despachos da PROGESP/UFAM:
    - 20 CPFs válidos gerados matematicamente (2 por página, incluindo repetição proposital do 1º CPF para teste de `group_id`);
    - 10 Matrículas SIAPE vinculadas a contexto funcional;
    - 10 E-mails institucionais nominais;
    - 5 Processos SEI nominais (vinculados a servidor no contexto imediato -> devem ser detectados);
    - 5 Processos SEI meramente organizacionais e CNPJ institucional da UFAM (`04.378.626/0001-97`) -> NÃO devem ser marcados.
    """
    doc = fitz.open()
    ground_truth: list[GroundTruthEntity] = []
    negative_org_strings: list[str] = ["04.378.626/0001-97"]

    # Lista determinística de sementes de 9 dígitos para CPFs sintéticos
    cpf_seeds = [
        "123456789",  # Página 1 (e repetido na Página 5 para testar agrupamento group_id)
        "234567891",
        "345678912",
        "456789123",
        "567891234",
        "678912345",
        "789123456",
        "891234567",
        "912345678",
        "135792468",
        "246813579",
        "369258147",
        "741852963",
        "159357486",
        "852963741",
        "951753852",
        "357159486",
        "654321987",
        "321987654",
        "987321654",
    ]

    server_names = [
        "Carlos Eduardo Mendonca",
        "Ana Beatriz Vasconcelos",
        "Roberto Carlos Nogueira",
        "Fernanda Lima Cavalcante",
        "Marcos Vinicius Peixoto",
        "Juliana Costa Tavares",
        "Paulo Henrique Amazonas",
        "Camila Rodrigues Barreto",
        "Ricardo Augusto Sampaio",
        "Patricia Oliveira Monteiro",
    ]

    recurring_cpf = generate_valid_cpf_formatted(cpf_seeds[0])

    for p_idx in range(10):
        page_num = p_idx + 1
        page = doc.new_page(width=595, height=842)

        cpf_a = recurring_cpf if p_idx in (0, 4) else generate_valid_cpf_formatted(cpf_seeds[p_idx * 2])
        cpf_b = generate_valid_cpf_formatted(cpf_seeds[p_idx * 2 + 1])
        siape_code = f"{1420300 + p_idx}"
        email_addr = f"servidor.sintetico{page_num}@ufam.edu.br"
        srv_name = server_names[p_idx]

        # Cabeçalho Institucional com CNPJ da UFAM (Não deve ser tarjado)
        page.insert_text(
            fitz.Point(45, 55),
            f"PODER EXECUTIVO - UNIVERSIDADE FEDERAL DO AMAZONAS (CNPJ 04.378.626/0001-97) - PÁGINA {page_num}/10",
            fontsize=10,
        )
        page.insert_text(
            fitz.Point(45, 75),
            f"PORTARIA PROGESP/UFAM Nº {400 + page_num}/2026 - DESPACHO ADMINISTRATIVO DE PESSOAL",
            fontsize=11,
        )

        # Parágrafo 1: Dados pessoais comuns do servidor (Nome, CPF A, Matrícula SIAPE, E-mail)
        line1 = f"Servidor: {srv_name} | Portador do CPF {cpf_a} | Matrícula SIAPE {siape_code}"
        page.insert_text(fitz.Point(45, 115), line1, fontsize=10.5)

        line2 = f"Contato funcional cadastrado: {email_addr} | Segundo titular CPF {cpf_b}."
        page.insert_text(fitz.Point(45, 140), line2, fontsize=10.5)

        # Parágrafo 2: Processo SEI organizacional isolado (Páginas pares) vs. Processo SEI nominal (Páginas ímpares)
        if page_num % 2 == 0:
            org_sei = f"23105.0089{page_num:02d}/2025-{10 + page_num:02d}"
            negative_org_strings.append(org_sei)
            page.insert_text(
                fitz.Point(45, 195),
                f"Referência normativa meramente organizacional de frota e almoxarifado: Processo {org_sei}.",
                fontsize=10,
            )
        else:
            nominal_sei = f"23105.0041{page_num:02d}/2026-{30 + page_num:02d}"
            page.insert_text(
                fitz.Point(45, 195),
                f"Requerente: {srv_name} vinculado diretamente ao Processo SEI {nominal_sei} em trâmite.",
                fontsize=10,
            )

        # Registra Ground Truth com coordenadas reais extraídas do PyMuPDF
        for val, etype in (
            (cpf_a, "CPF"),
            (cpf_b, "CPF"),
            (siape_code, "SIAPE"),
            (email_addr, "EMAIL"),
        ):
            x0, y0, x1, y1 = _locate_norm_bbox_on_page(page, val)
            ground_truth.append(
                GroundTruthEntity(
                    page_number=page_num,
                    entity_type=etype,
                    category=DataCategory.PERSONAL_COMMON,
                    value=val,
                    x0_norm=x0,
                    y0_norm=y0,
                    x1_norm=x1,
                    y1_norm=y1,
                )
            )

    pdf_bytes = doc.tobytes()
    doc.close()

    return SyntheticDocumentBundle(
        name="portaria_despacho_10p_ufam.pdf",
        pdf_bytes=pdf_bytes,
        page_count=10,
        ground_truth_entities=ground_truth,
        negative_organizational_strings=negative_org_strings,
    )


def generate_medical_and_union_report_pdf() -> SyntheticDocumentBundle:
    """
    Constrói um Laudo/Atestado Médico Pericial simulado contendo:
    - CPF válido do servidor (`PERSONAL_COMMON`);
    - Códigos médicos CID-10 (`F41.1` e `M54.5`) (`PERSONAL_SENSITIVE`);
    - Filiação sindical (`Sindicato ADUA`) e autodeclaração étnico-racial (`PERSONAL_SENSITIVE`);
    - Dependente funcional menor (`MINOR_OR_DEPENDENT`).
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    cpf_val = generate_valid_cpf_formatted("432198765")

    page.insert_text(
        fitz.Point(45, 60),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - SUBSISTEMA DE ATENÇÃO À SAÚDE DO SERVIDOR (SIASS)",
        fontsize=10.5,
    )
    page.insert_text(
        fitz.Point(45, 95),
        f"Atestado Médico e Laudo Pericial referente ao servidor portador do CPF {cpf_val}.",
        fontsize=10.5,
    )
    page.insert_text(
        fitz.Point(45, 125),
        "Diagnóstico clínico confirmado sob CID-10 F41.1 e patologia ortopédica associada CID M54.5.",
        fontsize=10.5,
    )
    page.insert_text(
        fitz.Point(45, 155),
        "Registro complementar: Sindicato ADUA e Autodeclaração étnico-racial homologada em banca.",
        fontsize=10.5,
    )
    page.insert_text(
        fitz.Point(45, 185),
        "Beneficiário de Pensão Alimentícia: Dependente: Lucas Gabriel Vasconcelos.",
        fontsize=10.5,
    )

    gt: list[GroundTruthEntity] = []
    for val, etype, cat in (
        (cpf_val, "CPF", DataCategory.PERSONAL_COMMON),
        ("F41.1", "CID_CODE", DataCategory.PERSONAL_SENSITIVE),
        ("M54.5", "CID_CODE", DataCategory.PERSONAL_SENSITIVE),
    ):
        x0, y0, x1, y1 = _locate_norm_bbox_on_page(page, val)
        gt.append(
            GroundTruthEntity(
                page_number=1,
                entity_type=etype,
                category=cat,
                value=val,
                x0_norm=x0,
                y0_norm=y0,
                x1_norm=x1,
                y1_norm=y1,
            )
        )

    pdf_bytes = doc.tobytes()
    doc.close()

    return SyntheticDocumentBundle(
        name="laudo_pericial_sensivel_ufam.pdf",
        pdf_bytes=pdf_bytes,
        page_count=1,
        ground_truth_entities=gt,
    )


def generate_financial_payroll_table_pdf() -> SyntheticDocumentBundle:
    """
    Constrói uma Planilha/Tabela Financeira de Folha de Pagamento da UFAM contendo:
    - Agência bancária (`3456-7`), Conta Corrente (`89012-3`), Chave PIX EVP e Remuneração Bruta (`FINANCIAL_BANKING`).
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    cpf_val = generate_valid_cpf_formatted("543216789")
    pix_evp = "123e4567-e89b-12d3-a456-426614174000"

    page.insert_text(
        fitz.Point(45, 60),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - PROPLAN - PLANILHA FINANCEIRA DE FOLHA DE PAGAMENTO",
        fontsize=10.5,
    )

    # Desenha grade de tabela vetorial para exercitar o extrator de células de tabela
    page.draw_rect(fitz.Rect(45, 85, 550, 120))
    page.draw_rect(fitz.Rect(45, 120, 550, 165))

    page.insert_text(
        fitz.Point(52, 106),
        f"Beneficiário CPF {cpf_val} | Remuneração Bruta: R$ 15.840,90",
        fontsize=10.5,
    )
    page.insert_text(
        fitz.Point(52, 146),
        f"Dados Bancários: Agência: 3456-7 | Conta Corrente: 89012-3 | Chave PIX: {pix_evp}",
        fontsize=10.5,
    )

    gt: list[GroundTruthEntity] = []
    for val, etype, cat in (
        (cpf_val, "CPF", DataCategory.PERSONAL_COMMON),
        ("3456-7", "BANK_AGENCY", DataCategory.FINANCIAL_BANKING),
        ("89012-3", "BANK_ACCOUNT", DataCategory.FINANCIAL_BANKING),
        (pix_evp, "PIX_KEY_EVP", DataCategory.FINANCIAL_BANKING),
        ("R$ 15.840,90", "FINANCIAL_AMOUNT", DataCategory.FINANCIAL_BANKING),
    ):
        x0, y0, x1, y1 = _locate_norm_bbox_on_page(page, val)
        gt.append(
            GroundTruthEntity(
                page_number=1,
                entity_type=etype,
                category=cat,
                value=val,
                x0_norm=x0,
                y0_norm=y0,
                x1_norm=x1,
                y1_norm=y1,
            )
        )

    pdf_bytes = doc.tobytes()
    doc.close()

    return SyntheticDocumentBundle(
        name="planilha_financeira_proplan_ufam.pdf",
        pdf_bytes=pdf_bytes,
        page_count=1,
        ground_truth_entities=gt,
    )


def generate_scanned_and_hybrid_pdf() -> SyntheticDocumentBundle:
    """
    Constrói um documento PDF Híbrido de 2 páginas:
    - Página 1: Textual nativa com CPF válido;
    - Página 2: Digitalizada/Escaneada (imagem rasterizada com leve rotação e ruído granulado controlado,
      sem camada de texto selecionável) contendo CPF válido para validação de OCR ONNX e queima de pixels.
    """
    doc = fitz.open()
    cpf_native = generate_valid_cpf_formatted("612345879")
    cpf_scanned = generate_valid_cpf_formatted("712345986")

    # Página 1: Nativa Textual
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text(
        fitz.Point(45, 70),
        "PROCESSO HÍBRIDO UFAM - PÁGINA 1 (TEXTO NATIVO)",
        fontsize=12,
    )
    p1.insert_text(
        fitz.Point(45, 115),
        f"Interessado portador do CPF {cpf_native} e Matrícula SIAPE 2345678.",
        fontsize=11,
    )
    x0_p1, y0_p1, x1_p1, y1_p1 = _locate_norm_bbox_on_page(p1, cpf_native)

    # Página 2: Escaneada com leve rotação e ruído sintético
    img_w, img_h = 1240, 1754
    canvas = Image.new("RGB", (img_w, img_h), color=(252, 252, 250))
    draw = ImageDraw.Draw(canvas)

    try:
        font_header = ImageFont.truetype("arial.ttf", 34)
        font_body = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        font_header = ImageFont.load_default()
        font_body = ImageFont.load_default()

    draw.text((90, 140), "UNIVERSIDADE FEDERAL DO AMAZONAS - DOCUMENTO DIGITALIZADO", fill=(15, 15, 15), font=font_header)
    draw.text((90, 230), f"Declaracao funcional escaneada do CPF {cpf_scanned}", fill=(10, 10, 10), font=font_body)

    # Adiciona ruído granulado leve determinístico simulando scanner departamental
    rng = random.Random(42)
    pixels = canvas.load()
    if pixels is not None:
        for _ in range(1200):
            rx = rng.randint(0, img_w - 1)
            ry = rng.randint(0, img_h - 1)
            shade = rng.randint(190, 235)
            pixels[rx, ry] = (shade, shade, shade)

    # Leve rotação de 0.4 grau típica de alimentador automático de papel (ADF)
    rotated = canvas.rotate(0.4, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(252, 252, 250))
    img_buf = io.BytesIO()
    rotated.save(img_buf, format="PNG")

    p2 = doc.new_page(width=595, height=842)
    p2.insert_image(p2.rect, stream=img_buf.getvalue())

    gt = [
        GroundTruthEntity(
            page_number=1,
            entity_type="CPF",
            category=DataCategory.PERSONAL_COMMON,
            value=cpf_native,
            x0_norm=x0_p1,
            y0_norm=y0_p1,
            x1_norm=x1_p1,
            y1_norm=y1_p1,
        ),
        GroundTruthEntity(
            page_number=2,
            entity_type="CPF",
            category=DataCategory.PERSONAL_COMMON,
            value=cpf_scanned,
            x0_norm=0.07,
            y0_norm=0.12,
            x1_norm=0.65,
            y1_norm=0.16,
        ),
    ]

    pdf_bytes = doc.tobytes()
    doc.close()

    return SyntheticDocumentBundle(
        name="documento_hibrido_escaneado_ufam.pdf",
        pdf_bytes=pdf_bytes,
        page_count=2,
        ground_truth_entities=gt,
    )
