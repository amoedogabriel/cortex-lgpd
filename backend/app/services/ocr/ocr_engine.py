"""
Motor de OCR Acelerado via ONNX (`ocr_engine.py`).

Executa inferência em CPU utilizando `rapidocr-onnxruntime` (pesos PaddleOCR otimizados para ONNX):
- Renderiza a página ou recorte a **200 DPI** (`fitz.Matrix(200/72, 200/72)`);
- Caso o score médio de confiança da página fique abaixo de `0.70`, executa fallback automático
  a **300 DPI** (`fitz.Matrix(300/72, 300/72)`) com pré-processamento de contraste e nitidez;
- Divide linhas OCR em palavras individuais usando largura visual ponderada via
  `fitz.get_text_length(..., fontname="helv", fontsize=10)` para posicionamento preciso;
- Converte todas as caixas delimitadoras (polígonos de 4 pontos do OCR) para coordenadas
  normalizadas `[x0_norm, y0_norm, x1_norm, y1_norm]` no intervalo `[0.0, 1.0]`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from app.core.ml_models import ml_registry

logger = logging.getLogger(__name__)

PRIMARY_OCR_DPI: int = 200
FALLBACK_OCR_DPI: int = 300
FALLBACK_CONFIDENCE_THRESHOLD: float = 0.70
_OCR_ROW_CLUSTER_Y0_TOL: float = 0.006  # ~5pt em A4 para ordenar esquerda->direita na mesma linha


@dataclass(frozen=True, slots=True)
class OCRTokenSpan:
    """Representa um token ou linha extraída via OCR com coordenadas normalizadas [0.0, 1.0]."""

    text: str
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    confidence: float
    dpi_used: int
    source: str = "OCR"


@dataclass(frozen=True, slots=True)
class OCRPageResult:
    """Resultado consolidado do OCR em uma página ou região."""

    lines: list[OCRTokenSpan]
    tokens: list[OCRTokenSpan]
    mean_confidence: float
    dpi_used: int
    fallback_triggered: bool


def _clamp_norm(val: float) -> float:
    """Garante que a coordenada normalizada permaneça estritamente em [0.0, 1.0]."""
    return round(max(0.0, min(1.0, float(val))), 6)


def _pixmap_to_numpy_rgb(pix: fitz.Pixmap, *, enhance_contrast: bool = False) -> np.ndarray:
    """Converte um `fitz.Pixmap` para array NumPy RGB, aplicando pré-processamento se solicitado."""
    mode = "RGB" if pix.n >= 3 else "L"
    pil_img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if mode != "RGB":
        pil_img = pil_img.convert("RGB")

    if enhance_contrast:
        # Equalização suave de níveis + reforço de contraste e nitidez para documentos escaneados desgastados
        gray = ImageOps.grayscale(pil_img)
        autocontrast = ImageOps.autocontrast(gray, cutoff=1)
        contrast_enhanced = ImageEnhance.Contrast(autocontrast).enhance(1.45)
        sharpened = ImageEnhance.Sharpness(contrast_enhanced).enhance(1.35)
        pil_img = sharpened.convert("RGB")

    return np.asarray(pil_img, dtype=np.uint8)


def _split_line_into_word_tokens(line_span: OCRTokenSpan) -> list[OCRTokenSpan]:
    """
    Subdivide uma linha reconhecida pelo OCR em tokens individuais de palavras,
    calculando `x0` e `x1` proporcionalmente à largura visual real ponderada dos caracteres
    via `fitz.get_text_length(substring, fontname="helv", fontsize=10)`.
    """
    raw_text = line_span.text
    if not raw_text or not raw_text.strip():
        return []

    total_line_len_pt = float(fitz.get_text_length(raw_text, fontname="helv", fontsize=10))
    if total_line_len_pt <= 1e-5:
        total_line_len_pt = float(max(1, len(raw_text)))

    line_width = max(1e-5, line_span.x1_norm - line_span.x0_norm)
    word_tokens: list[OCRTokenSpan] = []

    for match in re.finditer(r"\S+", raw_text):
        word = match.group(0)
        start_idx, end_idx = match.span()

        prefix_len_pt = float(fitz.get_text_length(raw_text[:start_idx], fontname="helv", fontsize=10))
        upto_end_len_pt = float(fitz.get_text_length(raw_text[:end_idx], fontname="helv", fontsize=10))

        rel_start = max(0.0, min(1.0, prefix_len_pt / total_line_len_pt))
        rel_end = max(rel_start, min(1.0, upto_end_len_pt / total_line_len_pt))

        wx0 = _clamp_norm(line_span.x0_norm + rel_start * line_width)
        wx1 = _clamp_norm(line_span.x0_norm + rel_end * line_width)
        if wx1 <= wx0:
            wx1 = _clamp_norm(min(1.0, wx0 + 1e-4))

        word_tokens.append(
            OCRTokenSpan(
                text=word,
                x0_norm=wx0,
                y0_norm=line_span.y0_norm,
                x1_norm=wx1,
                y1_norm=line_span.y1_norm,
                confidence=line_span.confidence,
                dpi_used=line_span.dpi_used,
                source="OCR",
            )
        )

    return word_tokens


def _sort_ocr_lines_reading_order(lines: list[OCRTokenSpan]) -> list[OCRTokenSpan]:
    """Ordena linhas OCR por faixas verticais (tolerância ~5pt) e da esquerda para a direita."""
    if not lines:
        return []
    by_y = sorted(lines, key=lambda sp: sp.y0_norm)
    rows: list[list[OCRTokenSpan]] = [[by_y[0]]]
    for sp in by_y[1:]:
        if abs(sp.y0_norm - rows[-1][0].y0_norm) <= _OCR_ROW_CLUSTER_Y0_TOL:
            rows[-1].append(sp)
        else:
            rows.append([sp])
    ordered: list[OCRTokenSpan] = []
    for row in rows:
        row.sort(key=lambda sp: sp.x0_norm)
        ordered.extend(row)
    return ordered


def _parse_rapidocr_output(
    raw_result: Any,
    img_width: int,
    img_height: int,
    dpi_used: int,
    *,
    region_offset_norm: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
) -> tuple[list[OCRTokenSpan], float]:
    """
    Converte a saída do `RapidOCR` (`[[box_points, text, score], ...]`) em objetos `OCRTokenSpan`
    normalizados em relação à página inteira `[0.0, 1.0]`.
    """
    if not raw_result or img_width <= 0 or img_height <= 0:
        return [], 1.0

    rx0, ry0, rx1, ry1 = region_offset_norm
    region_w = max(1e-6, rx1 - rx0)
    region_h = max(1e-6, ry1 - ry0)

    lines: list[OCRTokenSpan] = []
    scores: list[float] = []

    for item in raw_result:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        box_pts, text, score_raw = item[0], str(item[1]).strip(), float(item[2])
        if not text:
            continue

        xs = [float(pt[0]) for pt in box_pts]
        ys = [float(pt[1]) for pt in box_pts]

        local_x0 = max(0.0, min(xs)) / img_width
        local_y0 = max(0.0, min(ys)) / img_height
        local_x1 = min(float(img_width), max(xs)) / img_width
        local_y1 = min(float(img_height), max(ys)) / img_height

        page_x0 = _clamp_norm(rx0 + local_x0 * region_w)
        page_y0 = _clamp_norm(ry0 + local_y0 * region_h)
        page_x1 = _clamp_norm(rx0 + local_x1 * region_w)
        page_y1 = _clamp_norm(ry0 + local_y1 * region_h)

        if page_x1 <= page_x0:
            page_x1 = _clamp_norm(min(1.0, page_x0 + 1e-4))
        if page_y1 <= page_y0:
            page_y1 = _clamp_norm(min(1.0, page_y0 + 1e-4))

        conf = round(max(0.0, min(1.0, score_raw)), 4)
        scores.append(conf)
        lines.append(
            OCRTokenSpan(
                text=text,
                x0_norm=page_x0,
                y0_norm=page_y0,
                x1_norm=page_x1,
                y1_norm=page_y1,
                confidence=conf,
                dpi_used=dpi_used,
                source="OCR",
            )
        )

    mean_conf = round(sum(scores) / len(scores), 4) if scores else 1.0
    return lines, mean_conf


def run_ocr_on_page(
    page: fitz.Page,
    *,
    clip_rect: fitz.Rect | None = None,
    primary_dpi: int = PRIMARY_OCR_DPI,
    fallback_dpi: int = FALLBACK_OCR_DPI,
    confidence_threshold: float = FALLBACK_CONFIDENCE_THRESHOLD,
) -> OCRPageResult:
    """
    Executa OCR em uma página completa (`SCANNED`) ou em uma sub-região (`HYBRID`)
    a 200 DPI via `rapidocr-onnxruntime`, acionando fallback a 300 DPI com melhoria de
    contraste apenas quando o score médio de confiança for inferior a `0.70`.
    """
    ocr_engine = ml_registry.get_ocr_engine()
    page_rect = page.rect
    pw = max(1.0, float(page_rect.width))
    ph = max(1.0, float(page_rect.height))

    if clip_rect is not None:
        safe_clip = clip_rect & page_rect
        region_norm = (
            _clamp_norm((safe_clip.x0 - page_rect.x0) / pw),
            _clamp_norm((safe_clip.y0 - page_rect.y0) / ph),
            _clamp_norm((safe_clip.x1 - page_rect.x0) / pw),
            _clamp_norm((safe_clip.y1 - page_rect.y0) / ph),
        )
    else:
        safe_clip = None
        region_norm = (0.0, 0.0, 1.0, 1.0)

    # 1. Passo primário a 200 DPI
    mat_primary = fitz.Matrix(primary_dpi / 72.0, primary_dpi / 72.0)
    pix_primary = page.get_pixmap(matrix=mat_primary, clip=safe_clip, alpha=False)
    np_img_primary = _pixmap_to_numpy_rgb(pix_primary, enhance_contrast=False)

    raw_primary, _ = ocr_engine(np_img_primary)
    lines_primary, mean_conf_primary = _parse_rapidocr_output(
        raw_primary,
        pix_primary.width,
        pix_primary.height,
        primary_dpi,
        region_offset_norm=region_norm,
    )

    used_lines = lines_primary
    used_conf = mean_conf_primary
    used_dpi = primary_dpi
    fallback_triggered = False

    # 2. Fallback a 300 DPI com pré-processamento de contraste se confiança média < 0.70
    if lines_primary and mean_conf_primary < confidence_threshold:
        fallback_triggered = True
        mat_fallback = fitz.Matrix(fallback_dpi / 72.0, fallback_dpi / 72.0)
        pix_fallback = page.get_pixmap(matrix=mat_fallback, clip=safe_clip, alpha=False)
        np_img_fallback = _pixmap_to_numpy_rgb(pix_fallback, enhance_contrast=True)

        raw_fallback, _ = ocr_engine(np_img_fallback)
        lines_fallback, mean_conf_fallback = _parse_rapidocr_output(
            raw_fallback,
            pix_fallback.width,
            pix_fallback.height,
            fallback_dpi,
            region_offset_norm=region_norm,
        )
        if lines_fallback and mean_conf_fallback >= mean_conf_primary:
            used_lines = lines_fallback
            used_conf = mean_conf_fallback
            used_dpi = fallback_dpi

    # Ordena linhas na ordem natural de leitura (topo -> base, esquerda -> direita)
    used_lines = _sort_ocr_lines_reading_order(used_lines)

    tokens: list[OCRTokenSpan] = []
    for line in used_lines:
        tokens.extend(_split_line_into_word_tokens(line))

    return OCRPageResult(
        lines=used_lines,
        tokens=tokens,
        mean_confidence=used_conf,
        dpi_used=used_dpi,
        fallback_triggered=fallback_triggered,
    )
