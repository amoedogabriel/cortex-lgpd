"""
Testes automatizados da Etapa 4 (`test_true_redaction.py`):
1. Gera um PDF híbrido em memória contendo:
   - Camada de texto nativo com Nome Completo e CPF;
   - Imagem raster embutida contendo CPF e Nome renderizados em pixels;
   - Metadados `/Info` (Author, Title, Subject), fluxo XML `/Metadata` (XMP),
     anotação de comentário e widget de formulário na área tarjada;
2. Executa o pipeline de Tarjamento Definitivo (`execute_complete_redaction_pipeline`):
   - `pdf_redactor.py` (`add_redact_annot` + `apply_redactions` único por página);
   - `structural_sanitizer.py` (`pikepdf` removendo `/Info`, XMP, `/OCProperties`, etc.);
3. Executa o Validador Pós-Tarjamento Cirúrgico (`validate_redacted_pdf_surgically`):
   - Confirma que `page.get_text("text", clip=rect)` é estritamente vazio e `page.search_for()` retorna 0;
   - Confirma que os pixels da imagem embutida na área tarjada foram fisicamente queimados em preto;
   - Confirma que metadados `/Info` e `/Metadata` XMP foram expurgados e o PDF original permanece intacto;
   - Comprova que todo o ciclo (redação física + sanitização + validação cirúrgica) ocorre em menos de 1,5s (1500ms)
     e que o validador cirúrgico isolado executa em menos de 500ms.
"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import fitz  # PyMuPDF
import pikepdf
from PIL import Image, ImageDraw, ImageFont

from app.models.enums import DataCategory
from app.services.redactor.pdf_redactor import RedactionBoxSpec
from app.services.redactor.structural_sanitizer import execute_complete_redaction_pipeline
from app.services.security_validator.post_redaction_validator import validate_redacted_pdf_surgically

SECRET_NAME = "Roberto Carlos Amazonas"
SECRET_CPF = "529.982.247-25"
IMAGE_SECRET_CPF = "841.239.650-11"


def _create_hybrid_pdf_with_metadata_and_widgets(output_path: Path) -> tuple[list[RedactionBoxSpec], str]:
    """
    Gera um PDF híbrido (texto nativo + imagem raster embutida + metadados XMP/Info + anotação)
    e retorna a lista de `RedactionBoxSpec` normalizadas em `[0.0, 1.0]` correspondentes às áreas sensíveis.
    """
    doc = fitz.open()
    pw, ph = 595.0, 842.0
    page = doc.new_page(width=pw, height=ph)

    # 1. Texto institucional público (deve ser preservado intacto)
    page.insert_text(
        fitz.Point(50, 60),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - PORTARIA PROGESP N 890/2026",
        fontsize=12,
    )

    # 2. Texto nativo sensível com Nome e CPF
    page.insert_text(
        fitz.Point(50, 110),
        f"Servidor: {SECRET_NAME} | CPF: {SECRET_CPF}",
        fontsize=11,
    )

    # Localiza os retângulos exatos do nome e do CPF nativo para montar as caixas normalizadas
    name_rect = page.search_for(SECRET_NAME)[0]
    cpf_rect = page.search_for(SECRET_CPF)[0]

    # Adiciona uma anotação de comentário sobre o CPF para comprovar remoção de anotações
    page.add_text_annot(cpf_rect.tl, "Comentario confidencial contendo CPF")

    # 3. Imagem raster embutida contendo CPF e Nome em pixels (simulando carimbo/recorte digitalizado)
    img_w, img_h = 420, 110
    pil_img = Image.new("RGB", (img_w, img_h), color=(245, 245, 245))
    draw = ImageDraw.Draw(pil_img)
    font = ImageFont.load_default()
    draw.text((20, 25), f"ASSINADO DIGITALMENTE: {SECRET_NAME}", fill=(10, 10, 10), font=font)
    draw.text((20, 60), f"CPF DO TITULAR: {IMAGE_SECRET_CPF}", fill=(10, 10, 10), font=font)

    img_buf = io.BytesIO()
    pil_img.save(img_buf, format="PNG")
    img_rect = fitz.Rect(50, 200, 350, 280)
    page.insert_image(img_rect, stream=img_buf.getvalue())

    # Define metadados de autoria e XMP que poderiam vazar identidade do servidor
    doc.set_metadata(
        {
            "author": SECRET_NAME,
            "title": f"Processo Individual de {SECRET_NAME}",
            "subject": f"CPF {SECRET_CPF}",
            "keywords": "PII, Confidencial",
            "creator": "Sistema Antigo UFAM",
        }
    )
    doc.set_xml_metadata(
        f'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        f"<rdf:Description rdf:about='' xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        f"<dc:creator>{SECRET_NAME}</dc:creator></rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end='w'?>"
    )

    doc.save(str(output_path))
    doc.close()

    original_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

    boxes = [
        RedactionBoxSpec(
            page_number=1,
            x0_norm=round(name_rect.x0 / pw, 6),
            y0_norm=round(name_rect.y0 / ph, 6),
            x1_norm=round(name_rect.x1 / pw, 6),
            y1_norm=round(name_rect.y1 / ph, 6),
            category=DataCategory.PERSONAL_COMMON,
            expected_text=SECRET_NAME,
        ),
        RedactionBoxSpec(
            page_number=1,
            x0_norm=round(cpf_rect.x0 / pw, 6),
            y0_norm=round(cpf_rect.y0 / ph, 6),
            x1_norm=round(cpf_rect.x1 / pw, 6),
            y1_norm=round(cpf_rect.y1 / ph, 6),
            category=DataCategory.PERSONAL_COMMON,
            expected_text=SECRET_CPF,
        ),
        RedactionBoxSpec(
            page_number=1,
            x0_norm=round(img_rect.x0 / pw, 6),
            y0_norm=round(img_rect.y0 / ph, 6),
            x1_norm=round(img_rect.x1 / pw, 6),
            y1_norm=round(img_rect.y1 / ph, 6),
            category=DataCategory.PERSONAL_COMMON,
            expected_text=IMAGE_SECRET_CPF,
        ),
    ]
    return boxes, original_sha256


def test_true_redaction_and_surgical_validation_under_1500ms(tmp_path: Path) -> None:
    """
    Valida que:
    1. O PDF original permanece 100% intacto;
    2. O texto subjacente (`Roberto Carlos Amazonas` e `529.982.247-25`) é fisicamente destruído;
    3. Os pixels da imagem embutida na região tarjada são queimados em preto sólido;
    4. O texto público fora das tarjas (`PORTARIA PROGESP N 890/2026`) permanece intacto;
    5. Metadados `/Info` e `/Metadata` (XMP) contendo o nome do autor são removidos pelo `pikepdf`;
    6. A validação cirúrgica executa em < 500ms e todo o pipeline termina em < 1500ms (1,5s).
    """
    original_pdf = tmp_path / "hybrid_original.pdf"
    redacted_pdf = tmp_path / "hybrid_redacted_final.pdf"

    boxes, original_sha256_before = _create_hybrid_pdf_with_metadata_and_widgets(original_pdf)

    t_total_start = time.perf_counter()

    # 1. Executa pipeline completo de redação física (PyMuPDF) + sanitização estrutural (pikepdf)
    pipeline_res = execute_complete_redaction_pipeline(
        original_pdf_path=original_pdf,
        boxes=boxes,
        final_output_pdf_path=redacted_pdf,
    )

    # 2. Executa validador cirúrgico pós-tarjamento
    t_val_start = time.perf_counter()
    validation_res = validate_redacted_pdf_surgically(
        redacted_pdf_path=redacted_pdf,
        boxes=boxes,
        forbidden_strings=[SECRET_NAME, SECRET_CPF, IMAGE_SECRET_CPF],
    )
    val_elapsed_ms = (time.perf_counter() - t_val_start) * 1000.0
    total_elapsed_ms = (time.perf_counter() - t_total_start) * 1000.0

    # Verifica que o arquivo original não foi alterado
    original_sha256_after = hashlib.sha256(original_pdf.read_bytes()).hexdigest()
    assert original_sha256_after == original_sha256_before
    assert pipeline_res.original_sha256 == original_sha256_before
    assert pipeline_res.final_sha256 != original_sha256_before
    assert pipeline_res.affected_pages == [1]
    assert pipeline_res.total_boxes_applied == 3
    assert pipeline_res.physical_result.annotations_removed >= 1

    # Verifica aprovação em todas as 3 camadas do validador cirúrgico
    assert validation_res.passed is True
    assert validation_res.text_layer_verified is True
    assert validation_res.pixel_layer_verified is True
    assert validation_res.structure_verified is True
    assert validation_res.boxes_checked == 3

    for b_res in validation_res.box_results:
        assert b_res.clip_text_empty is True
        assert b_res.search_hits_in_box == 0
        assert b_res.pixels_blackened is True
        assert b_res.ocr_leak_detected is False
        assert b_res.passed is True

    # Reabre o PDF final e comprova que o cabeçalho público sobreviveu, mas os dados pessoais sumiram do texto global
    doc_check = fitz.open(str(redacted_pdf))
    try:
        full_page_text = doc_check[0].get_text("text")
        assert "PORTARIA PROGESP N 890/2026" in full_page_text
        assert SECRET_NAME not in full_page_text
        assert SECRET_CPF not in full_page_text
        assert doc_check[0].search_for(SECRET_NAME) == []
        assert doc_check[0].search_for(SECRET_CPF) == []
    finally:
        doc_check.close()

    # Verifica via pikepdf que o nome do autor nos metadados /Info e /Metadata XMP foi destruído
    with pikepdf.open(str(redacted_pdf)) as pdf_check:
        assert "/Metadata" not in pdf_check.Root
        assert "/Author" not in pdf_check.docinfo
        assert "/Title" not in pdf_check.docinfo
        assert "/Subject" not in pdf_check.docinfo

    # Critérios de performance: validador cirúrgico < 500ms e ciclo completo < 1500ms
    assert val_elapsed_ms < 500.0, f"Validação cirúrgica levou {val_elapsed_ms:.2f}ms (limite: 500ms)"
    assert total_elapsed_ms < 1500.0, f"Ciclo completo de tarjamento + validação levou {total_elapsed_ms:.2f}ms (limite: 1500ms)"
