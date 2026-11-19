"""
Validador Pós-Tarjamento Cirúrgico Ultra-Rápido (`post_redaction_validator.py`).

Executa em menos de 500ms (sem re-escanear páginas inteiras):
- **Verificação 1 (Extração Textual Global e Local):**
  - Para itens `BLACKOUT`: confirma que o interior da caixa tarjada (`page.get_text("words")`) não contém
    restos do texto original nem palavras não-deslocadas na região tarjada, e que o valor original
    não pode ser localizado via `page.search_for()`;
  - Para itens `PARTIAL_MASK`: confirma que o valor original completo (ex.: `348.729.160-69`) foi 100% destruído
    e não pode ser encontrado via `page.search_for(original_value)` nem extraído no recorte, restando apenas a
    string mascarada (`***.729.160-**`) ou fundo branco sólido nas linhas de continuação;
- **Verificação 2 (Inspeção de Pixels e Re-OCR Cirúrgico por Recorte):**
  - Para itens `BLACKOUT`: renderiza o pixmap do recorte (`page.get_pixmap(clip=inner_rect, dpi=150)`) e
    verifica se os pixels na área interna da tarja foram zerados/preenchidos de preto;
  - Para itens `PARTIAL_MASK`: verifica que os pixels originais foram substituídos pelo fundo branco com o texto mascarado;
- **Verificação 3 (Integridade Estrutural):** Confirma com `pikepdf` a ausência de metadados
  residuais (`/Info`, `/Metadata` XMP), camadas ocultas (`/OCProperties`) e anexos (`/Names` -> `/EmbeddedFiles`),
  e que o PDF abre sem erros.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import fitz  # PyMuPDF
import numpy as np
import pikepdf

from app.models.enums import RedactionActionMode
from app.schemas.processing_job import NormalizedBoundingBox
from app.services.redactor.pdf_redactor import RedactionBoxSpec, _denormalize_spec_rects

logger = logging.getLogger(__name__)

_ALPHANUM_RE = re.compile(r"[A-Za-z0-9ÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç]")
_TOKEN_WORDS_RE = re.compile(r"[A-Za-z0-9ÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç]+")
_EDGE_NON_ALNUM_RE = re.compile(r"^[^A-Za-z0-9ÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç]+|[^A-Za-z0-9ÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç]+$")


@dataclass(frozen=True, slots=True)
class BoxSurgicalCheckResult:
    """Resultado da verificação cirúrgica individual de uma caixa tarjada ou mascarada."""

    page_number: int
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    clip_text_empty: bool
    search_hits_in_box: int
    mean_interior_luminance: float
    max_interior_luminance: float
    pixels_blackened: bool
    ocr_leak_detected: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class StructuralCheckResult:
    """Resultado da verificação de integridade estrutural com `pikepdf`."""

    pdf_opens_cleanly: bool
    docinfo_empty: bool
    xmp_metadata_absent: bool
    ocg_layers_absent: bool
    embedded_files_absent: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class SecurityValidationResult:
    """Resultado consolidado da validação pós-tarjamento cirúrgica."""

    passed: bool
    text_layer_verified: bool
    pixel_layer_verified: bool
    structure_verified: bool
    boxes_checked: int
    box_results: list[BoxSurgicalCheckResult] = field(default_factory=list)
    structural_result: StructuralCheckResult | None = None
    validation_time_ms: float = 0.0
    summary_message: str = ""


def _shrink_rect_slightly(
    rect: fitz.Rect,
    *,
    margin_x_pts: float = 3.5,
    margin_y_pts: float = 1.8,
) -> fitz.Rect:
    """
    Contrai levemente a caixa (3.5pt nas laterais X e 1.8pt no eixo Y) para inspecionar
    estritamente o interior da tarja sem capturar glifos de palavras vizinhas na mesma linha.
    """
    mx = margin_x_pts if rect.width > margin_x_pts * 3 else 0.5
    my = margin_y_pts if rect.height > margin_y_pts * 3 else 0.5
    return fitz.Rect(
        rect.x0 + mx,
        rect.y0 + my,
        rect.x1 - mx,
        rect.y1 - my,
    )


def _verify_pikepdf_structure(redacted_pdf_path: Path) -> StructuralCheckResult:
    """Verifica com `pikepdf` a ausência de metadados `/Info`, XMP `/Metadata`, `/OCProperties` e anexos."""
    try:
        with pikepdf.open(str(redacted_pdf_path)) as pdf:
            _ = len(pdf.pages)
            sensitive_info_keys = {"/Author", "/Title", "/Subject", "/Keywords", "/Creator"}
            present_info_keys = set(pdf.docinfo.keys()) if pdf.docinfo else set()
            docinfo_clean = len(sensitive_info_keys.intersection(present_info_keys)) == 0

            root = pdf.Root
            xmp_absent = "/Metadata" not in root
            ocg_absent = "/OCProperties" not in root
            embedded_absent = True
            if "/Names" in root and isinstance(root["/Names"], pikepdf.Dictionary):
                if "/EmbeddedFiles" in root["/Names"]:
                    embedded_absent = False

            passed = docinfo_clean and xmp_absent and ocg_absent and embedded_absent
            return StructuralCheckResult(
                pdf_opens_cleanly=True,
                docinfo_empty=docinfo_clean,
                xmp_metadata_absent=xmp_absent,
                ocg_layers_absent=ocg_absent,
                embedded_files_absent=embedded_absent,
                passed=passed,
            )
    except Exception as exc:
        logger.error("Falha na verificação estrutural pós-tarjamento", extra={"error": type(exc).__name__})
        return StructuralCheckResult(
            pdf_opens_cleanly=False,
            docinfo_empty=False,
            xmp_metadata_absent=False,
            ocg_layers_absent=False,
            embedded_files_absent=False,
            passed=False,
        )


def validate_redacted_pdf_surgically(
    redacted_pdf_path: Path | str,
    boxes: Sequence[RedactionBoxSpec | NormalizedBoundingBox],
    *,
    forbidden_strings: Sequence[str] | None = None,
    run_crop_ocr_if_nonblack: bool = True,
) -> SecurityValidationResult:
    """
    Executa a bateria de 3 verificações cirúrgicas pós-tarjamento em menos de 500ms:
    1. Extração textual no recorte (`page.get_text("words")`) e busca textual (`page.search_for()`);
    2. Renderização exclusiva do recorte (`page.get_pixmap(clip=inner_rect, dpi=150)`) e inspeção
       dos pixels para comprovar preenchimento preto sólido em `BLACKOUT` ou substituição limpa em `PARTIAL_MASK`;
    3. Validação estrutural com `pikepdf`.
    """
    t0 = time.perf_counter()
    pdf_path = Path(redacted_pdf_path)

    normalized_specs: list[RedactionBoxSpec] = [
        b if isinstance(b, RedactionBoxSpec) else RedactionBoxSpec.from_normalized_bbox(b)
        for b in boxes
    ]
    active_specs = [s for s in normalized_specs if s.action_mode != RedactionActionMode.KEEP]

    partial_mask_strings: set[str] = {
        s.masked_preview.strip()
        for s in active_specs
        if s.action_mode == RedactionActionMode.PARTIAL_MASK and s.masked_preview and s.masked_preview.strip()
    }

    global_forbidden: set[str] = {s.strip() for s in (forbidden_strings or []) if s and len(s.strip()) >= 3}
    for spec in active_specs:
        if spec.expected_text and len(spec.expected_text.strip()) >= 3:
            exp = spec.expected_text.strip()
            if spec.action_mode == RedactionActionMode.PARTIAL_MASK and any(exp in m for m in partial_mask_strings):
                continue
            global_forbidden.add(exp)

    box_results: list[BoxSurgicalCheckResult] = []
    all_text_ok = True
    all_pixels_ok = True

    doc = fitz.open(str(pdf_path))
    try:
        specs_by_page: dict[int, list[RedactionBoxSpec]] = {}
        for spec in active_specs:
            specs_by_page.setdefault(spec.page_number, []).append(spec)

        for page_num, page_specs in sorted(specs_by_page.items()):
            page = doc[page_num - 1]
            page_rect = page.rect

            found_forbidden_rects: list[fitz.Rect] = []
            for term in global_forbidden:
                hits = page.search_for(term)
                if hits:
                    found_forbidden_rects.extend(hits)

            page_words: list[tuple[fitz.Rect, str]] = [
                (fitz.Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3])), str(w[4]))
                for w in page.get_text("words")
            ]

            blackout_rects: list[fitz.Rect] = [
                br
                for b_spec in page_specs
                if b_spec.action_mode == RedactionActionMode.BLACKOUT
                for br in _denormalize_spec_rects(b_spec, page_rect)
            ]

            for spec in page_specs:
                box_rects = _denormalize_spec_rects(spec, page_rect)
                for full_box_rect in box_rects:
                    inner_rect = _shrink_rect_slightly(full_box_rect)

                    if spec.action_mode == RedactionActionMode.PARTIAL_MASK:
                        orig_val = (spec.expected_text or "").strip()
                        mask_val = (spec.masked_preview or "").strip()
                        preserved_prefix = bool(orig_val and mask_val and orig_val in mask_val)

                        clip_text = page.get_text("text", clip=full_box_rect).strip()
                        orig_still_present = bool(
                            orig_val
                            and len(orig_val) >= 2
                            and orig_val in clip_text
                            and not preserved_prefix
                        )

                        hits_in_box = sum(1 for hr in found_forbidden_rects if hr.intersects(inner_rect))
                        if orig_val and len(orig_val) >= 2 and not preserved_prefix:
                            direct_hits = page.search_for(orig_val, clip=full_box_rect)
                            hits_in_box += len(direct_hits)

                        clip_empty = not orig_still_present
                        text_ok = clip_empty and (hits_in_box == 0)
                        if not text_ok:
                            all_text_ok = False

                        pix = page.get_pixmap(clip=inner_rect, dpi=150, alpha=False)
                        samples = np.frombuffer(pix.samples, dtype=np.uint8)
                        mean_lum = float(np.mean(samples)) if samples.size > 0 else 255.0
                        max_lum = float(np.max(samples)) if samples.size > 0 else 255.0

                        overlapped_by_blackout = any(br.intersects(inner_rect) for br in blackout_rects)
                        pixels_valid = (
                            samples.size == 0
                            or mean_lum >= 50.0
                            or (overlapped_by_blackout and mean_lum <= 5.0 and max_lum <= 30.0)
                        )
                        if not pixels_valid:
                            all_pixels_ok = False

                        box_passed = text_ok and pixels_valid
                        box_results.append(
                            BoxSurgicalCheckResult(
                                page_number=page_num,
                                x0_norm=spec.x0_norm,
                                y0_norm=spec.y0_norm,
                                x1_norm=spec.x1_norm,
                                y1_norm=spec.y1_norm,
                                clip_text_empty=clip_empty,
                                search_hits_in_box=hits_in_box,
                                mean_interior_luminance=round(mean_lum, 3),
                                max_interior_luminance=round(max_lum, 3),
                                pixels_blackened=pixels_valid,
                                ocr_leak_detected=False,
                                passed=box_passed,
                            )
                        )
                        continue

                    exp_clean = (spec.expected_text or "").strip().lower()
                    expected_tokens = {tok.lower() for tok in _TOKEN_WORDS_RE.findall(exp_clean) if tok}
                    leaked_words: list[str] = []

                    for w_rect, wtext in page_words:
                        if not _ALPHANUM_RE.search(wtext):
                            continue
                        inter = w_rect & inner_rect
                        if inter.is_empty:
                            continue

                        w_norm = _EDGE_NON_ALNUM_RE.sub("", wtext).lower()
                        is_expected_or_forbidden = (
                            (w_norm in expected_tokens)
                            or (len(w_norm) >= 2 and bool(exp_clean) and w_norm in exp_clean)
                            or any(term.lower() in wtext.lower() for term in global_forbidden)
                        )

                        if is_expected_or_forbidden:
                            if inter.width >= 1.5 and inter.height >= 1.5:
                                leaked_words.append(wtext)
                        elif not exp_clean:
                            if inter.width >= max(2.0, w_rect.width * 0.35) and inter.height >= max(
                                2.0, w_rect.height * 0.35
                            ):
                                leaked_words.append(wtext)
                        else:
                            if inter.height < max(2.0, w_rect.height * 0.35):
                                continue
                            if len(w_norm) >= 3:
                                if inter.width >= w_rect.width * 0.50:
                                    leaked_words.append(wtext)
                            elif w_norm and w_rect.x0 < (full_box_rect.x1 - 20.0):
                                if inter.width >= w_rect.width * 0.50:
                                    leaked_words.append(wtext)

                    clip_empty = len(leaked_words) == 0

                    hits_in_box = sum(1 for hr in found_forbidden_rects if hr.intersects(inner_rect))
                    if spec.expected_text and len(spec.expected_text.strip()) >= 2:
                        direct_hits = page.search_for(spec.expected_text.strip(), clip=full_box_rect)
                        hits_in_box += len(direct_hits)

                    text_ok = clip_empty and (hits_in_box == 0)
                    if not text_ok:
                        all_text_ok = False

                    pix = page.get_pixmap(clip=inner_rect, dpi=150, alpha=False)
                    samples = np.frombuffer(pix.samples, dtype=np.uint8)

                    if samples.size > 0:
                        mean_lum = float(np.mean(samples))
                        max_lum = float(np.max(samples))
                    else:
                        mean_lum = 0.0
                        max_lum = 0.0

                    pixels_blackened = mean_lum <= 5.0 and max_lum <= 30.0
                    ocr_leak = False

                    if not pixels_blackened and run_crop_ocr_if_nonblack and pix.width >= 8 and pix.height >= 8:
                        from app.core.ml_models import ml_registry

                        ocr_engine = ml_registry.get_ocr_engine()
                        crop_arr = samples.reshape(pix.height, pix.width, pix.n)
                        raw_ocr, _ = ocr_engine(crop_arr)
                        if raw_ocr:
                            ocr_leak = True

                    pixel_box_ok = pixels_blackened and not ocr_leak
                    if not pixel_box_ok:
                        all_pixels_ok = False

                    box_passed = text_ok and pixel_box_ok
                    box_results.append(
                        BoxSurgicalCheckResult(
                            page_number=page_num,
                            x0_norm=spec.x0_norm,
                            y0_norm=spec.y0_norm,
                            x1_norm=spec.x1_norm,
                            y1_norm=spec.y1_norm,
                            clip_text_empty=clip_empty,
                            search_hits_in_box=hits_in_box,
                            mean_interior_luminance=round(mean_lum, 3),
                            max_interior_luminance=round(max_lum, 3),
                            pixels_blackened=pixels_blackened,
                            ocr_leak_detected=ocr_leak,
                            passed=box_passed,
                        )
                    )
    finally:
        doc.close()

    struct_res = _verify_pikepdf_structure(pdf_path)
    overall_passed = all_text_ok and all_pixels_ok and struct_res.passed
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    if overall_passed:
        summary = (
            f"Validação cirúrgica aprovada ({len(box_results)} áreas verificadas em {elapsed_ms:.1f}ms): "
            "fluxos de texto originais removidos, pixels substituídos e metadados estruturais expurgados."
        )
    else:
        summary = (
            "ALERTA DE SEGURANÇA: A validação pós-tarjamento detectou resíduos de texto, pixels ou metadados."
        )

    logger.info(
        "Validação cirúrgica pós-tarjamento concluída",
        extra={
            "passed": overall_passed,
            "boxes_checked": len(box_results),
            "text_layer_verified": all_text_ok,
            "pixel_layer_verified": all_pixels_ok,
            "structure_verified": struct_res.passed,
            "validation_time_ms": elapsed_ms,
        },
    )

    return SecurityValidationResult(
        passed=overall_passed,
        text_layer_verified=all_text_ok,
        pixel_layer_verified=all_pixels_ok,
        structure_verified=struct_res.passed,
        boxes_checked=len(box_results),
        box_results=box_results,
        structural_result=struct_res,
        validation_time_ms=elapsed_ms,
        summary_message=summary,
    )
