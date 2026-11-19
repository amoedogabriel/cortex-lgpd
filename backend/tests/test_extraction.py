"""
Testes automatizados da Etapa 2 (`test_extraction.py`):
1. Pre-warming Singleton dos modelos de ML (`ml_models.py`);
2. Validador de upload (`file_validator.py`) aceitando PDF válido e rejeitando PDFs maliciosos (`/JavaScript`);
3. Renderizador de página Server-Side em `.webp` (`page_renderer.py`) gerando imagem otimizada < 120 KB;
4. Extrator posicional (`extractor.py`) e motor de OCR ONNX (`ocr_engine.py`):
   - Página textual nativa processada em < 200ms sem acionar OCR (`Zero-Waste OCR`);
   - Página escaneada processada via `rapidocr-onnxruntime` em < 1,5s (1500ms) com modelos pré-aquecidos;
   - Todas as Bounding Boxes normalizadas estritamente no intervalo `[0.0, 1.0]`.
"""

from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

import fitz  # PyMuPDF
import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.exceptions import InvalidDocumentError
from app.core.ml_models import ml_registry
from app.services.pdf_extractor.extractor import (
    PageContentType,
    VisualElementKind,
    extract_single_page,
)
from app.services.pdf_extractor.file_validator import validate_and_store_pdf
from app.services.pdf_extractor.page_renderer import TARGET_MAX_WEBP_BYTES, render_page_to_webp


def _build_textual_pdf_in_memory() -> bytes:
    """Gera em memória um PDF 100% textual contendo parágrafos, tabela simples e linha de assinatura."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 em pontos (72 DPI)

    # Cabeçalho e corpo institucional
    page.insert_text(
        fitz.Point(50, 60),
        "UNIVERSIDADE FEDERAL DO AMAZONAS - UFAM",
        fontsize=14,
    )
    page.insert_text(
        fitz.Point(50, 85),
        "PRÓ-REITORIA DE GESTÃO DE PESSOAS - PROGESP",
        fontsize=11,
    )
    page.insert_text(
        fitz.Point(50, 120),
        "Despacho Administrativo referente ao Servidor João da Silva, CPF 123.456.789-00, Matrícula SIAPE 1234567.",
        fontsize=10,
    )

    # Desenha uma grade de tabela simples (2 linhas x 2 colunas) para validar extração de células
    table_top = 160
    col1_x, col2_x, col3_x = 50, 260, 500
    row1_y, row2_y, row3_y = table_top, table_top + 25, table_top + 50

    page.draw_rect(fitz.Rect(col1_x, row1_y, col2_x, row2_y), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(col2_x, row1_y, col3_x, row2_y), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(col1_x, row2_y, col2_x, row3_y), color=(0, 0, 0), width=1)
    page.draw_rect(fitz.Rect(col2_x, row2_y, col3_x, row3_y), color=(0, 0, 0), width=1)

    page.insert_text(fitz.Point(col1_x + 8, row1_y + 16), "Servidor", fontsize=10)
    page.insert_text(fitz.Point(col2_x + 8, row1_y + 16), "Matricula", fontsize=10)
    page.insert_text(fitz.Point(col1_x + 8, row2_y + 16), "Maria Souza", fontsize=10)
    page.insert_text(fitz.Point(col2_x + 8, row2_y + 16), "7654321", fontsize=10)

    # Âncora de assinatura visual na metade inferior da página
    page.insert_text(
        fitz.Point(180, 700),
        "____________________________________________",
        fontsize=10,
    )
    page.insert_text(
        fitz.Point(180, 720),
        "Assinatura do Servidor(a) Requerente",
        fontsize=10,
    )

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _build_scanned_pdf_in_memory() -> bytes:
    """Gera em memória um PDF puramente escaneado (imagem raster sem camada de texto nativo)."""
    img_w, img_h = 1240, 1754
    canvas = Image.new("RGB", (img_w, img_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except OSError:
        font = ImageFont.load_default()

    draw.text((80, 120), "UNIVERSIDADE FEDERAL DO AMAZONAS", fill=(0, 0, 0), font=font)
    draw.text((80, 200), "PORTARIA PROGESP N 452 DE 2026", fill=(0, 0, 0), font=font)
    draw.text((80, 280), "CPF 123.456.789-00 SIAPE 1234567", fill=(0, 0, 0), font=font)

    img_buf = io.BytesIO()
    canvas.save(img_buf, format="PNG")
    png_bytes = img_buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Insere a imagem cobrindo praticamente toda a página A4 (sem nenhum texto nativo)
    page.insert_image(fitz.Rect(20, 20, 575, 822), stream=png_bytes)

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture(scope="module", autouse=True)
def warmup_ml_models_once() -> None:
    """Garante que o motor RapidOCR ONNX e o spaCy estejam pré-aquecidos em RAM antes dos testes."""
    ml_registry.warmup(load_ocr=True, load_spacy=True)
    assert ml_registry.is_warmed_up is True


def test_file_validator_accepts_valid_pdf_and_rejects_malicious_js(tmp_path: Path) -> None:
    """Valida aceitação de PDF legítimo (com SHA-256) e rejeição de PDF contendo /JavaScript."""
    valid_bytes = _build_textual_pdf_in_memory()
    job_id = uuid.uuid4()

    meta = validate_and_store_pdf(valid_bytes, job_id=job_id, base_temp_dir=tmp_path)
    assert meta.job_id == job_id
    assert meta.file_path.exists()
    assert len(meta.sha256_hash) == 64
    assert meta.page_count == 1
    assert meta.mime_type == "application/pdf"

    # Injeta uma ação /JavaScript maliciosa no PDF via pikepdf e verifica rejeição
    malicious_stream = io.BytesIO()
    with pikepdf.open(io.BytesIO(valid_bytes)) as pdf:
        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            {
                "/S": pikepdf.Name("/JavaScript"),
                "/JS": "app.alert('pwned');",
            }
        )
        pdf.save(malicious_stream)

    with pytest.raises(InvalidDocumentError) as exc_info:
        validate_and_store_pdf(malicious_stream.getvalue(), base_temp_dir=tmp_path)

    assert "MALICIOUS" in str(exc_info.value.details.get("reason", ""))


def test_textual_page_extraction_webp_and_performance(tmp_path: Path) -> None:
    """
    Valida em um PDF textual gerado em memória:
    1. Geração da imagem `.webp` a 150 DPI abaixo de ~120 KB;
    2. Classificação `TEXTUAL` com Zero-Waste OCR (`ocr_executed == False`);
    3. Extração unificada de tokens, linhas e tabela com coordenadas normalizadas em `[0.0, 1.0]`;
    4. Tempo de execução da extração posicional da página textual estritamente inferior a 200ms.
    """
    pdf_bytes = _build_textual_pdf_in_memory()
    job_id = uuid.uuid4()

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]

        # 1. Testa geração da imagem .webp de visualização Thin-Client (150 DPI, quality=80, method=4)
        preview = render_page_to_webp(page, job_id=job_id, page_num=1, base_temp_dir=tmp_path)
        assert preview.image_path.exists()
        assert preview.image_path.suffix == ".webp"
        assert preview.width_px > 1000 and preview.height_px > 1400
        assert preview.file_size_bytes <= TARGET_MAX_WEBP_BYTES

        # 2. Mede tempo da extração posicional da página textual (Zero-Waste OCR)
        t0 = time.perf_counter()
        result = extract_single_page(
            page,
            job_id=job_id,
            page_number=1,
            render_webp=False,
            base_temp_dir=tmp_path,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result.preview_webp = preview
    finally:
        doc.close()

    assert result.page_type == PageContentType.TEXTUAL
    assert result.ocr_executed is False
    assert len(result.tokens) >= 10
    assert "PROGESP" in result.full_text
    assert "123.456.789-00" in result.full_text

    # Verifica células de tabela extraídas com cabeçalho preservado
    assert len(result.table_cells) > 0
    headers = {c.header_label for c in result.table_cells}
    assert "Servidor" in headers or "Matricula" in headers

    # Verifica detecção de zona de assinatura visual
    assert any(v.kind == VisualElementKind.VISUAL_SIGNATURE for v in result.visual_elements)

    # Verifica normalização estrita [0.0, 1.0] em todos os tokens e linhas
    for tok in result.tokens:
        assert 0.0 <= tok.x0_norm < tok.x1_norm <= 1.0
        assert 0.0 <= tok.y0_norm < tok.y1_norm <= 1.0
        assert tok.source == "NATIVE"

    # Critério de aceite: página textual < 200ms
    assert elapsed_ms < 200.0, f"Extração textual levou {elapsed_ms:.2f}ms (limite: 200ms)"


def test_scanned_page_extraction_onnx_ocr_and_performance(tmp_path: Path) -> None:
    """
    Valida em um PDF escaneado gerado em memória (com modelo pré-aquecido):
    1. Geração da imagem `.webp` em `tmp/{job_id}/pages/page_1.webp`;
    2. Classificação `SCANNED` e acionamento do `rapidocr-onnxruntime` a 200 DPI;
    3. Extração de tokens OCR com coordenadas normalizadas em `[0.0, 1.0]`;
    4. Tempo de execução da página escaneada em CPU inferior a 1,5s (1500ms).
    """
    pdf_bytes = _build_scanned_pdf_in_memory()
    job_id = uuid.uuid4()

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]

        preview = render_page_to_webp(page, job_id=job_id, page_num=1, base_temp_dir=tmp_path)

        t0 = time.perf_counter()
        result = extract_single_page(
            page,
            job_id=job_id,
            page_number=1,
            render_webp=False,
            base_temp_dir=tmp_path,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result.preview_webp = preview
    finally:
        doc.close()

    assert result.page_type == PageContentType.SCANNED
    assert result.ocr_executed is True
    assert result.preview_webp is not None
    assert result.preview_webp.image_path.exists()
    assert result.preview_webp.file_size_bytes <= TARGET_MAX_WEBP_BYTES

    assert len(result.tokens) > 0
    extracted_upper = result.full_text.upper()
    assert "AMAZONAS" in extracted_upper or "PROGESP" in extracted_upper or "123" in extracted_upper

    for tok in result.tokens:
        assert 0.0 <= tok.x0_norm < tok.x1_norm <= 1.0
        assert 0.0 <= tok.y0_norm < tok.y1_norm <= 1.0
        assert tok.source == "OCR"

    # Critério de aceite: página escaneada em CPU < 1500ms (1,5s) com modelo pré-aquecido
    assert elapsed_ms < 1500.0, f"Extração OCR em página escaneada levou {elapsed_ms:.2f}ms (limite: 1500ms)"
