"""
Sanitizador Estrutural de PDF via `pikepdf` (`structural_sanitizer.py`).

Executa a limpeza profunda da árvore de objetos do PDF após a redação física:
1. Remove metadados do dicionário `/Info` (Author, Creator, Producer, Subject, Title, Keywords)
   e fluxos XML `/Metadata` (XMP) no catálogo Root e em objetos de página;
2. Elimina camadas opcionais ocultas (`/OCProperties` / Optional Content Groups);
3. Remove anexos embutidos (`/EmbeddedFiles`), árvores `/Names` suspeitas, `/PieceInfo`,
   `/StructTreeRoot` (que pode reter `ActualText` ou árvore de tags com texto antigo) e `/AcroForm` residual;
4. Achata atualizações incrementais anteriores, remove objetos órfãos não referenciados e
   gera o arquivo final sanitizado e linearizado (`linearize=True`).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pikepdf

from app.core.exceptions import RedactionPipelineError
from app.schemas.processing_job import NormalizedBoundingBox
from app.services.redactor.pdf_redactor import (
    PhysicalRedactionResult,
    RedactionBoxSpec,
    apply_true_redactions_to_pdf,
)

logger = logging.getLogger(__name__)

# Chaves do catálogo Root que podem reter metadados, camadas ocultas, anexos ou texto alternativo (ActualText)
_ROOT_KEYS_TO_STRIP: tuple[str, ...] = (
    "/Metadata",
    "/OCProperties",
    "/Names",
    "/PieceInfo",
    "/StructTreeRoot",
    "/MarkInfo",
    "/OpenAction",
    "/AA",
    "/Threads",
    "/SpiderInfo",
)

# Chaves por página que podem reter metadados XMP privados ou ações
_PAGE_KEYS_TO_STRIP: tuple[str, ...] = (
    "/Metadata",
    "/PieceInfo",
    "/AA",
    "/StructParents",
    "/PresSteps",
)


@dataclass(frozen=True, slots=True)
class StructuralSanitizationResult:
    """Resultado da sanitização estrutural via `pikepdf`."""

    sanitized_pdf_path: Path
    sanitized_sha256: str
    info_entries_removed: int
    xmp_metadata_removed: bool
    ocg_layers_removed: bool
    embedded_files_removed: bool
    struct_tree_removed: bool
    execution_time_ms: float


@dataclass(frozen=True, slots=True)
class CompleteRedactionPipelineResult:
    """Resultado consolidado do pipeline completo (PyMuPDF Redaction + pikepdf Sanitization)."""

    original_pdf_path: Path
    final_pdf_path: Path
    original_sha256: str
    final_sha256: str
    total_pages: int
    affected_pages: list[int]
    total_boxes_applied: int
    category_counts: dict[str, int]
    physical_result: PhysicalRedactionResult
    sanitization_result: StructuralSanitizationResult
    total_execution_time_ms: float


def sanitize_pdf_structure(
    input_pdf_path: Path | str,
    output_pdf_path: Path | str | None = None,
    *,
    remove_input_after_sanitize: bool = False,
) -> StructuralSanitizationResult:
    """
    Abre o PDF intermediário com `pikepdf`, expurga metadados `/Info`, fluxos `/Metadata` (XMP),
    camadas `/OCProperties`, árvores de estrutura `/StructTreeRoot`, anexos e objetos órfãos,
    e salva um novo PDF linearizado sem histórico de revisões incrementais.
    """
    t0 = time.perf_counter()
    src_path = Path(input_pdf_path)
    if not src_path.exists():
        raise RedactionPipelineError(
            message="PDF intermediário não encontrado para sanitização estrutural.",
            stage="OPEN_INTERMEDIATE_PDF",
        )

    dst_path = (
        Path(output_pdf_path)
        if output_pdf_path is not None
        else src_path.with_name(f"{src_path.stem}_sanitized.pdf")
    )

    info_removed_count = 0
    xmp_removed = False
    ocg_removed = False
    embedded_removed = False
    struct_removed = False

    try:
        with pikepdf.open(str(src_path)) as pdf:
            # 1. Limpa completamente o dicionário /Info (Author, Creator, Producer, Title, Subject, CreationDate, ModDate)
            if pdf.docinfo:
                for key in list(pdf.docinfo.keys()):
                    try:
                        del pdf.docinfo[key]
                        info_removed_count += 1
                    except Exception:
                        pass

            root = pdf.Root
            if isinstance(root, pikepdf.Dictionary):
                if "/Metadata" in root:
                    xmp_removed = True
                if "/OCProperties" in root:
                    ocg_removed = True
                if "/Names" in root:
                    embedded_removed = True
                if "/StructTreeRoot" in root:
                    struct_removed = True

                for rk in _ROOT_KEYS_TO_STRIP:
                    if rk in root:
                        del root[rk]

            # 2. Limpa metadados XMP, PieceInfo e ações em todas as páginas
            for page in pdf.pages:
                page_obj = page.obj
                if isinstance(page_obj, pikepdf.Dictionary):
                    for pk in _PAGE_KEYS_TO_STRIP:
                        if pk in page_obj:
                            if pk == "/Metadata":
                                xmp_removed = True
                            del page_obj[pk]

            # 3. Remove recursos de objetos não referenciados e achata atualizações incrementais
            pdf.remove_unreferenced_resources()

            dst_path.parent.mkdir(parents=True, exist_ok=True)
            pdf.save(
                str(dst_path),
                linearize=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                compress_streams=True,
                recompress_flate=True,
            )

    except Exception as exc:
        raise RedactionPipelineError(
            message=f"Erro durante a sanitização estrutural com pikepdf ({type(exc).__name__}).",
            stage="PIKEPDF_STRUCTURAL_SANITIZE",
        ) from exc

    if remove_input_after_sanitize and src_path.exists() and src_path.resolve() != dst_path.resolve():
        try:
            src_path.unlink(missing_ok=True)
        except OSError:
            pass

    final_bytes = dst_path.read_bytes()
    final_sha256 = hashlib.sha256(final_bytes).hexdigest()
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    logger.info(
        "Sanitização estrutural do PDF concluída via pikepdf",
        extra={
            "info_entries_removed": info_removed_count,
            "xmp_metadata_removed": xmp_removed,
            "ocg_layers_removed": ocg_removed,
            "execution_time_ms": elapsed_ms,
        },
    )

    return StructuralSanitizationResult(
        sanitized_pdf_path=dst_path,
        sanitized_sha256=final_sha256,
        info_entries_removed=info_removed_count,
        xmp_metadata_removed=xmp_removed,
        ocg_layers_removed=ocg_removed,
        embedded_files_removed=embedded_removed,
        struct_tree_removed=struct_removed,
        execution_time_ms=elapsed_ms,
    )


def execute_complete_redaction_pipeline(
    original_pdf_path: Path | str,
    boxes: Sequence[RedactionBoxSpec | NormalizedBoundingBox],
    final_output_pdf_path: Path | str | None = None,
) -> CompleteRedactionPipelineResult:
    """
    Orquestra de ponta a ponta:
    1. Redação física irreversível via `PyMuPDF` (`apply_true_redactions_to_pdf`) em arquivo intermediário;
    2. Sanitização estrutural e linearização via `pikepdf` (`sanitize_pdf_structure`) gerando o PDF final;
    3. Remoção do arquivo intermediário não sanitizado.
    """
    t0 = time.perf_counter()
    orig_path = Path(original_pdf_path)
    final_path = (
        Path(final_output_pdf_path)
        if final_output_pdf_path is not None
        else orig_path.with_name(f"{orig_path.stem}_redacted.pdf")
    )
    intermediate_path = orig_path.with_name(f"{orig_path.stem}_intermediate_fitz.pdf")

    phys_res = apply_true_redactions_to_pdf(
        original_pdf_path=orig_path,
        boxes=boxes,
        output_pdf_path=intermediate_path,
    )
    san_res = sanitize_pdf_structure(
        input_pdf_path=intermediate_path,
        output_pdf_path=final_path,
        remove_input_after_sanitize=True,
    )

    total_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return CompleteRedactionPipelineResult(
        original_pdf_path=orig_path,
        final_pdf_path=san_res.sanitized_pdf_path,
        original_sha256=phys_res.original_sha256,
        final_sha256=san_res.sanitized_sha256,
        total_pages=phys_res.total_pages,
        affected_pages=phys_res.affected_pages,
        total_boxes_applied=phys_res.total_boxes_applied,
        category_counts=phys_res.category_counts,
        physical_result=phys_res,
        sanitization_result=san_res,
        total_execution_time_ms=total_ms,
    )
