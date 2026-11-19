"""
Renderizador Server-Side de Páginas PDF em WebP para Thin-Client (`page_renderer.py`).

Elimina a necessidade de `PDF.js` pesado nos computadores antigos das unidades da UFAM.
Recebe uma `fitz.Page` (PyMuPDF), renderiza um pixmap a 150 DPI e salva como `.webp`
otimizado (`quality=80`, `method=4`) em `tmp/{job_id}/pages/page_{page_num}.webp`,
mantendo o tamanho abaixo de ~120 KB por página (com ajuste adaptativo caso páginas
muito densas excedam o orçamento de bytes).
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_DPI: int = 150
DEFAULT_WEBP_QUALITY: int = 80
DEFAULT_WEBP_METHOD: int = 4
TARGET_MAX_WEBP_BYTES: int = 120 * 1024  # ~120 KB por página


@dataclass(frozen=True, slots=True)
class RenderedPagePreview:
    """Metadados e dimensões exatas da página renderizada em WebP."""

    job_id: str
    page_number: int  # 1-indexed
    image_path: Path
    width_px: int
    height_px: int
    page_width_pts: float
    page_height_pts: float
    dpi: int
    file_size_bytes: int


def render_page_to_webp(
    page: fitz.Page,
    job_id: uuid.UUID | str,
    page_num: int | None = None,
    *,
    base_temp_dir: Path | None = None,
    dpi: int = DEFAULT_PREVIEW_DPI,
    quality: int = DEFAULT_WEBP_QUALITY,
    method: int = DEFAULT_WEBP_METHOD,
    max_bytes_budget: int = TARGET_MAX_WEBP_BYTES,
) -> RenderedPagePreview:
    """
    Renderiza uma `fitz.Page` do PyMuPDF a 150 DPI e salva como imagem `.webp` otimizada
    em `{base_temp_dir}/{job_id}/pages/page_{page_num}.webp`.

    Caso `base_temp_dir` não seja informado, utiliza `Path("tmp")` ou `settings.TEMP_STORAGE_DIR`.
    """
    resolved_page_num = page_num if page_num is not None else (int(page.number) + 1)
    effective_root = base_temp_dir if base_temp_dir is not None else settings.TEMP_STORAGE_DIR

    pages_dir = effective_root / str(job_id) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    output_path = pages_dir / f"page_{resolved_page_num}.webp"

    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, alpha=False)

    mode = "RGB" if pix.n >= 3 else "L"
    pil_img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)

    # Em páginas com método padrão (4), utiliza method=1 inicialmente para alta velocidade (< 45ms/página)
    # e recorre a method=4/6 caso o tamanho exceda o orçamento de ~120 KB
    fast_method = 1 if method == DEFAULT_WEBP_METHOD else method
    buf = io.BytesIO()
    pil_img.save(buf, format="WEBP", quality=quality, method=fast_method)
    webp_bytes = buf.getvalue()

    if len(webp_bytes) > max_bytes_budget and fast_method != method:
        buf = io.BytesIO()
        pil_img.save(buf, format="WEBP", quality=quality, method=method)
        webp_bytes = buf.getvalue()

    # Se uma página extremamente densa ultrapassar o orçamento de ~120 KB, reduz progressivamente a qualidade
    current_quality = quality
    while len(webp_bytes) > max_bytes_budget and current_quality > 35:
        current_quality = max(35, current_quality - 15)
        buf = io.BytesIO()
        pil_img.save(buf, format="WEBP", quality=current_quality, method=6)
        webp_bytes = buf.getvalue()

    output_path.write_bytes(webp_bytes)

    page_rect = page.rect
    return RenderedPagePreview(
        job_id=str(job_id),
        page_number=resolved_page_num,
        image_path=output_path,
        width_px=pix.width,
        height_px=pix.height,
        page_width_pts=float(page_rect.width),
        page_height_pts=float(page_rect.height),
        dpi=dpi,
        file_size_bytes=len(webp_bytes),
    )
