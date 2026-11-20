"""
Motor de OCR Acelerado via ONNX (`ocr_engine.py`).

Executa inferência em CPU utilizando `rapidocr-onnxruntime` (pesos PaddleOCR otimizados para ONNX):
- Renderiza a página ou recorte a **200 DPI** (`fitz.Matrix(200/72, 200/72)`);
- Caso o score médio de confiança da página fique abaixo de `0.70`, executa fallback automático
  a **300 DPI** (`fitz.Matrix(300/72, 300/72)`) com pré-processamento de contraste e nitidez;
- Executa resgate cirúrgico por linha via `tesseract` (`-l por --psm 7 tsv`, 100% em memória via
  `stdin`, sem arquivos temporários) exclusivamente para linhas suspeitas/corrompidas (baixa
  confiança `< 0.78` ou linhas largas com densidade alfanumérica anômala);
- Divide linhas OCR em palavras individuais usando largura visual ponderada via
  `fitz.get_text_length(..., fontname="helv", fontsize=10)` para posicionamento preciso;
- Converte todas as caixas delimitadoras (polígonos de 4 pontos do OCR) para coordenadas
  normalizadas `[x0_norm, y0_norm, x1_norm, y1_norm]` no intervalo `[0.0, 1.0]`.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
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
LINE_RESCUE_CONFIDENCE_THRESHOLD: float = 0.78
MAX_TESSERACT_RESCUE_LINES: int = 8
_OCR_ROW_CLUSTER_Y0_TOL: float = 0.006  # ~5pt em A4 para ordenar esquerda->direita na mesma linha
_GLUED_PUNCT_WORD_RE = re.compile(r"[^\s,;]+[,;]?|[,;]")
_TESSERACT_BIN: str | None = shutil.which("tesseract")


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


def _iter_word_subspans(raw_text: str) -> list[tuple[int, int, str]]:
    """
    Itera sobre palavras em `raw_text`, separando vírgula/ponto-e-vírgula colados à palavra
    seguinte por artefato de OCR (ex.: `528.419.630-98,necessita` -> `528.419.630-98,` + `necessita`),
    sem jamais quebrar decimais monetários (`1.400,00`).
    """
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\S+", raw_text):
        word = match.group(0)
        base_start = match.start()
        # Verifica se há vírgula/ponto-e-vírgula imediatamente seguido de letra (colado por OCR)
        split_points = [
            m.start()
            for m in re.finditer(r"(?<=[,;])(?=[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç])", word)
        ]
        if not split_points:
            spans.append((base_start, match.end(), word))
            continue

        prev = 0
        for sp in split_points:
            part = word[prev:sp]
            if part:
                spans.append((base_start + prev, base_start + sp, part))
            prev = sp
        tail = word[prev:]
        if tail:
            spans.append((base_start + prev, match.end(), tail))
    return spans


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

    for start_idx, end_idx, word in _iter_word_subspans(raw_text):
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


def _is_suspect_ocr_line(line: OCRTokenSpan) -> bool:
    """
    Identifica linhas potencialmente corrompidas pelo RapidOCR que merecem resgate cirúrgico
    via Tesseract (ex.: linhas justificadas lidas como `'(   11    )'` ou com confiança < 0.78).
    """
    width_norm = max(0.0, line.x1_norm - line.x0_norm)
    height_norm = max(0.0, line.y1_norm - line.y0_norm)
    # Ignora fragmentos muito pequenos ou verticais
    if width_norm < 0.04 or height_norm < 0.005 or height_norm > 0.12:
        return False

    non_space = re.sub(r"\s+", "", line.text or "")
    alnum_count = sum(1 for ch in non_space if ch.isalnum())
    alnum_ratio = alnum_count / max(1, len(non_space))

    if line.confidence < LINE_RESCUE_CONFIDENCE_THRESHOLD:
        return True

    # Linha larga com texto quase vazio ou dominado por pontuação/espaços (ex.: `'(   11    )'`)
    if width_norm >= 0.25 and (alnum_count < max(4, int(width_norm * 16)) or alnum_ratio < 0.45):
        return True

    return False


def _rescue_line_with_tesseract(
    page: fitz.Page,
    line: OCRTokenSpan,
    *,
    rescue_dpi: int = FALLBACK_OCR_DPI,
) -> tuple[OCRTokenSpan, list[OCRTokenSpan]] | None:
    """
    Executa resgate cirúrgico de uma única linha suspeita usando `tesseract` (`-l por --psm 7 tsv`)
    inteiramente em memória via `stdin` (Zero-PII, sem gravar arquivos em disco).
    Retorna `(rescued_line, rescued_word_tokens)` se o resultado for objetivamente superior ao original.
    """
    if not _TESSERACT_BIN:
        return None

    page_rect = page.rect
    pw = max(1.0, float(page_rect.width))
    ph = max(1.0, float(page_rect.height))

    pad_x_pts = 6.0
    pad_y_pts = 3.5
    clip = fitz.Rect(
        max(page_rect.x0, page_rect.x0 + line.x0_norm * pw - pad_x_pts),
        max(page_rect.y0, page_rect.y0 + line.y0_norm * ph - pad_y_pts),
        min(page_rect.x1, page_rect.x0 + line.x1_norm * pw + pad_x_pts),
        min(page_rect.y1, page_rect.y0 + line.y1_norm * ph + pad_y_pts),
    )
    if clip.is_empty or clip.width < 8.0 or clip.height < 4.0:
        return None

    try:
        mat = fitz.Matrix(rescue_dpi / 72.0, rescue_dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        if pix.width < 12 or pix.height < 8:
            return None

        mode = "RGB" if pix.n >= 3 else "L"
        pil_img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        gray = ImageOps.grayscale(pil_img)
        enhanced = ImageOps.autocontrast(gray, cutoff=1)

        buf = io.BytesIO()
        enhanced.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        proc = subprocess.run(
            [_TESSERACT_BIN, "stdin", "stdout", "-l", "por", "--psm", "7", "tsv"],
            input=png_bytes,
            capture_output=True,
            timeout=3.5,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
    except Exception:
        return None

    clip_x0_norm = (clip.x0 - page_rect.x0) / pw
    clip_y0_norm = (clip.y0 - page_rect.y0) / ph
    clip_w_norm = max(1e-6, clip.width / pw)
    clip_h_norm = max(1e-6, clip.height / ph)

    words_parsed: list[tuple[str, float, float, float, float, float]] = []
    tsv_lines = proc.stdout.decode("utf-8", errors="ignore").splitlines()
    for row in tsv_lines[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        if cols[0] != "5":  # level 5 = word
            continue
        word_txt = cols[11].strip()
        if not word_txt:
            continue
        try:
            left_px = float(cols[6])
            top_px = float(cols[7])
            w_px = float(cols[8])
            h_px = float(cols[9])
            conf_raw = float(cols[10])
        except ValueError:
            continue
        if w_px <= 0 or h_px <= 0 or conf_raw < 0:
            continue

        conf_norm = round(max(0.0, min(1.0, conf_raw / 100.0)), 4)
        wx0 = _clamp_norm(clip_x0_norm + (left_px / pix.width) * clip_w_norm)
        wy0 = _clamp_norm(clip_y0_norm + (top_px / pix.height) * clip_h_norm)
        wx1 = _clamp_norm(clip_x0_norm + ((left_px + w_px) / pix.width) * clip_w_norm)
        wy1 = _clamp_norm(clip_y0_norm + ((top_px + h_px) / pix.height) * clip_h_norm)
        if wx1 <= wx0:
            wx1 = _clamp_norm(min(1.0, wx0 + 1e-4))
        if wy1 <= wy0:
            wy1 = _clamp_norm(min(1.0, wy0 + 1e-4))

        words_parsed.append((word_txt, wx0, wy0, wx1, wy1, conf_norm))

    if not words_parsed:
        return None

    rescued_text = " ".join(w[0] for w in words_parsed).strip()
    orig_alnum = sum(1 for ch in (line.text or "") if ch.isalnum())
    tess_alnum = sum(1 for ch in rescued_text if ch.isalnum())
    tess_mean_conf = round(sum(w[5] for w in words_parsed) / len(words_parsed), 4)

    # Aceita apenas se o resgate recuperou conteúdo alfanumérico claramente perdido ou elevou muito a confiança
    recovered_garble = tess_alnum >= max(4, int(orig_alnum * 1.5)) and tess_mean_conf >= 0.45
    stronger_read = (
        tess_mean_conf >= (line.confidence + 0.12)
        and tess_alnum >= max(2, orig_alnum)
        and tess_mean_conf >= 0.72
    )
    if not (recovered_garble or stronger_read):
        return None

    lx0 = min(w[1] for w in words_parsed)
    ly0 = min(w[2] for w in words_parsed)
    lx1 = max(w[3] for w in words_parsed)
    ly1 = max(w[4] for w in words_parsed)

    rescued_line = OCRTokenSpan(
        text=rescued_text,
        x0_norm=lx0,
        y0_norm=ly0,
        x1_norm=lx1,
        y1_norm=ly1,
        confidence=max(tess_mean_conf, line.confidence),
        dpi_used=rescue_dpi,
        source="OCR",
    )

    rescued_tokens: list[OCRTokenSpan] = []
    for w_txt, wx0, wy0, wx1, wy1, w_conf in words_parsed:
        base_tok = OCRTokenSpan(
            text=w_txt,
            x0_norm=wx0,
            y0_norm=wy0,
            x1_norm=wx1,
            y1_norm=wy1,
            confidence=w_conf,
            dpi_used=rescue_dpi,
            source="OCR",
        )
        # Reaplica separação de vírgula/ponto-e-vírgula colados, caso exista
        if re.search(r"(?<=[,;])(?=[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç])", w_txt):
            rescued_tokens.extend(_split_line_into_word_tokens(base_tok))
        else:
            rescued_tokens.append(base_tok)

    return rescued_line, rescued_tokens


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
    contraste apenas quando o score médio de confiança for inferior a `0.70`, além de
    resgate cirúrgico via Tesseract para linhas isoladas suspeitas/corrompidas.
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

    # 3. Resgate cirúrgico por linha via Tesseract apenas para linhas suspeitas/corrompidas
    rescued_tokens_by_idx: dict[int, list[OCRTokenSpan]] = {}
    if _TESSERACT_BIN and used_lines:
        suspect_indices = [
            idx for idx, ln in enumerate(used_lines) if _is_suspect_ocr_line(ln)
        ]
        if suspect_indices:
            suspect_indices.sort(key=lambda i: used_lines[i].confidence)
            for idx in suspect_indices[:MAX_TESSERACT_RESCUE_LINES]:
                rescued = _rescue_line_with_tesseract(page, used_lines[idx], rescue_dpi=fallback_dpi)
                if rescued is not None:
                    rescued_line, rescued_toks = rescued
                    used_lines[idx] = rescued_line
                    rescued_tokens_by_idx[idx] = rescued_toks

    tokens: list[OCRTokenSpan] = []
    for idx, line in enumerate(used_lines):
        if idx in rescued_tokens_by_idx:
            tokens.extend(rescued_tokens_by_idx[idx])
        else:
            tokens.extend(_split_line_into_word_tokens(line))

    if used_lines and rescued_tokens_by_idx:
        used_conf = round(sum(ln.confidence for ln in used_lines) / len(used_lines), 4)

    return OCRPageResult(
        lines=used_lines,
        tokens=tokens,
        mean_confidence=used_conf,
        dpi_used=used_dpi,
        fallback_triggered=fallback_triggered,
    )
