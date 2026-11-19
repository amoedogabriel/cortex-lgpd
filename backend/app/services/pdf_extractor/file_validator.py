"""
Validador de Upload de PDF (`file_validator.py`).

Executa:
1. Verificação de tamanho máximo configurável (`MAX_UPLOAD_SIZE_BYTES`);
2. Verificação de assinatura de cabeçalho PDF (`%PDF-1.x` / `%PDF-2.x`) e MIME type real via `python-magic` (`application/pdf`);
3. Inspeção estrutural profunda com `pikepdf` para rejeitar:
   - PDFs criptografados / protegidos por senha;
   - PDFs corrompidos ou que excedam `MAX_PDF_PAGES`;
   - PDFs contendo scripts/ações maliciosas (`/JS`, `/JavaScript`, `/Launch`, `/EmbeddedFiles`, `/RichMedia`);
4. Persistência transitória em diretório isolado por `job_id` e cálculo determinístico do hash SHA-256.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf

from app.core.config import settings
from app.core.exceptions import FileTooLargeError, InvalidDocumentError

logger = logging.getLogger(__name__)

# Chaves de dicionário PDF estritamente proibidas por segurança institucional
_FORBIDDEN_PDF_KEYS: frozenset[str] = frozenset(
    {
        "/JS",
        "/JavaScript",
        "/Launch",
        "/EmbeddedFiles",
        "/RichMedia",
        "/SubmitForm",
        "/ImportData",
        "/GoToR",
        "/GoToE",
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedPDFMetadata:
    """Resultado imutável da validação e ingestão transitória de um PDF."""

    job_id: uuid.UUID
    file_path: Path
    sha256_hash: str
    file_size_bytes: int
    page_count: int
    mime_type: str


def _detect_mime_type(file_bytes: bytes) -> str:
    """
    Identifica o MIME type real via `python-magic` (`libmagic`), com fallback
    para `puremagic` ou inspeção de assinatura binária ISO 32000 caso a DLL
    nativa `libmagic` não esteja presente em um host Windows fora do Docker.
    """
    try:
        import magic  # type: ignore[import-untyped]

        detected = magic.from_buffer(file_bytes[:8192], mime=True)
        return str(detected).strip().lower()
    except Exception as exc:
        logger.debug(
            "libmagic nativa indisponível no host; acionando detector de assinatura binária",
            extra={"error": str(exc)},
        )
        try:
            import puremagic  # type: ignore[import-untyped]

            matches = puremagic.magic_string(file_bytes[:8192])
            if matches:
                return str(matches[0].mime_type).strip().lower()
        except Exception:
            pass

        if b"%PDF-" in file_bytes[:1024]:
            return "application/pdf"
        return "application/octet-stream"


def _inspect_pdf_structure_with_pikepdf(file_bytes: bytes, max_pages: int) -> int:
    """
    Abre o PDF via `pikepdf` validando integridade estrutural, ausência de criptografia
    de bloqueio e ausência de objetos maliciosos (`/JS`, `/JavaScript`, `/Launch`, `/EmbeddedFiles`).
    Retorna o total de páginas do documento.
    """
    stream = io.BytesIO(file_bytes)
    try:
        with pikepdf.open(stream, password="") as pdf:
            if pdf.is_encrypted:
                raise InvalidDocumentError(
                    "Documentos PDF criptografados ou protegidos por restrições de senha não são permitidos.",
                    details={"reason": "ENCRYPTED_PDF"},
                )

            page_count = len(pdf.pages)
            if page_count == 0:
                raise InvalidDocumentError(
                    "O arquivo PDF não possui páginas válidas.",
                    details={"reason": "EMPTY_PDF"},
                )
            if page_count > max_pages:
                raise InvalidDocumentError(
                    f"O documento possui {page_count} páginas, excedendo o limite máximo de {max_pages}.",
                    details={"reason": "MAX_PAGES_EXCEEDED", "page_count": page_count, "max_pages": max_pages},
                )

            # Varredura de catálogo Root (/Names, /OpenAction, /AA) e objetos indiretos do PDF
            _scan_pikepdf_objects_for_threats(pdf)

            return page_count

    except pikepdf.PasswordError as exc:
        raise InvalidDocumentError(
            "O arquivo PDF está protegido por senha e não pode ser processado.",
            details={"reason": "PASSWORD_PROTECTED_PDF"},
        ) from exc
    except InvalidDocumentError:
        raise
    except pikepdf.PdfError as exc:
        raise InvalidDocumentError(
            "O arquivo PDF encontra-se corrompido ou possui estrutura interna inválida.",
            details={"reason": "CORRUPTED_PDF_STRUCTURE"},
        ) from exc


def _inspect_dict_recursively(obj: Any, visited: set[tuple[int, int]], depth: int = 0) -> None:
    """Percorre recursivamente dicionários e listas do `pikepdf` (diretos e indiretos)."""
    if depth > 12:
        return

    objgen = getattr(obj, "objgen", (0, 0))
    if objgen != (0, 0):
        if objgen in visited:
            return
        visited.add(objgen)

    if isinstance(obj, pikepdf.Dictionary) or hasattr(obj, "keys"):
        try:
            keys = [str(k) for k in obj.keys()]
        except Exception:
            return

        found_forbidden = _FORBIDDEN_PDF_KEYS.intersection(keys)
        if found_forbidden:
            forbidden_item = sorted(found_forbidden)[0]
            raise InvalidDocumentError(
                f"O documento PDF contém objeto interno com instrução proibida ({forbidden_item}).",
                details={"reason": "MALICIOUS_PDF_OBJECT", "forbidden_key": forbidden_item},
            )

        if "/S" in keys:
            try:
                action_type = str(obj["/S"])
            except Exception:
                action_type = ""
            if action_type in _FORBIDDEN_PDF_KEYS:
                raise InvalidDocumentError(
                    f"O documento PDF contém ação executável não permitida ({action_type}).",
                    details={"reason": "MALICIOUS_PDF_ACTION", "action_type": action_type},
                )

        if "/EF" in keys and ("/F" in keys or "/UF" in keys):
            raise InvalidDocumentError(
                "O documento PDF contém arquivos embutidos (/EmbeddedFiles /EF) não permitidos.",
                details={"reason": "EMBEDDED_FILE_STREAM_DETECTED"},
            )

        for k in keys:
            try:
                child = obj[k]
            except Exception:
                continue
            _inspect_dict_recursively(child, visited, depth + 1)

    elif isinstance(obj, pikepdf.Array):
        for item in obj:
            _inspect_dict_recursively(item, visited, depth + 1)


def _scan_pikepdf_objects_for_threats(pdf: pikepdf.Pdf) -> None:
    """Inspeciona o catálogo Root e todos os objetos do PDF para bloquear JavaScript, Launch e EmbeddedFiles."""
    visited: set[tuple[int, int]] = set()
    _inspect_dict_recursively(pdf.Root, visited)
    for obj in pdf.objects:
        _inspect_dict_recursively(obj, visited)


def validate_and_store_pdf(
    file_bytes: bytes,
    job_id: uuid.UUID | str | None = None,
    *,
    base_temp_dir: Path | None = None,
    max_upload_bytes: int | None = None,
    max_pages: int | None = None,
) -> ValidatedPDFMetadata:
    """
    Valida integralmente um buffer de PDF e o persiste de forma isolada em
    `{base_temp_dir}/{job_id}/original.pdf`, retornando metadados e hash SHA-256.
    """
    effective_max_bytes = max_upload_bytes if max_upload_bytes is not None else settings.MAX_UPLOAD_SIZE_BYTES
    effective_max_pages = max_pages if max_pages is not None else settings.MAX_PDF_PAGES
    effective_temp_root = base_temp_dir if base_temp_dir is not None else settings.TEMP_STORAGE_DIR

    size_bytes = len(file_bytes)
    if size_bytes == 0:
        raise InvalidDocumentError(
            "O arquivo enviado está vazio.",
            details={"reason": "EMPTY_PAYLOAD"},
        )
    if size_bytes > effective_max_bytes:
        raise FileTooLargeError(max_bytes=effective_max_bytes, received_bytes=size_bytes)

    # 1. Verifica assinatura mágica %PDF- no início do buffer (primeiros 1024 bytes segundo especificação ISO 32000)
    header_window = file_bytes[:1024]
    if b"%PDF-" not in header_window:
        raise InvalidDocumentError(
            "Assinatura de cabeçalho PDF (%PDF-) ausente.",
            details={"reason": "INVALID_PDF_HEADER"},
        )

    # 2. Verifica MIME type real via python-magic
    mime_type = _detect_mime_type(file_bytes)
    if mime_type != "application/pdf":
        raise InvalidDocumentError(
            f"Tipo MIME inválido detectado ('{mime_type}'). Apenas 'application/pdf' é aceito.",
            details={"reason": "INVALID_MIME_TYPE", "detected_mime": mime_type},
        )

    # 3. Inspeciona estrutura interna via pikepdf
    page_count = _inspect_pdf_structure_with_pikepdf(file_bytes, max_pages=effective_max_pages)

    # 4. Calcula hash SHA-256 criptográfico
    sha256_hash = hashlib.sha256(file_bytes).hexdigest()

    # 5. Persiste em diretório isolado por job_id
    resolved_job_id = uuid.UUID(str(job_id)) if job_id is not None else uuid.uuid4()
    job_dir = effective_temp_root / str(resolved_job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    target_pdf_path = job_dir / "original.pdf"
    target_pdf_path.write_bytes(file_bytes)

    logger.info(
        "PDF validado e armazenado no diretório transitório do job",
        extra={
            "job_id": str(resolved_job_id),
            "sha256": sha256_hash,
            "file_size_bytes": size_bytes,
            "page_count": page_count,
        },
    )

    return ValidatedPDFMetadata(
        job_id=resolved_job_id,
        file_path=target_pdf_path,
        sha256_hash=sha256_hash,
        file_size_bytes=size_bytes,
        page_count=page_count,
        mime_type=mime_type,
    )
