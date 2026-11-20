"""
Analisador Posicional por Página e OCR Seletivo (`extractor.py`).

Requisitos implementados:
1. Extração página por página (`extract_single_page`) preparando o terreno para streaming SSE/WebSocket;
2. Classificação de cada página como `TEXTUAL`, `SCANNED` ou `HYBRID` (Zero-Waste OCR: jamais executa
   OCR em páginas 100% textuais sem imagens relevantes maiores que 100x50px, mas detecta PDFs de texto
   vetorizado como `Microsoft: Print To PDF` que têm 0 palavras nativas e glifos desenhados como vetores);
3. Em páginas `TEXTUAL`, extrai palavras, linhas e estruturas de tabelas diretamente via `PyMuPDF`
   em milissegundos, preservando a relação cabeçalho-célula (`header_label`) tanto para tabelas com
   retângulos explícitos quanto para tabelas desenhadas com linhas de grade (`GRID`);
4. Em páginas `SCANNED` ou regiões de imagem de páginas `HYBRID`, executa OCR ONNX a 200 DPI
   (com fallback a 300 DPI se confiança média < 0.70 e resgate cirúrgico por linha via Tesseract);
5. Identifica caixas delimitadoras de assinaturas visuais/manuscritas (`VISUAL_SIGNATURE`),
   fotografias 3x4/faciais e biometria digital (`FACIAL_PHOTO`) por análise geométrica de imagens
   embutidas (protegendo brasões e cabeçalhos institucionais), âncoras OCR (`FOTO 3x4`, `POLEGAR DIREITO`,
   `Ciente ...`), e varredura filtrada de molduras/contornos 3x4 sem texto impresso interno;
6. Converte todas as coordenadas para o padrão normalizado `[x0_norm, y0_norm, x1_norm, y1_norm]`
   no intervalo `[0.0, 1.0]`.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

from app.services.ocr.ocr_engine import run_ocr_on_page
from app.services.pdf_extractor.page_renderer import RenderedPagePreview, render_page_to_webp

logger = logging.getLogger(__name__)

MIN_RELEVANT_IMAGE_WIDTH_PX: int = 100
MIN_RELEVANT_IMAGE_HEIGHT_PX: int = 50
MIN_VECTOR_DRAWINGS_FOR_OUTLINED_TEXT: int = 15

_UNDERSCORE_SIGNATURE_LINE_RE = re.compile(r"_{6,}")
_STRICT_SIGNATURE_LABEL_RE = re.compile(
    r"(?i)^\s*(?:assinatura\s+do(?:\(a\))?\s+|assinado\s+(?:eletronicamente|digitalmente)?\s*por\b)"
)
_PHOTO_ANCHOR_RE = re.compile(
    r"(?i)\b(foto\s*3\s*[x×*]?\s*4(?:\s+escanead[ao])?|fotografia\s+3\s*[x×*]?\s*4|identifica[cçg][aã]o\s+visual)\b"
)
_POLEGAR_ANCHOR_RE = re.compile(
    r"(?i)\b(polegar\s+(?:direito|esquerdo)|impress[aã]o\s+digital)\b"
)
_ID_CARD_ANCHOR_RE = re.compile(
    r"(?i)\b(carteira\s+de\s*identidade|registro\s+geral|polegar\s+(?:direito|esquerdo)|assinatura\s+do\s+titular)\b"
)
_CIENTE_SIGNATURE_RE = re.compile(
    r"(?i)\bciente.{0,45}(?:19|20)\d{2}\b"
)
_NON_PORTRAIT_DOC_RE = re.compile(
    r"(?i)(?:(?:fatura|vencimento|total\s+da\s+fatura|total\s+a\s+pagar|limite\s+de\s+cr[eé]dito|banco\s+bradesco|visa\s*platinum|hidr[oô]metro|nota\s+fiscal)"
    r"|(?:\bmensagem\b.{0,240}\b(?:bom\s+dia|boa\s+tarde|boa\s+noite|obrigad[oa])\b))"
)
_INSTITUTIONAL_HEADER_TEXT_RE = re.compile(
    r"(?i)(?:poder\s*judici[aá]rio|estado\s*do\s*amazonas|tribunal\s*de\s*justi[cç]a|"
    r"rep[uú]blica\s*federativa|minist[eé]rio|universidade\s*federal|juizado\s*especial|"
    r"comarca\s*de|projudi|prefeitura|governo\s*do\s*estado|termo\s*de\s*apresenta[cç][aã]o)"
)


class PageContentType(StrEnum):
    """Classificação estrutural da página para roteamento Zero-Waste OCR."""

    TEXTUAL = "TEXTUAL"
    SCANNED = "SCANNED"
    HYBRID = "HYBRID"


class VisualElementKind(StrEnum):
    """Tipos de elementos visuais sensíveis detectados na página."""

    VISUAL_SIGNATURE = "VISUAL_SIGNATURE"
    FACIAL_PHOTO = "FACIAL_PHOTO"


@dataclass(frozen=True, slots=True)
class UnifiedToken:
    """Token unificado (nativo ou OCR) com coordenadas normalizadas em [0.0, 1.0]."""

    text: str
    page_number: int
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    confidence: float = 1.0
    source: str = "NATIVE"  # "NATIVE" ou "OCR"
    block_no: int = 0
    line_no: int = 0
    word_no: int = 0


@dataclass(frozen=True, slots=True)
class UnifiedLine:
    """Linha de texto unificada (nativa ou OCR) com coordenadas normalizadas em [0.0, 1.0]."""

    text: str
    page_number: int
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    confidence: float = 1.0
    source: str = "NATIVE"


@dataclass(frozen=True, slots=True)
class ExtractedTableCell:
    """Célula de tabela com vínculo explícito ao cabeçalho da coluna e coordenadas normalizadas."""

    page_number: int
    table_index: int
    row_index: int
    col_index: int
    header_label: str
    cell_text: str
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    cell_tokens: tuple[UnifiedToken, ...] = ()


@dataclass(frozen=True, slots=True)
class DetectedVisualElement:
    """Caixa delimitadora de assinatura visual ou fotografia 3x4/facial em [0.0, 1.0]."""

    page_number: int
    kind: VisualElementKind
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    confidence: float
    detection_method: str


@dataclass(slots=True)
class PageExtractionResult:
    """Resultado completo da extração posicional e renderização de uma única página."""

    page_number: int
    page_type: PageContentType
    page_width_pts: float
    page_height_pts: float
    tokens: list[UnifiedToken] = field(default_factory=list)
    lines: list[UnifiedLine] = field(default_factory=list)
    table_cells: list[ExtractedTableCell] = field(default_factory=list)
    visual_elements: list[DetectedVisualElement] = field(default_factory=list)
    preview_webp: RenderedPagePreview | None = None
    ocr_executed: bool = False
    ocr_dpi_used: int | None = None
    ocr_mean_confidence: float | None = None
    processing_time_ms: float = 0.0

    @property
    def full_text(self) -> str:
        """Retorna o texto concatenado das linhas da página preservando quebras."""
        return "\n".join(line.text for line in self.lines if line.text)


def _clamp_norm(val: float) -> float:
    return round(max(0.0, min(1.0, float(val))), 6)


def _normalize_rect(
    rect: fitz.Rect | tuple[float, float, float, float],
    page_rect: fitz.Rect,
) -> tuple[float, float, float, float]:
    """Converte um retângulo em pontos PDF (72 DPI) para coordenadas normalizadas [0.0, 1.0]."""
    r = fitz.Rect(rect) & page_rect
    pw = max(1.0, float(page_rect.width))
    ph = max(1.0, float(page_rect.height))

    x0 = _clamp_norm((r.x0 - page_rect.x0) / pw)
    y0 = _clamp_norm((r.y0 - page_rect.y0) / ph)
    x1 = _clamp_norm((r.x1 - page_rect.x0) / pw)
    y1 = _clamp_norm((r.y1 - page_rect.y0) / ph)

    if x1 <= x0:
        x1 = _clamp_norm(min(1.0, x0 + 1e-4))
    if y1 <= y0:
        y1 = _clamp_norm(min(1.0, y0 + 1e-4))
    return x0, y0, x1, y1


@dataclass(frozen=True, slots=True)
class _ImageRegionInfo:
    """Metadados internos de uma imagem raster encontrada na página."""

    bbox: fitz.Rect
    pixel_width: int
    pixel_height: int
    coverage_ratio: float


def _collect_relevant_image_regions(page: fitz.Page) -> list[_ImageRegionInfo]:
    """
    Coleta todas as imagens embutidas na página cuja resolução real ou renderizada seja
    maior que 100x50px (ignorando ícones diminutos, filetes ou espaçadores).
    """
    page_rect = page.rect
    page_area = max(1.0, float(page_rect.width * page_rect.height))
    regions: list[_ImageRegionInfo] = []

    try:
        image_infos = page.get_image_info(xrefs=True)
    except Exception:
        image_infos = []

    for info in image_infos:
        bbox_raw = info.get("bbox")
        if not bbox_raw:
            continue
        bbox = fitz.Rect(bbox_raw) & page_rect
        if bbox.is_empty or bbox.width <= 1.0 or bbox.height <= 1.0:
            continue

        px_w = int(info.get("width") or 0)
        px_h = int(info.get("height") or 0)
        eff_w = max(px_w, int(bbox.width * (150.0 / 72.0)))
        eff_h = max(px_h, int(bbox.height * (150.0 / 72.0)))

        if eff_w >= MIN_RELEVANT_IMAGE_WIDTH_PX and eff_h >= MIN_RELEVANT_IMAGE_HEIGHT_PX:
            coverage = float((bbox.width * bbox.height) / page_area)
            regions.append(
                _ImageRegionInfo(
                    bbox=bbox,
                    pixel_width=eff_w,
                    pixel_height=eff_h,
                    coverage_ratio=coverage,
                )
            )

    return regions


def classify_page_type(
    native_word_count: int,
    relevant_images: list[_ImageRegionInfo],
    vector_drawing_count: int = 0,
) -> PageContentType:
    """
    Classifica a página em `TEXTUAL`, `SCANNED` ou `HYBRID` aplicando a regra Zero-Waste OCR:
    - Sem imagens relevantes (>100x50px) e com palavras nativas (ou página realmente em branco) -> `TEXTUAL` (Zero OCR)
    - Sem palavras nativas (< 5) mas com glifos vetorizados (`vector_drawing_count >= 15`, ex.: Microsoft Print To PDF) -> `SCANNED`
    - Sem palavras nativas (ou < 5 palavras isoladas) e com imagem relevante -> `SCANNED`
    - Com texto nativo significativo E imagens relevantes (>100x50px) -> `HYBRID`
    """
    if not relevant_images:
        if native_word_count < 5 and vector_drawing_count >= MIN_VECTOR_DRAWINGS_FOR_OUTLINED_TEXT:
            return PageContentType.SCANNED
        return PageContentType.TEXTUAL

    total_img_coverage = sum(img.coverage_ratio for img in relevant_images)
    if native_word_count < 5 or (native_word_count < 15 and total_img_coverage >= 0.65):
        return PageContentType.SCANNED

    return PageContentType.HYBRID


def _extract_native_words_and_lines(
    page: fitz.Page,
    page_number: int,
) -> tuple[list[UnifiedToken], list[UnifiedLine]]:
    """Extrai palavras e linhas nativas diretamente da árvore de objetos do PyMuPDF em milissegundos."""
    page_rect = page.rect
    raw_words = page.get_text("words")
    tokens: list[UnifiedToken] = []

    line_groups: dict[tuple[int, int], list[UnifiedToken]] = {}

    for w in raw_words:
        text_val = str(w[4]).strip()
        if not text_val:
            continue
        x0_n, y0_n, x1_n, y1_n = _normalize_rect((w[0], w[1], w[2], w[3]), page_rect)
        b_no, l_no, w_no = int(w[5]), int(w[6]), int(w[7])

        tok = UnifiedToken(
            text=text_val,
            page_number=page_number,
            x0_norm=x0_n,
            y0_norm=y0_n,
            x1_norm=x1_n,
            y1_norm=y1_n,
            confidence=1.0,
            source="NATIVE",
            block_no=b_no,
            line_no=l_no,
            word_no=w_no,
        )
        tokens.append(tok)
        line_groups.setdefault((b_no, l_no), []).append(tok)

    lines: list[UnifiedLine] = []
    for (_, _), group in line_groups.items():
        group.sort(key=lambda t: (t.word_no, t.x0_norm))
        line_text = " ".join(t.text for t in group)
        lx0 = min(t.x0_norm for t in group)
        ly0 = min(t.y0_norm for t in group)
        lx1 = max(t.x1_norm for t in group)
        ly1 = max(t.y1_norm for t in group)
        lines.append(
            UnifiedLine(
                text=line_text,
                page_number=page_number,
                x0_norm=lx0,
                y0_norm=ly0,
                x1_norm=lx1,
                y1_norm=ly1,
                confidence=1.0,
                source="NATIVE",
            )
        )

    lines.sort(key=lambda ln: (round(ln.y0_norm, 3), ln.x0_norm))
    return tokens, lines


def _cluster_coords(values: list[float], tolerance: float = 0.008) -> list[float]:
    """Agrupa coordenadas próximas dentro de uma tolerância normalizada."""
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters: list[list[float]] = [[sorted_vals[0]]]
    for v in sorted_vals[1:]:
        if abs(v - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [round(sum(c) / len(c), 6) for c in clusters]


def _extract_grid_rects_from_lines(
    drawings: list[dict],
    page_rect: fitz.Rect,
) -> list[tuple[float, float, float, float]]:
    """
    Reconstrói células retangulares quando tabelas (ex.: ReportLab `GRID`/`INNERGRID`) são
    desenhadas via segmentos de linha (`'l'`) horizontais e verticais em vez de retângulos individuais.
    """
    h_lines: list[tuple[float, float, float]] = []  # (y, x0, x1)
    v_lines: list[tuple[float, float, float]] = []  # (x, y0, y1)

    for d in drawings:
        items = d.get("items") or []
        for item in items:
            if not item or item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) <= 1.5 and abs(p2.x - p1.x) >= 20.0:
                h_lines.append((float(p1.y + p2.y) * 0.5, float(min(p1.x, p2.x)), float(max(p1.x, p2.x))))
            elif abs(p1.x - p2.x) <= 1.5 and abs(p2.y - p1.y) >= 10.0:
                v_lines.append((float(p1.x + p2.x) * 0.5, float(min(p1.y, p2.y)), float(max(p1.y, p2.y))))

        r = d.get("rect")
        if r and not items:
            rect_obj = fitz.Rect(r)
            if rect_obj.width >= 20.0 and rect_obj.height <= 2.5:
                h_lines.append((float(rect_obj.y0 + rect_obj.y1) * 0.5, float(rect_obj.x0), float(rect_obj.x1)))
            elif rect_obj.height >= 10.0 and rect_obj.width <= 2.5:
                v_lines.append((float(rect_obj.x0 + rect_obj.x1) * 0.5, float(rect_obj.y0), float(rect_obj.y1)))

    return _grid_rects_from_segments(h_lines, v_lines, page_rect)


def _grid_rects_from_segments(
    h_lines: list[tuple[float, float, float]],
    v_lines: list[tuple[float, float, float]],
    page_rect: fitz.Rect,
) -> list[tuple[float, float, float, float]]:
    if len(h_lines) < 3 or len(v_lines) < 2:
        return []

    pw = max(1.0, float(page_rect.width))
    ph = max(1.0, float(page_rect.height))

    ys_norm = _cluster_coords([(y - page_rect.y0) / ph for y, _, _ in h_lines], tolerance=0.005)
    xs_norm = _cluster_coords([(x - page_rect.x0) / pw for x, _, _ in v_lines], tolerance=0.005)
    if len(ys_norm) < 2 or len(xs_norm) < 2:
        return []

    rects_norm: list[tuple[float, float, float, float]] = []
    for r_i in range(len(ys_norm) - 1):
        y0_n, y1_n = ys_norm[r_i], ys_norm[r_i + 1]
        if (y1_n - y0_n) < 0.008 or (y1_n - y0_n) > 0.40:
            continue
        for c_i in range(len(xs_norm) - 1):
            x0_n, x1_n = xs_norm[c_i], xs_norm[c_i + 1]
            if (x1_n - x0_n) < 0.02:
                continue
            rects_norm.append((_clamp_norm(x0_n), _clamp_norm(y0_n), _clamp_norm(x1_n), _clamp_norm(y1_n)))

    return rects_norm


def _extract_native_tables_fast(
    page: fitz.Page,
    page_number: int,
    tokens: list[UnifiedToken],
) -> list[ExtractedTableCell]:
    """
    Reconstrói estruturas de tabelas diretamente a partir dos retângulos/linhas vetoriais
    (`page.get_drawings()`) e dos tokens já extraídos em sub-milissegundos (< 2ms),
    separando múltiplas tabelas na mesma página por faixas verticais conectadas e
    mantendo a relação cabeçalho-célula (`header_label`) sem o overhead pesado do `find_tables()`.
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    if not drawings or not tokens:
        return []

    page_rect = page.rect
    h_lines: list[tuple[float, float, float]] = []
    v_lines: list[tuple[float, float, float]] = []
    rect_cells_pts: list[fitz.Rect] = []

    for d in drawings:
        items = d.get("items") or []
        for item in items:
            if not item or item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) <= 1.5 and abs(p2.x - p1.x) >= 20.0:
                h_lines.append((float(p1.y + p2.y) * 0.5, float(min(p1.x, p2.x)), float(max(p1.x, p2.x))))
            elif abs(p1.x - p2.x) <= 1.5 and abs(p2.y - p1.y) >= 10.0:
                v_lines.append((float(p1.x + p2.x) * 0.5, float(min(p1.y, p2.y)), float(max(p1.y, p2.y))))

        r = d.get("rect")
        if not r:
            continue
        rect_obj = fitz.Rect(r)
        if not items:
            if rect_obj.width >= 20.0 and rect_obj.height <= 2.5:
                h_lines.append((float(rect_obj.y0 + rect_obj.y1) * 0.5, float(rect_obj.x0), float(rect_obj.x1)))
            elif rect_obj.height >= 10.0 and rect_obj.width <= 2.5:
                v_lines.append((float(rect_obj.x0 + rect_obj.x1) * 0.5, float(rect_obj.y0), float(rect_obj.y1)))
        if 15.0 <= rect_obj.width <= page_rect.width * 0.90 and 8.0 <= rect_obj.height <= page_rect.height * 0.40:
            rect_cells_pts.append(rect_obj)

    y_intervals: list[tuple[float, float]] = [(vy0, vy1) for _, vy0, vy1 in v_lines] + [
        (float(rc.y0), float(rc.y1)) for rc in rect_cells_pts
    ]
    if not y_intervals:
        return []

    y_intervals.sort(key=lambda iv: (iv[0], iv[1]))
    y_bands: list[list[float]] = [[y_intervals[0][0], y_intervals[0][1]]]
    for y0_i, y1_i in y_intervals[1:]:
        if y0_i <= y_bands[-1][1] + 2.5:
            if y1_i > y_bands[-1][1]:
                y_bands[-1][1] = y1_i
        else:
            y_bands.append([y0_i, y1_i])

    extracted_cells: list[ExtractedTableCell] = []
    table_counter = 0

    for band_y0, band_y1 in y_bands:
        band_h = [hl for hl in h_lines if (band_y0 - 2.5) <= hl[0] <= (band_y1 + 2.5)]
        band_v = [vl for vl in v_lines if vl[2] >= (band_y0 - 2.5) and vl[1] <= (band_y1 + 2.5)]
        band_rect_norm = [
            _normalize_rect(rc, page_rect)
            for rc in rect_cells_pts
            if rc.y1 >= (band_y0 - 2.5) and rc.y0 <= (band_y1 + 2.5)
        ]
        band_grid_norm = _grid_rects_from_segments(band_h, band_v, page_rect)
        cell_rects_norm = (
            band_grid_norm
            if len(band_grid_norm) >= max(2, len(band_rect_norm))
            else band_rect_norm
        )
        if len(cell_rects_norm) < 2:
            continue

        unique_y0s = _cluster_coords([c[1] for c in cell_rects_norm], tolerance=0.008)
        unique_x0s = _cluster_coords([c[0] for c in cell_rects_norm], tolerance=0.008)
        if len(unique_y0s) < 2 or len(unique_x0s) < 1:
            continue

        grid_cells: dict[tuple[int, int], tuple[float, float, float, float, str, tuple[UnifiedToken, ...]]] = {}
        for x0_n, y0_n, x1_n, y1_n in cell_rects_norm:
            r_idx = min(range(len(unique_y0s)), key=lambda i: abs(unique_y0s[i] - y0_n))
            c_idx = min(range(len(unique_x0s)), key=lambda i: abs(unique_x0s[i] - x0_n))

            inside_tokens = [
                t
                for t in tokens
                if (t.x0_norm + t.x1_norm) * 0.5 >= (x0_n - 0.003)
                and (t.x0_norm + t.x1_norm) * 0.5 <= (x1_n + 0.003)
                and (t.y0_norm + t.y1_norm) * 0.5 >= (y0_n - 0.003)
                and (t.y0_norm + t.y1_norm) * 0.5 <= (y1_n + 0.003)
            ]
            if inside_tokens:
                inside_tokens.sort(key=lambda t: (t.y0_norm, t.x0_norm))
                line_tol = 3.0 / max(1.0, float(page_rect.height))
                y_lines: list[list[UnifiedToken]] = [[inside_tokens[0]]]
                for tok in inside_tokens[1:]:
                    if abs(tok.y0_norm - y_lines[-1][0].y0_norm) <= line_tol:
                        y_lines[-1].append(tok)
                    else:
                        y_lines.append([tok])
                inside_tokens = [tok for grp in y_lines for tok in sorted(grp, key=lambda t: t.x0_norm)]
            cell_text = " ".join(t.text for t in inside_tokens).strip()
            if cell_text:
                grid_cells[(r_idx, c_idx)] = (x0_n, y0_n, x1_n, y1_n, cell_text, tuple(inside_tokens))

        if not grid_cells:
            continue

        headers_by_col: dict[int, str] = {}
        for (r_idx, c_idx), (_, _, _, _, txt, _) in grid_cells.items():
            if r_idx == 0:
                headers_by_col[c_idx] = txt

        for (r_idx, c_idx) in sorted(grid_cells.keys()):
            x0_n, y0_n, x1_n, y1_n, txt, c_tokens = grid_cells[(r_idx, c_idx)]
            header_label = headers_by_col.get(c_idx, f"COL_{c_idx + 1}")
            extracted_cells.append(
                ExtractedTableCell(
                    page_number=page_number,
                    table_index=table_counter,
                    row_index=r_idx,
                    col_index=c_idx,
                    header_label=header_label,
                    cell_text=txt,
                    x0_norm=x0_n,
                    y0_norm=y0_n,
                    x1_norm=x1_n,
                    y1_norm=y1_n,
                    cell_tokens=c_tokens,
                )
            )
        table_counter += 1

    return extracted_cells


def _boxes_overlap_norm(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    iou_threshold: float = 0.25,
) -> bool:
    ix0 = max(a[0], b[0])
    iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2])
    iy1 = min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return (inter / min(area_a, area_b)) >= iou_threshold


def _tokens_inside_norm_rect(
    tokens: list[UnifiedToken] | None,
    x0_n: float,
    y0_n: float,
    x1_n: float,
    y1_n: float,
    *,
    inset: float = 0.0,
) -> list[UnifiedToken]:
    """Retorna tokens cujo centro geométrico está contido no retângulo normalizado."""
    if not tokens:
        return []
    ix0 = x0_n + inset
    iy0 = y0_n + inset
    ix1 = x1_n - inset
    iy1 = y1_n - inset
    if ix1 <= ix0 or iy1 <= iy0:
        return []
    inside: list[UnifiedToken] = []
    for t in tokens:
        cx = 0.5 * (t.x0_norm + t.x1_norm)
        cy = 0.5 * (t.y0_norm + t.y1_norm)
        if ix0 <= cx <= ix1 and iy0 <= cy <= iy1:
            inside.append(t)
    return inside


def _find_anchor_photo_frame(
    arr: np.ndarray,
    ln: UnifiedLine,
    page_w_pts: float,
    page_h_pts: float,
) -> tuple[float, float, float, float, float, str]:
    """
    Localiza a moldura retangular 3x4 real ao redor ou imediatamente acima de uma linha âncora
    (`FOTO 3x4` / `FOTO 3X4 ESCANEADA`). Retorna `(x0_n, y0_n, x1_n, y1_n, conf, method)`.
    """
    h_px, w_px = arr.shape
    cx_n = 0.5 * (ln.x0_norm + ln.x1_norm)
    ln_w_n = max(0.06, ln.x1_norm - ln.x0_norm)

    sx0 = max(0, int((cx_n - 0.17) * w_px))
    sx1 = min(w_px, int((cx_n + 0.17) * w_px))
    sy0 = max(0, int((ln.y0_norm - 0.24) * h_px))
    sy1 = min(h_px, int((ln.y1_norm + 0.16) * h_px))

    if (sx1 - sx0) >= 30 and (sy1 - sy0) >= 30:
        patch = arr[sy0:sy1, sx0:sx1] < 185
        ph, pw = patch.shape
        ln_cx_local = int(cx_n * w_px) - sx0
        ln_y0_local = max(0, int(ln.y0_norm * h_px) - sy0)
        ln_y1_local = min(ph, int(ln.y1_norm * h_px) - sy0)

        col_sums = patch.sum(axis=0)
        min_v_pixels = max(25, int(ph * 0.22))
        gap_min = max(12, int(0.03 * w_px))
        left_cands = [c for c in range(0, max(1, ln_cx_local - gap_min)) if col_sums[c] >= min_v_pixels]
        right_cands = [c for c in range(min(pw - 1, ln_cx_local + gap_min), pw) if col_sums[c] >= min_v_pixels]

        if left_cands and right_cands:
            best_pair: tuple[int, int] | None = None
            best_score = -1e9
            for lc in left_cands:
                for rc in right_cands:
                    bw = rc - lc
                    if 0.075 * w_px <= bw <= 0.22 * w_px:
                        pair_center = 0.5 * (lc + rc)
                        score = float(col_sums[lc] + col_sums[rc]) - abs(pair_center - ln_cx_local) * 0.8
                        if score > best_score:
                            best_score = score
                            best_pair = (lc, rc)

            if best_pair is not None:
                bx0_loc, bx1_loc = best_pair
                bw = bx1_loc - bx0_loc
                sub = patch[:, bx0_loc : bx1_loc + 1]
                row_fracs = sub.mean(axis=1)
                h_border_rows = [r for r in range(ph) if row_fracs[r] >= 0.50]

                expected_min_h = bw / 0.92
                expected_max_h = bw / 0.52
                best_rect: tuple[int, int] | None = None
                best_rect_score = -1.0

                for t_r in h_border_rows:
                    if t_r > ln_y1_local + int(0.02 * h_px):
                        continue
                    for b_r in h_border_rows:
                        bh = b_r - t_r
                        if not (expected_min_h <= bh <= expected_max_h):
                            continue
                        if b_r < ln_y0_local - int(0.04 * h_px):
                            continue
                        left_cov = float(
                            patch[t_r : b_r + 1, max(0, bx0_loc - 1) : min(pw, bx0_loc + 2)].any(axis=1).mean()
                        )
                        right_cov = float(
                            patch[t_r : b_r + 1, max(0, bx1_loc - 1) : min(pw, bx1_loc + 2)].any(axis=1).mean()
                        )
                        if left_cov >= 0.55 and right_cov >= 0.55:
                            rect_score = (left_cov + right_cov) + float(row_fracs[t_r] + row_fracs[b_r])
                            if rect_score > best_rect_score:
                                best_rect_score = rect_score
                                best_rect = (t_r, b_r)

                if best_rect is not None:
                    chosen_top, chosen_bot = best_rect
                    return (
                        _clamp_norm((sx0 + bx0_loc) / w_px),
                        _clamp_norm((sy0 + chosen_top) / h_px),
                        _clamp_norm((sx0 + bx1_loc + 1) / w_px),
                        _clamp_norm((sy0 + chosen_bot + 1) / h_px),
                        0.94,
                        "SCAN_OCR_ANCHOR_3X4_FRAME",
                    )

    box_w_n = max(min(0.20, max(0.125, ln_w_n + 0.025)), 0.11)
    box_w_pts = box_w_n * page_w_pts
    target_h_pts = box_w_pts / 0.75
    box_h_n = target_h_pts / max(1.0, page_h_pts)

    x0_n = _clamp_norm(cx_n - box_w_n * 0.5)
    x1_n = _clamp_norm(cx_n + box_w_n * 0.5)
    y1_n = _clamp_norm(min(1.0, ln.y1_norm + 0.012))
    y0_n = _clamp_norm(max(0.0, y1_n - box_h_n))
    return (x0_n, y0_n, x1_n, y1_n, 0.88, "SCAN_OCR_ANCHOR_3X4_BOX")


def _scan_unanchored_portrait_frames(
    arr: np.ndarray,
    existing_boxes: list[tuple[float, float, float, float]],
    *,
    tokens: list[UnifiedToken] | None = None,
    max_candidates: int = 2,
) -> list[tuple[float, float, float, float, float, str]]:
    """
    Varre a página escaneada em busca de molduras retangulares verticais 3x4 (`w_norm` entre 0.08 e 0.20,
    razão de aspecto entre 0.65 e 0.85 e conteúdo interno não vazio) mesmo sem legenda OCR explícita.
    Rejeita células de tabela/fatura contendo texto impresso interno e bordas laterais de scanner.
    """
    h_px, w_px = arr.shape
    dark = arr < 180
    h_dil = dark.copy()
    h_dil[:, 1:] |= dark[:, :-1]
    h_dil[:, :-1] |= dark[:, 1:]

    min_w_px = max(20, int(0.08 * w_px))
    max_w_px = max(min_w_px + 5, int(0.20 * w_px))

    h_segments: list[tuple[int, int, int]] = []
    padded = np.pad(h_dil.astype(np.uint8), ((0, 0), (1, 1)), mode="constant", constant_values=0)
    diffs = np.diff(padded.astype(np.int8), axis=1)
    ys_s, xs_s = np.where(diffs == 1)
    ys_e, xs_e = np.where(diffs == -1)

    for y, x0, x1 in zip(ys_s, xs_s, xs_e):
        seg_w = int(x1 - x0)
        if min_w_px <= seg_w <= max_w_px:
            h_segments.append((int(y), int(x0), int(x1)))

    if not h_segments:
        return []

    # Desduplica segmentos horizontais consecutivos pertencentes à mesma borda de 2..4px de espessura
    dedup_segs: list[tuple[int, int, int]] = []
    for seg in h_segments:
        if (
            dedup_segs
            and abs(seg[0] - dedup_segs[-1][0]) <= 3
            and abs(seg[1] - dedup_segs[-1][1]) <= 6
            and abs(seg[2] - dedup_segs[-1][2]) <= 6
        ):
            continue
        dedup_segs.append(seg)

    tol_x = max(5, int(0.014 * w_px))
    scored_candidates: list[tuple[float, tuple[float, float, float, float]]] = []

    for i, (y_top, tx0, tx1) in enumerate(dedup_segs):
        for y_bot, bx0, bx1 in dedup_segs[i + 1 :]:
            bh = y_bot - y_top
            bw = 0.5 * ((tx1 - tx0) + (bx1 - bx0))
            if bh <= 0:
                continue
            aspect = bw / bh
            if aspect < 0.62:
                break
            if aspect > 0.88:
                continue
            if abs(tx0 - bx0) > tol_x or abs(tx1 - bx1) > tol_x:
                continue

            x0 = max(0, min(tx0, bx0))
            x1 = min(w_px - 1, max(tx1, bx1))
            if not (min_w_px <= (x1 - x0) <= max_w_px):
                continue

            cand_norm = (
                _clamp_norm(x0 / w_px),
                _clamp_norm(y_top / h_px),
                _clamp_norm((x1 + 1) / w_px),
                _clamp_norm((y_bot + 1) / h_px),
            )
            # Rejeita artefatos encostados nas margens extremas do scanner / sombras de folha
            if cand_norm[0] < 0.04 or cand_norm[2] > 0.945 or cand_norm[1] < 0.02 or cand_norm[3] > 0.98:
                continue

            left_cov = float(dark[y_top : y_bot + 1, max(0, x0 - 2) : min(w_px, x0 + 3)].any(axis=1).mean())
            right_cov = float(dark[y_top : y_bot + 1, max(0, x1 - 2) : min(w_px, x1 + 3)].any(axis=1).mean())
            if left_cov < 0.72 or right_cov < 0.72:
                continue

            interior = arr[y_top + 4 : max(y_top + 5, y_bot - 4), x0 + 4 : max(x0 + 5, x1 - 4)]
            if interior.size == 0:
                continue
            int_std = float(interior.std())
            if int_std <= 14.0:
                continue

            # Rejeita células de tabela/fatura que contêm texto impresso nos 78% superiores do quadro
            # (preservando legendas pequenas "FOTO 3X4" na faixa inferior de molduras fotográficas)
            upper_body_y1 = cand_norm[1] + (cand_norm[3] - cand_norm[1]) * 0.78
            interior_toks = _tokens_inside_norm_rect(
                tokens,
                cand_norm[0],
                cand_norm[1],
                cand_norm[2],
                upper_body_y1,
                inset=0.006,
            )
            if interior_toks:
                total_alnum = sum(sum(1 for ch in t.text if ch.isalnum()) for t in interior_toks)
                if len(interior_toks) >= 2 or total_alnum >= 8:
                    continue

            if any(_boxes_overlap_norm(cand_norm, ex, iou_threshold=0.25) for ex in existing_boxes):
                continue

            score = (left_cov + right_cov) + min(1.0, int_std / 60.0)
            scored_candidates.append((score, cand_norm))

    scored_candidates.sort(key=lambda item: -item[0])
    found: list[tuple[float, float, float, float, float, str]] = []
    for _score, cand_norm in scored_candidates:
        if len(found) >= max_candidates:
            break
        if any(_boxes_overlap_norm(cand_norm, ex, iou_threshold=0.25) for ex in existing_boxes):
            continue
        existing_boxes.append(cand_norm)
        found.append((*cand_norm, 0.86, "SCAN_CONTOUR_3X4_FRAME"))

    return found


def _detect_rg_card_visuals(
    page_number: int,
    lines: list[UnifiedLine],
    existing_boxes: list[tuple[float, float, float, float]],
) -> list[DetectedVisualElement]:
    """
    Detecta biometria digital (`POLEGAR DIREITO`), foto 3x4 e assinatura manuscrita em frentes de
    Carteira de Identidade (RG) escaneadas usando as âncoras textuais oficiais do documento.
    """
    polegar_lines = [ln for ln in lines if _POLEGAR_ANCHOR_RE.search(ln.text)]
    if not polegar_lines:
        return []

    has_rg_context = any(_ID_CARD_ANCHOR_RE.search(ln.text) for ln in lines)
    detected: list[DetectedVisualElement] = []

    for ln in polegar_lines:
        w = max(0.11, min(0.26, ln.x1_norm - ln.x0_norm))
        cx = 0.5 * (ln.x0_norm + ln.x1_norm)

        # 1. Impressão digital do polegar imediatamente abaixo do rótulo "POLEGAR DIREITO"
        thumb_box = (
            _clamp_norm(cx - 0.90 * w),
            _clamp_norm(ln.y1_norm),
            _clamp_norm(cx + 0.90 * w),
            _clamp_norm(min(0.95, ln.y1_norm + 1.80 * w)),
        )
        if (thumb_box[2] - thumb_box[0]) >= 0.08 and (thumb_box[3] - thumb_box[1]) >= 0.10:
            if not any(_boxes_overlap_norm(thumb_box, ex, iou_threshold=0.35) for ex in existing_boxes):
                existing_boxes.append(thumb_box)
                detected.append(
                    DetectedVisualElement(
                        page_number=page_number,
                        kind=VisualElementKind.FACIAL_PHOTO,
                        x0_norm=thumb_box[0],
                        y0_norm=thumb_box[1],
                        x1_norm=thumb_box[2],
                        y1_norm=thumb_box[3],
                        confidence=0.92,
                        detection_method="OCR_ANCHOR_BIOMETRIC_THUMBPRINT",
                    )
                )

        # 2. Foto 3x4 acima do polegar + assinatura do titular à direita na frente do RG
        if has_rg_context and ln.y0_norm >= 0.30:
            photo_box = (
                _clamp_norm(cx - 0.96 * w),
                _clamp_norm(max(0.05, ln.y0_norm - 2.15 * w)),
                _clamp_norm(cx + 0.96 * w),
                _clamp_norm(max(0.08, ln.y0_norm - 0.15 * w)),
            )
            if (photo_box[2] - photo_box[0]) >= 0.10 and (photo_box[3] - photo_box[1]) >= 0.12:
                if not any(_boxes_overlap_norm(photo_box, ex, iou_threshold=0.35) for ex in existing_boxes):
                    existing_boxes.append(photo_box)
                    detected.append(
                        DetectedVisualElement(
                            page_number=page_number,
                            kind=VisualElementKind.FACIAL_PHOTO,
                            x0_norm=photo_box[0],
                            y0_norm=photo_box[1],
                            x1_norm=photo_box[2],
                            y1_norm=photo_box[3],
                            confidence=0.91,
                            detection_method="OCR_ANCHOR_RG_PORTRAIT",
                        )
                    )

            sig_x0 = _clamp_norm(cx + 0.85 * w)
            sig_x1 = _clamp_norm(min(0.92, cx + 1.75 * w))
            sig_y0 = _clamp_norm(max(0.12, ln.y0_norm - 1.75 * w))
            sig_y1 = _clamp_norm(min(0.93, ln.y1_norm + 1.70 * w))
            if (sig_x1 - sig_x0) >= 0.06 and (sig_y1 - sig_y0) >= 0.20:
                detected.append(
                    DetectedVisualElement(
                        page_number=page_number,
                        kind=VisualElementKind.VISUAL_SIGNATURE,
                        x0_norm=sig_x0,
                        y0_norm=sig_y0,
                        x1_norm=sig_x1,
                        y1_norm=sig_y1,
                        confidence=0.88,
                        detection_method="OCR_ANCHOR_RG_SIGNATURE",
                    )
                )

    return detected


def _detect_scanned_3x4_photos(
    page: fitz.Page,
    page_number: int,
    page_type: PageContentType,
    lines: list[UnifiedLine],
    existing_visuals: list[DetectedVisualElement],
    tokens: list[UnifiedToken] | None = None,
) -> list[DetectedVisualElement]:
    """
    Detecta automaticamente fotografias/molduras 3x4 e biometria em páginas escaneadas (`SCANNED`)
    ou híbridas (`HYBRID`):
    1. Âncoras de RG (`POLEGAR DIREITO`, foto 3x4 e assinatura do titular);
    2. Âncora textual OCR (`FOTO 3x4` / `FOTO 3X4 ESCANEADA`) localizando a moldura ao redor;
    3. Varredura geométrica filtrada de retângulos verticais 3x4 sem texto impresso interno.
    """
    if page_type not in (PageContentType.SCANNED, PageContentType.HYBRID):
        return []

    existing_boxes: list[tuple[float, float, float, float]] = [
        (v.x0_norm, v.y0_norm, v.x1_norm, v.y1_norm)
        for v in existing_visuals
        if v.kind == VisualElementKind.FACIAL_PHOTO
    ]
    detected: list[DetectedVisualElement] = []

    # 1. Âncoras oficiais de Carteira de Identidade (RG): polegar biométrico + foto + assinatura
    rg_visuals = _detect_rg_card_visuals(page_number, lines, existing_boxes)
    if rg_visuals:
        detected.extend(rg_visuals)
        # Em páginas de RG já ancoradas, evita varredura não ancorada sobre bordas decorativas do documento
        return detected

    anchor_lines = [ln for ln in lines if _PHOTO_ANCHOR_RE.search(ln.text)]
    if page_type == PageContentType.HYBRID and not anchor_lines:
        return []

    pix = page.get_pixmap(dpi=100, colorspace=fitz.csGRAY, alpha=False)
    if pix.width < 40 or pix.height < 40:
        return []

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    page_rect = page.rect
    page_w_pts = max(1.0, float(page_rect.width))
    page_h_pts = max(1.0, float(page_rect.height))

    for ln in anchor_lines:
        x0_n, y0_n, x1_n, y1_n, conf, method = _find_anchor_photo_frame(arr, ln, page_w_pts, page_h_pts)
        cand_norm = (x0_n, y0_n, x1_n, y1_n)
        if any(_boxes_overlap_norm(cand_norm, ex, iou_threshold=0.25) for ex in existing_boxes):
            continue
        existing_boxes.append(cand_norm)
        detected.append(
            DetectedVisualElement(
                page_number=page_number,
                kind=VisualElementKind.FACIAL_PHOTO,
                x0_norm=x0_n,
                y0_norm=y0_n,
                x1_norm=x1_n,
                y1_norm=y1_n,
                confidence=conf,
                detection_method=method,
            )
        )

    joined_page_text = "\n".join(ln.text for ln in lines if ln.text)
    if page_type == PageContentType.SCANNED and (anchor_lines or not _NON_PORTRAIT_DOC_RE.search(joined_page_text)):
        for x0_n, y0_n, x1_n, y1_n, conf, method in _scan_unanchored_portrait_frames(
            arr,
            existing_boxes,
            tokens=tokens,
        ):
            detected.append(
                DetectedVisualElement(
                    page_number=page_number,
                    kind=VisualElementKind.FACIAL_PHOTO,
                    x0_norm=x0_n,
                    y0_norm=y0_n,
                    x1_norm=x1_n,
                    y1_norm=y1_n,
                    confidence=conf,
                    detection_method=method,
                )
            )

    return detected


def _detect_visual_signatures_and_photos(
    page: fitz.Page,
    page_number: int,
    relevant_images: list[_ImageRegionInfo],
    lines: list[UnifiedLine],
    page_type: PageContentType = PageContentType.TEXTUAL,
    tokens: list[UnifiedToken] | None = None,
) -> list[DetectedVisualElement]:
    """
    Identifica caixas delimitadoras de:
    1. Fotografias 3x4 / faciais e biometria (`FACIAL_PHOTO`) em imagens raster embutidas
       (protegendo brasões/logotipos institucionais centralizados no topo da página),
       em âncoras OCR (`FOTO 3x4`, `POLEGAR DIREITO`) ou em molduras escaneadas 3x4 sem texto interno;
    2. Assinaturas visuais/manuscritas (`VISUAL_SIGNATURE`) em imagens horizontais recortadas
       (aspect ratio >= 1.8, fora da faixa de cabeçalho institucional e sem texto impresso OCR interno),
       sobre linhas de traço de assinatura ("________") ou ao lado de âncoras `Ciente <data>`.
    """
    page_rect = page.rect
    detected: list[DetectedVisualElement] = []

    has_top_institutional_header = any(
        ln.y0_norm < 0.32 and _INSTITUTIONAL_HEADER_TEXT_RE.search(ln.text)
        for ln in lines
    )
    has_photo_or_id_anchor = any(
        _PHOTO_ANCHOR_RE.search(ln.text) or _ID_CARD_ANCHOR_RE.search(ln.text)
        for ln in lines
    )

    for img in relevant_images:
        if img.coverage_ratio > 0.45:
            continue

        x0_n, y0_n, x1_n, y1_n = _normalize_rect(img.bbox, page_rect)
        w_pts = float(img.bbox.width)
        h_pts = float(img.bbox.height)
        if h_pts <= 0 or w_pts <= 0:
            continue

        aspect = w_pts / h_pts
        cx_n = 0.5 * (x0_n + x1_n)
        inside_toks = _tokens_inside_norm_rect(tokens, x0_n, y0_n, x1_n, y1_n)
        inside_alnum = sum(sum(1 for ch in t.text if ch.isalnum()) for t in inside_toks)

        if 0.60 <= aspect <= 0.92 and 0.008 <= img.coverage_ratio <= 0.18:
            # Protege brasões oficiais centralizados no cabeçalho (ex.: Brasão TJAM / República / UFAM)
            is_top_center_crest = (
                y0_n < 0.22
                and 0.34 <= cx_n <= 0.66
                and has_top_institutional_header
                and not has_photo_or_id_anchor
            )
            if is_top_center_crest:
                continue
            # Foto facial real não contém bloco extenso de texto impresso no interior
            if len(inside_toks) >= 3 and inside_alnum >= 12 and not has_photo_or_id_anchor:
                continue

            detected.append(
                DetectedVisualElement(
                    page_number=page_number,
                    kind=VisualElementKind.FACIAL_PHOTO,
                    x0_norm=x0_n,
                    y0_norm=y0_n,
                    x1_norm=x1_n,
                    y1_norm=y1_n,
                    confidence=0.88,
                    detection_method="IMAGE_ASPECT_3X4",
                )
            )
        elif aspect >= 1.8 and 0.003 <= img.coverage_ratio <= 0.15:
            # Banners de cabeçalho institucional (ex.: "ESTADO DO AMAZONAS / PODER JUDICIÁRIO")
            # jamais são assinaturas manuscritas
            if y0_n < 0.28:
                continue
            if len(inside_toks) >= 2 and inside_alnum >= 8:
                continue

            detected.append(
                DetectedVisualElement(
                    page_number=page_number,
                    kind=VisualElementKind.VISUAL_SIGNATURE,
                    x0_norm=x0_n,
                    y0_norm=y0_n,
                    x1_norm=x1_n,
                    y1_norm=y1_n,
                    confidence=0.85,
                    detection_method="IMAGE_ASPECT_SIGNATURE",
                )
            )

    detected.extend(
        _detect_scanned_3x4_photos(
            page=page,
            page_number=page_number,
            page_type=page_type,
            lines=lines,
            existing_visuals=detected,
            tokens=tokens,
        )
    )

    gray_arr_cache: np.ndarray | None = None
    for idx, ln in enumerate(lines):
        if _UNDERSCORE_SIGNATURE_LINE_RE.search(ln.text) and ln.y0_norm >= 0.60:
            next_ln = lines[idx + 1] if idx + 1 < len(lines) else None
            if next_ln and _STRICT_SIGNATURE_LABEL_RE.search(next_ln.text):
                detected.append(
                    DetectedVisualElement(
                        page_number=page_number,
                        kind=VisualElementKind.VISUAL_SIGNATURE,
                        x0_norm=ln.x0_norm,
                        y0_norm=ln.y0_norm,
                        x1_norm=ln.x1_norm,
                        y1_norm=ln.y1_norm,
                        confidence=0.82,
                        detection_method="TEXT_ANCHOR_SIGNATURE_ZONE",
                    )
                )
        elif (
            page_type == PageContentType.SCANNED
            and ln.y0_norm >= 0.45
            and ln.x1_norm <= 0.72
            and _CIENTE_SIGNATURE_RE.search(ln.text)
        ):
            # Verifica tinta/traço de assinatura manuscrita à direita de "Ciente DD de mês de AAAA"
            if gray_arr_cache is None:
                pix_sig = page.get_pixmap(dpi=100, colorspace=fitz.csGRAY, alpha=False)
                if pix_sig.width >= 40 and pix_sig.height >= 40:
                    gray_arr_cache = np.frombuffer(pix_sig.samples, dtype=np.uint8).reshape(
                        pix_sig.height, pix_sig.width
                    )
            if gray_arr_cache is not None:
                h_px, w_px = gray_arr_cache.shape
                sx0_n = _clamp_norm(ln.x1_norm + 0.004)
                sx1_n = _clamp_norm(min(0.86, ln.x1_norm + 0.36))
                sy0_n = _clamp_norm(max(0.0, ln.y0_norm - 0.032))
                sy1_n = _clamp_norm(min(0.92, ln.y1_norm + 0.014))
                px0, px1 = int(sx0_n * w_px), int(sx1_n * w_px)
                py0, py1 = int(sy0_n * h_px), int(sy1_n * h_px)
                if (px1 - px0) >= 20 and (py1 - py0) >= 8:
                    patch = gray_arr_cache[py0:py1, px0:px1]
                    ink_ratio = float((patch < 200).mean())
                    if 0.02 <= ink_ratio <= 0.45:
                        detected.append(
                            DetectedVisualElement(
                                page_number=page_number,
                                kind=VisualElementKind.VISUAL_SIGNATURE,
                                x0_norm=sx0_n,
                                y0_norm=sy0_n,
                                x1_norm=sx1_n,
                                y1_norm=sy1_n,
                                confidence=0.84,
                                detection_method="TEXT_ANCHOR_CIENTE_SIGNATURE",
                            )
                        )

    return detected


def extract_single_page(
    page: fitz.Page,
    *,
    job_id: uuid.UUID | str | None = None,
    page_number: int | None = None,
    render_webp: bool = True,
    base_temp_dir: Path | None = None,
) -> PageExtractionResult:
    """
    Executa a análise posicional completa de **uma única página** (`fitz.Page`):
    - Renderiza opcionalmente a imagem `.webp` a 150 DPI para visualização Thin-Client;
    - Classifica a página como `TEXTUAL`, `SCANNED` ou `HYBRID` (detectando também páginas de texto vetorizado);
    - Extrai texto nativo + tabelas em milissegundos nas páginas `TEXTUAL` / `HYBRID`;
    - Aciona OCR ONNX seletivo a 200 DPI (com fallback a 300 DPI) apenas em páginas `SCANNED`
      ou nas sub-regiões de imagem de páginas `HYBRID`;
    - Normaliza todas as coordenadas para `[0.0, 1.0]`.
    """
    t_start = time.perf_counter()
    resolved_page_number = page_number if page_number is not None else (int(page.number) + 1)
    page_rect = page.rect

    native_tokens, native_lines = _extract_native_words_and_lines(page, resolved_page_number)

    relevant_images = _collect_relevant_image_regions(page)
    vector_drawing_count = 0
    if len(native_tokens) < 5 and not relevant_images:
        try:
            vector_drawing_count = len(page.get_drawings())
        except Exception:
            vector_drawing_count = 0

    page_type = classify_page_type(
        len(native_tokens),
        relevant_images,
        vector_drawing_count=vector_drawing_count,
    )

    tokens: list[UnifiedToken] = list(native_tokens)
    lines: list[UnifiedLine] = list(native_lines)
    table_cells: list[ExtractedTableCell] = []

    ocr_executed = False
    ocr_dpi_used: int | None = None
    ocr_mean_conf: float | None = None

    if page_type == PageContentType.TEXTUAL:
        table_cells = _extract_native_tables_fast(page, resolved_page_number, native_tokens)

    elif page_type == PageContentType.SCANNED:
        ocr_executed = True
        ocr_res = run_ocr_on_page(page)
        ocr_dpi_used = ocr_res.dpi_used
        ocr_mean_conf = ocr_res.mean_confidence

        for ocr_ln in ocr_res.lines:
            lines.append(
                UnifiedLine(
                    text=ocr_ln.text,
                    page_number=resolved_page_number,
                    x0_norm=ocr_ln.x0_norm,
                    y0_norm=ocr_ln.y0_norm,
                    x1_norm=ocr_ln.x1_norm,
                    y1_norm=ocr_ln.y1_norm,
                    confidence=ocr_ln.confidence,
                    source="OCR",
                )
            )
        for w_idx, ocr_tok in enumerate(ocr_res.tokens):
            tokens.append(
                UnifiedToken(
                    text=ocr_tok.text,
                    page_number=resolved_page_number,
                    x0_norm=ocr_tok.x0_norm,
                    y0_norm=ocr_tok.y0_norm,
                    x1_norm=ocr_tok.x1_norm,
                    y1_norm=ocr_tok.y1_norm,
                    confidence=ocr_tok.confidence,
                    source="OCR",
                    block_no=0,
                    line_no=0,
                    word_no=w_idx,
                )
            )

    elif page_type == PageContentType.HYBRID:
        table_cells = _extract_native_tables_fast(page, resolved_page_number, native_tokens)
        ocr_executed = True
        conf_accum: list[float] = []
        max_dpi_used = 200

        for img_region in relevant_images:
            ocr_res = run_ocr_on_page(page, clip_rect=img_region.bbox)
            max_dpi_used = max(max_dpi_used, ocr_res.dpi_used)
            if ocr_res.lines:
                conf_accum.append(ocr_res.mean_confidence)

            for ocr_ln in ocr_res.lines:
                lines.append(
                    UnifiedLine(
                        text=ocr_ln.text,
                        page_number=resolved_page_number,
                        x0_norm=ocr_ln.x0_norm,
                        y0_norm=ocr_ln.y0_norm,
                        x1_norm=ocr_ln.x1_norm,
                        y1_norm=ocr_ln.y1_norm,
                        confidence=ocr_ln.confidence,
                        source="OCR",
                    )
                )
            for w_idx, ocr_tok in enumerate(ocr_res.tokens):
                tokens.append(
                    UnifiedToken(
                        text=ocr_tok.text,
                        page_number=resolved_page_number,
                        x0_norm=ocr_tok.x0_norm,
                        y0_norm=ocr_tok.y0_norm,
                        x1_norm=ocr_tok.x1_norm,
                        y1_norm=ocr_tok.y1_norm,
                        confidence=ocr_tok.confidence,
                        source="OCR",
                        block_no=0,
                        line_no=0,
                        word_no=w_idx,
                    )
                )

        ocr_dpi_used = max_dpi_used
        ocr_mean_conf = round(sum(conf_accum) / len(conf_accum), 4) if conf_accum else 1.0

    visual_elements = _detect_visual_signatures_and_photos(
        page=page,
        page_number=resolved_page_number,
        relevant_images=relevant_images,
        lines=lines,
        page_type=page_type,
        tokens=tokens,
    )

    preview_webp: RenderedPagePreview | None = None
    if render_webp and job_id is not None:
        preview_webp = render_page_to_webp(
            page=page,
            job_id=job_id,
            page_num=resolved_page_number,
            base_temp_dir=base_temp_dir,
        )

    elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)

    return PageExtractionResult(
        page_number=resolved_page_number,
        page_type=page_type,
        page_width_pts=float(page_rect.width),
        page_height_pts=float(page_rect.height),
        tokens=tokens,
        lines=lines,
        table_cells=table_cells,
        visual_elements=visual_elements,
        preview_webp=preview_webp,
        ocr_executed=ocr_executed,
        ocr_dpi_used=ocr_dpi_used,
        ocr_mean_confidence=ocr_mean_conf,
        processing_time_ms=elapsed_ms,
    )


def extract_pdf_document_pages(
    pdf_source: Path | bytes,
    *,
    job_id: uuid.UUID | str | None = None,
    render_webp: bool = True,
    base_temp_dir: Path | None = None,
) -> list[PageExtractionResult]:
    """
    Itera sobre todas as páginas de um PDF delegando para `extract_single_page`.
    """
    resolved_job_id = str(job_id) if job_id is not None else str(uuid.uuid4())
    if isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        doc = fitz.open(str(pdf_source))

    results: list[PageExtractionResult] = []
    try:
        for idx in range(len(doc)):
            page = doc[idx]
            page_res = extract_single_page(
                page,
                job_id=resolved_job_id,
                page_number=idx + 1,
                render_webp=render_webp,
                base_temp_dir=base_temp_dir,
            )
            results.append(page_res)
    finally:
        doc.close()

    return results
