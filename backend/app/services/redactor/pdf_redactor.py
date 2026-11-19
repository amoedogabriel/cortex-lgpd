"""
Executor de Redação Definitiva Irreversível Híbrida (`pdf_redactor.py`).

DIRETRIZ RÍGIDA (SPEC.md Seção 1 e Seção 4.2):
É estritamente proibido ocultar dados desenhando apenas retângulos sobre o PDF.
Este módulo suporta dois modos físicos irreversíveis via `PyMuPDF`:
1. `BLACKOUT` (Tarja Preta Total):
   Registra `page.add_redact_annot(rect, fill=(0, 0, 0))` e destrói glifos, vetores e pixels.
2. `PARTIAL_MASK` (Mascaramento Parcial, ex.: `***.729.160-**`):
   1. Registra `page.add_redact_annot(rect, fill=(1, 1, 1))` (sem passar `text=` na anotação, evitando
      encolhimento de fonte por recuo interno);
   2. Executa `page.apply_redactions(...)` destruindo fisicamente glifos, vetores e pixels originais;
   3. Insere a string mascarada (`masked_preview`) com `page.insert_text(...)` usando fonte ampliada
      proporcional à altura real da linha (`max(10.5, min(13.0, round(rect.height * 0.95, 1)))`,
      mantendo `>= 10.0pt`) e linha de base alinhada (`rect.y1 - (rect.height * 0.14)`).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import fitz  # PyMuPDF

from app.core.exceptions import RedactionPipelineError
from app.models.enums import DataCategory, RedactionActionMode
from app.schemas.processing_job import NormalizedBoundingBox

logger = logging.getLogger(__name__)

_NON_DIGIT_RE = re.compile(r"\D+")
_NAME_PARTICLES: frozenset[str] = frozenset({"da", "de", "do", "dos", "das", "e"})


def generate_masked_preview(entity_type: str, raw_value: str) -> str:
    """
    Gera a string `masked_preview` com o valor mascarado conforme padrão institucional LGPD/CGU:
    - CPF `348.729.160-69` -> `***.729.160-**`
    - RG `1849203-5` -> `***.9203-*`
    - E-mail `carlos.mendonca84@email.com` -> `ca***@email.com`
    - Telefone `(92) 99184-7721` ou `+5592984310092` -> `(92) *****-7721` / `(92) *****-0092`
    - Conta `48921-3` -> `***21-*`
    - Agência `3563-7` -> `**63-*`
    - SIAPE `1357924` -> `***792*`
    - Matrícula UFAM / Inscrição `22150198` -> `***0198*`
    - Título de Eleitor `0482 9103 2216` -> `**** 9103 22**`
    - CNH `04938271654` -> `***3827****`
    - PIS/PASEP `120.56874.18-2` -> `***.56874.**-*`
    - PIX UUID `9f8a7b6c-1234-4abc-89de-0192837465ab` -> `****-****-65ab`
    - Nome Próprio `Alexandre Magno de Figueiredo` -> `Alexandre M. F. ***`
    - Data de Nascimento `12/05/1988` -> `**/**/****`
    - Endereço `Rua das Flores, nº 120` -> `Logradouro ***`
    """
    val = re.sub(r"\s+", " ", (raw_value or "")).strip()
    if not val:
        return "***"

    etype = (entity_type or "").upper()
    digits = _NON_DIGIT_RE.sub("", val)

    if etype == "CPF" or (not etype and len(digits) == 11 and re.match(r"^\d{3}\.?\d{3}\.?\d{3}-?\s*\d{2}$", val)):
        if len(digits) == 11:
            return f"***.{digits[3:6]}.{digits[6:9]}-**"
        return "***.XXX.XXX-**"

    if etype == "RG":
        if "-" in val:
            main_part, _ = val.rsplit("-", 1)
            main_digits = _NON_DIGIT_RE.sub("", main_part)
            mid = main_digits[-4:] if len(main_digits) >= 4 else main_digits
            return f"***.{mid}-*"
        if len(digits) >= 5:
            return f"***.{digits[-5:-1]}-*"
        return "***.XXXX-*"

    if etype == "CNH":
        if len(digits) >= 9:
            return f"***{digits[3:7]}****"
        return "***********"

    if etype == "PIS_PASEP":
        if len(digits) == 11:
            return f"***.{digits[3:8]}.**-*"
        return "***.*****.**-*"

    if etype == "TITULO_ELEITOR":
        if len(digits) == 12:
            return f"**** {digits[4:8]} {digits[8:10]}**"
        return "**** **** ****"

    if etype == "EMAIL" or "@" in val:
        parts = val.replace(" ", "").split("@", 1)
        if len(parts) == 2:
            user, domain = parts[0], parts[1]
            prefix = user[:2] if len(user) >= 2 else user[:1]
            return f"{prefix}***@{domain}"
        return "u***@dominio.br"

    if etype == "PHONE":
        local_digits = digits[2:] if digits.startswith("55") and len(digits) in (12, 13) else digits
        m_ddd = re.search(r"\(([1-9][0-9])\)", val)
        ddd = m_ddd.group(1) if m_ddd else (local_digits[:2] if len(local_digits) >= 10 else "")
        last4 = digits[-4:] if len(digits) >= 4 else "0000"
        if ddd:
            stars = "*****" if len(local_digits) >= 11 else "****"
            return f"({ddd}) {stars}-{last4}"
        return f"*****-{last4}"

    if etype == "BANK_ACCOUNT":
        if "-" in val:
            base, _ = val.rsplit("-", 1)
            base_digits = _NON_DIGIT_RE.sub("", base)
            visible = base_digits[-2:] if len(base_digits) >= 2 else base_digits
            return f"***{visible}-*"
        if len(digits) >= 3:
            return f"***{digits[-3:-1]}-*"
        return "***XX-*"

    if etype == "BANK_AGENCY":
        if "-" in val:
            base, _ = val.rsplit("-", 1)
            base_digits = _NON_DIGIT_RE.sub("", base)
            visible = base_digits[-2:] if len(base_digits) >= 2 else base_digits
            return f"**{visible}-*"
        if len(digits) >= 2:
            return f"**{digits[-2:]}"
        return "**XX-*"

    if etype == "BANK_OPERATION":
        return "Op. ***"

    if etype == "SIAPE":
        if len(digits) >= 5:
            return f"***{digits[-4:-1]}*"
        return "***XXXX*"

    if etype == "MATRICULA_UFAM":
        if len(digits) >= 7:
            return f"***{digits[-4:]}*"
        if len(digits) >= 5:
            return f"***{digits[-3:-1]}*"
        return "***XXXX*"

    if etype == "PIX_KEY":
        compact = re.sub(r"\s+", "", val)
        if len(compact) >= 8:
            return f"****-****-{compact[-4:]}"
        return "****-PIX-****"

    if etype == "REMUNERATION":
        return "R$ ***.***,**"

    if etype in ("PERSON_NAME", "PER", "FILIACAO", "DEPENDENT_DATA"):
        words = [w for w in val.split(" ") if w]
        if len(words) >= 2:
            initials = [
                f"{w[0].upper()}."
                for w in words[1:]
                if w.lower() not in _NAME_PARTICLES and re.match(r"^[A-Za-zÀ-ÿ]", w)
            ]
            if initials:
                return f"{words[0]} {' '.join(initials)} ***"
            return f"{words[0]} ***"
        return f"{val[:2]}***"

    if etype == "BIRTH_DATE":
        return "**/**/****"

    if etype == "ADDRESS":
        return "Logradouro ***"

    if etype == "CID_MEDICAL":
        return "[CID PROTEGIDO]"

    if etype == "HEALTH_DATA":
        return "[SAÚDE PROTEGIDO]"

    if etype == "ETHNIC_RACIAL_DECLARATION":
        return "[DADO ÉTNICO PROTEGIDO]"

    if etype == "UNION_AFFILIATION":
        return "[FILIAÇÃO SINDICAL PROTEGIDA]"

    if etype == "BIOMETRY_ID":
        return f"{val[:4]}***" if len(val) > 5 else "[BIOMETRIA PROTEGIDA]"

    if etype == "GENETIC_DATA":
        return "[DADO GENÉTICO PROTEGIDO]"

    if etype == "RELIGIOUS_BELIEF":
        return "[CRENÇA RELIGIOSA PROTEGIDA]"

    if etype == "FACIAL_PHOTO":
        return "[FOTO PROTEGIDA]"

    if etype == "CEP" and len(digits) == 8:
        return f"{digits[:2]}.***-***"

    if etype == "PROCESSO_SEI" and len(digits) >= 10:
        return "23105.******/****-**"

    if len(val) > 4:
        return f"***{val[-3:]}"
    return "***"


@dataclass(frozen=True, slots=True)
class RedactionBoxSpec:
    """Especificação de uma caixa de tarjamento ou mascaramento normalizada [0.0, 1.0]."""

    page_number: int  # 1-indexed
    x0_norm: float
    y0_norm: float
    x1_norm: float
    y1_norm: float
    category: DataCategory = DataCategory.PERSONAL_COMMON
    expected_text: str | None = None  # Usado apenas em memória para verificação cirúrgica pós-redação; nunca logado
    action_mode: RedactionActionMode = RedactionActionMode.BLACKOUT
    masked_preview: str | None = None
    entity_type: str | None = None
    rects: tuple[tuple[float, float, float, float], ...] | None = None
    is_ocr: bool = False
    safe_x0_norm: float | None = None
    safe_x1_norm: float | None = None

    @classmethod
    def from_normalized_bbox(
        cls,
        box: NormalizedBoundingBox,
        *,
        expected_text: str | None = None,
    ) -> "RedactionBoxSpec":
        return cls(
            page_number=box.page_number,
            x0_norm=box.x0_norm,
            y0_norm=box.y0_norm,
            x1_norm=box.x1_norm,
            y1_norm=box.y1_norm,
            category=box.category,
            expected_text=expected_text,
            action_mode=getattr(box, "action_mode", RedactionActionMode.BLACKOUT),
            masked_preview=getattr(box, "masked_preview", None),
        )


@dataclass(frozen=True, slots=True)
class PhysicalRedactionResult:
    """Resultado da etapa de redação física com PyMuPDF (pré-sanitização pikepdf)."""

    original_pdf_path: Path
    intermediate_pdf_path: Path
    original_sha256: str
    intermediate_sha256: str
    total_pages: int
    affected_pages: list[int]
    total_boxes_applied: int
    category_counts: dict[str, int] = field(default_factory=dict)
    widgets_removed: int = 0
    annotations_removed: int = 0
    execution_time_ms: float = 0.0


def _denormalize_coords_to_fitz_rect(
    x0_norm: float,
    y0_norm: float,
    x1_norm: float,
    y1_norm: float,
    page_number: int,
    page_rect: fitz.Rect,
) -> fitz.Rect:
    pw = float(page_rect.width)
    ph = float(page_rect.height)

    x0 = float(page_rect.x0) + max(0.0, min(1.0, x0_norm)) * pw
    y0 = float(page_rect.y0) + max(0.0, min(1.0, y0_norm)) * ph
    x1 = float(page_rect.x0) + max(0.0, min(1.0, x1_norm)) * pw
    y1 = float(page_rect.y0) + max(0.0, min(1.0, y1_norm)) * ph

    rect = fitz.Rect(x0, y0, x1, y1) & page_rect
    if rect.is_empty or rect.width <= 0.1 or rect.height <= 0.1:
        raise RedactionPipelineError(
            message=f"Geometria de caixa de tarjamento inválida na página {page_number}.",
            stage="DENORMALIZE_REDACTION_RECT",
        )
    return rect


def _denormalize_box_to_fitz_rect(
    box: RedactionBoxSpec,
    page_rect: fitz.Rect,
) -> fitz.Rect:
    """
    Converte coordenadas normalizadas `[0.0, 1.0]` para `fitz.Rect(x0, y0, x1, y1)` em pontos PDF.
    Mantém `padding_y = 0` exato nas coordenadas das palavras da linha (sem expandir verticalmente),
    garantindo destruição completa dos glifos da palavra tarjada sem invadir cabeçalhos ou linhas vizinhas.
    """
    return _denormalize_coords_to_fitz_rect(
        box.x0_norm,
        box.y0_norm,
        box.x1_norm,
        box.y1_norm,
        box.page_number,
        page_rect,
    )


def _denormalize_spec_rects(
    box: RedactionBoxSpec,
    page_rect: fitz.Rect,
) -> list[fitz.Rect]:
    if box.rects:
        return [
            _denormalize_coords_to_fitz_rect(r[0], r[1], r[2], r[3], box.page_number, page_rect)
            for r in box.rects
        ]
    return [_denormalize_box_to_fitz_rect(box, page_rect)]


def _resolve_mask_text(spec: RedactionBoxSpec) -> str:
    """Resolve o texto mascarado para `PARTIAL_MASK`. String vazia `""` explícita sinaliza continuação sem texto."""
    if spec.masked_preview == "":
        return ""
    if spec.masked_preview and spec.masked_preview.strip():
        return spec.masked_preview.strip()
    if spec.expected_text and spec.expected_text.strip():
        return generate_masked_preview(spec.entity_type or "", spec.expected_text)
    return "***"


def _remove_intersecting_annotations_and_widgets(
    page: fitz.Page,
    target_rects: list[fitz.Rect],
) -> tuple[int, int]:
    """
    Remove anotações pré-existentes (comentários, highlights, links) e campos de formulário
    (`widgets`) que intersectem as áreas tarjadas antes de registrar as anotações de redação.
    """
    removed_annots = 0
    removed_widgets = 0

    existing_annots = list(page.annots() or [])
    for annot in existing_annots:
        annot_rect = annot.rect
        if any(annot_rect.intersects(r) for r in target_rects):
            page.delete_annot(annot)
            removed_annots += 1

    existing_widgets = list(page.widgets() or [])
    for widget in existing_widgets:
        w_rect = widget.rect
        if any(w_rect.intersects(r) for r in target_rects):
            try:
                page.delete_widget(widget)
                removed_widgets += 1
            except Exception:
                widget.field_value = ""
                widget.update()
                removed_widgets += 1

    return removed_annots, removed_widgets


def _insert_scaled_partial_mask_text(
    page: fitz.Page,
    rect: fitz.Rect,
    masked_text: str,
) -> None:
    """
    Insere `masked_text` após `apply_redactions()` com fonte ampliada proporcional à altura da linha
    e linha de base alinhada ao texto original do documento.
    """
    fontname = "hebo"
    dynamic_fontsize = max(10.5, min(13.0, round(float(rect.height) * 0.95, 1)))
    text_len = fitz.get_text_length(masked_text, fontname=fontname, fontsize=dynamic_fontsize)
    if text_len > float(rect.width) and text_len > 0.0:
        adjusted = round(dynamic_fontsize * (float(rect.width) / float(text_len)), 2)
        dynamic_fontsize = max(10.0, adjusted)

    baseline_y = float(rect.y1) - (float(rect.height) * 0.14)
    page.insert_text(
        fitz.Point(float(rect.x0), baseline_y),
        masked_text,
        fontname=fontname,
        fontsize=dynamic_fontsize,
        color=(0, 0, 0),
    )


def apply_true_redactions_to_pdf(
    original_pdf_path: Path | str,
    boxes: Sequence[RedactionBoxSpec | NormalizedBoundingBox],
    output_pdf_path: Path | str | None = None,
) -> PhysicalRedactionResult:
    """
    Executa o tarjamento ou mascaramento físico irreversível sobre uma cópia de trabalho do PDF original:
    - O arquivo em `original_pdf_path` permanece 100% intacto;
    - Agrupa as caixas por página e converte para `fitz.Rect(x0, y0, x1, y1)`;
    - Remove anotações e `widgets` nas regiões afetadas;
    - Quando `action_mode == BLACKOUT`: `page.add_redact_annot(rect, fill=(0, 0, 0))`;
    - Quando `action_mode == PARTIAL_MASK`: registra `page.add_redact_annot(rect, fill=(1, 1, 1))`,
      destrói fisicamente glifos/pixels com `page.apply_redactions(...)` e, em seguida, grava a
      string mascarada via `page.insert_text(...)` com fonte legível e alinhada no 1º retângulo;
    - Salva em `output_pdf_path` com `garbage=4, deflate=True, clean=True`.
    """
    t0 = time.perf_counter()
    src_path = Path(original_pdf_path)
    if not src_path.exists():
        raise RedactionPipelineError(
            message="Arquivo PDF original não encontrado para execução do tarjamento.",
            stage="OPEN_ORIGINAL_PDF",
        )

    dst_path = (
        Path(output_pdf_path)
        if output_pdf_path is not None
        else src_path.with_name(f"{src_path.stem}_redacted_raw.pdf")
    )
    if dst_path.resolve() == src_path.resolve():
        raise RedactionPipelineError(
            message="O caminho de saída do PDF tarjado não pode sobrescrever o PDF original.",
            stage="VALIDATE_OUTPUT_PATH",
        )

    original_bytes = src_path.read_bytes()
    original_sha256 = hashlib.sha256(original_bytes).hexdigest()

    normalized_specs: list[RedactionBoxSpec] = [
        b if isinstance(b, RedactionBoxSpec) else RedactionBoxSpec.from_normalized_bbox(b)
        for b in boxes
    ]

    active_specs = [
        s for s in normalized_specs if s.action_mode != RedactionActionMode.KEEP
    ]

    category_counts: dict[str, int] = {
        DataCategory.PERSONAL_COMMON.value: 0,
        DataCategory.PERSONAL_SENSITIVE.value: 0,
        DataCategory.FINANCIAL_BANKING.value: 0,
        DataCategory.MINOR_OR_DEPENDENT.value: 0,
    }

    boxes_by_page: dict[int, list[RedactionBoxSpec]] = {}
    for spec in active_specs:
        boxes_by_page.setdefault(spec.page_number, []).append(spec)
        cat_key = spec.category.value if isinstance(spec.category, DataCategory) else str(spec.category)
        category_counts[cat_key] = category_counts.get(cat_key, 0) + 1

    doc = fitz.open(stream=original_bytes, filetype="pdf")
    total_pages = len(doc)
    affected_pages: list[int] = []
    total_annots_removed = 0
    total_widgets_removed = 0
    total_boxes_applied = 0

    try:
        for page_num in sorted(boxes_by_page.keys()):
            if page_num < 1 or page_num > total_pages:
                raise RedactionPipelineError(
                    message=f"Página {page_num} fora do intervalo válido do documento (1..{total_pages}).",
                    stage="VALIDATE_PAGE_BOUNDS",
                )

            page = doc[page_num - 1]
            page_rect = page.rect
            page_specs = boxes_by_page[page_num]

            spec_and_rects: list[tuple[RedactionBoxSpec, list[fitz.Rect]]] = [
                (spec, _denormalize_spec_rects(spec, page_rect)) for spec in page_specs
            ]
            target_rects: list[fitz.Rect] = [r for _, rects in spec_and_rects for r in rects]

            annots_del, widgets_del = _remove_intersecting_annotations_and_widgets(page, target_rects)
            total_annots_removed += annots_del
            total_widgets_removed += widgets_del

            pending_mask_inserts: list[tuple[fitz.Rect, str]] = []

            for spec, rects in spec_and_rects:
                if spec.action_mode == RedactionActionMode.PARTIAL_MASK:
                    mask_str = _resolve_mask_text(spec)
                    for idx_r, rect in enumerate(rects):
                        # padding_x = 0 exato em PARTIAL_MASK para evitar invasão da palavra seguinte
                        page.add_redact_annot(rect, fill=(1, 1, 1))
                        if idx_r == 0 and mask_str:
                            pending_mask_inserts.append((rect, mask_str))
                        total_boxes_applied += 1
                else:
                    for rect in rects:
                        # Margem horizontal de segurança de +2pt (~0.003 norm) à direita em BLACKOUT
                        # Em páginas OCR, aplica margem bilateral segura de +4pt limitada aos tokens vizinhos
                        if spec.entity_type not in ("FACIAL_PHOTO", "VISUAL_SIGNATURE"):
                            if spec.is_ocr:
                                min_x0 = (
                                    float(page_rect.x0) + max(0.0, min(1.0, float(spec.safe_x0_norm))) * float(page_rect.width)
                                    if spec.safe_x0_norm is not None
                                    else float(page_rect.x0)
                                )
                                max_x1 = (
                                    float(page_rect.x0) + max(0.0, min(1.0, float(spec.safe_x1_norm))) * float(page_rect.width)
                                    if spec.safe_x1_norm is not None
                                    else float(page_rect.x1)
                                )
                                safe_left = min(float(rect.x0), max(float(page_rect.x0), min_x0, float(rect.x0) - 4.0))
                                safe_right = max(float(rect.x1), min(float(page_rect.x1), max_x1, float(rect.x1) + 4.0))
                                apply_rect = fitz.Rect(
                                    safe_left,
                                    rect.y0,
                                    safe_right,
                                    rect.y1,
                                )
                            else:
                                apply_rect = fitz.Rect(
                                    rect.x0,
                                    rect.y0,
                                    min(float(page_rect.x1), rect.x1 + 2.0),
                                    rect.y1,
                                )
                        else:
                            apply_rect = rect
                        page.add_redact_annot(apply_rect, fill=(0, 0, 0))
                        total_boxes_applied += 1

            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_PIXELS,
                graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )

            for first_rect, mask_str in pending_mask_inserts:
                _insert_scaled_partial_mask_text(page, first_rect, mask_str)

            affected_pages.append(page_num)

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(
            str(dst_path),
            garbage=4,
            deflate=True,
            clean=True,
        )
    except RedactionPipelineError:
        raise
    except Exception as exc:
        raise RedactionPipelineError(
            message=f"Falha ao aplicar redação física irreversível no PDF ({type(exc).__name__}).",
            stage="APPLY_PHYSICAL_REDACTIONS",
        ) from exc
    finally:
        doc.close()

    intermediate_bytes = dst_path.read_bytes()
    intermediate_sha256 = hashlib.sha256(intermediate_bytes).hexdigest()
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    logger.info(
        "Redação física concluída via PyMuPDF",
        extra={
            "total_pages": total_pages,
            "affected_pages": affected_pages,
            "total_boxes_applied": total_boxes_applied,
            "execution_time_ms": elapsed_ms,
        },
    )

    return PhysicalRedactionResult(
        original_pdf_path=src_path,
        intermediate_pdf_path=dst_path,
        original_sha256=original_sha256,
        intermediate_sha256=intermediate_sha256,
        total_pages=total_pages,
        affected_pages=affected_pages,
        total_boxes_applied=total_boxes_applied,
        category_counts=category_counts,
        widgets_removed=total_widgets_removed,
        annotations_removed=total_annots_removed,
        execution_time_ms=elapsed_ms,
    )
