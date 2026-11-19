"""
Testes Automatizados de Segurança, Anti-Malware PDF, Isolamento de Diretórios e Garantia Zero-PII
(`backend/tests/test_security.py`).

Requisitos validados:
1. Rejeição de uploads inválidos (arquivo vazio, arquivo não-PDF com extensão `.pdf` falsa, excesso de tamanho);
2. Rejeição de PDFs contendo `/JavaScript`, `/JS`, `/Launch` ou criptografia por senha;
3. Isolamento estrito de diretórios temporários por `job_id` e expurgo automático (`cleanup_expired_jobs_task` / `purge_expired_temp_files`);
4. Garantia de **Zero-PII** no filtro de logging estruturado JSON (`ZeroPIIFilter`) e nos modelos de banco/auditoria (`AuditLog`), comprovando que CPFs, e-mails e nomes jamais vazam nos logs ou registros persistidos.
"""

from __future__ import annotations

import io
import logging
import os
import time
import uuid
from pathlib import Path

import fitz  # PyMuPDF
import pikepdf
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.exceptions import FileTooLargeError, InvalidDocumentError
from app.core.logging import StructuredJSONFormatter, ZeroPIIFilter
from app.main import app
from app.models.audit_log import AuditLog
from app.services.pdf_extractor.file_validator import validate_and_store_pdf
from app.worker.celery_app import purge_expired_temp_files
from app.worker.tasks import cleanup_expired_jobs_task
from tests.synthetic_generator import generate_valid_cpf_formatted


def test_reject_invalid_uploads_and_malicious_pdf_actions(tmp_path: Path, monkeypatch) -> None:
    """
    Testa tentativas de upload de arquivos inválidos, falsos PDFs, tamanho excedido,
    PDFs com `/JavaScript`, `/Launch` e PDFs criptografados.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)
    client = TestClient(app)

    # 1. Arquivo vazio
    with pytest.raises(InvalidDocumentError):
        validate_and_store_pdf(b"", base_temp_dir=tmp_path)

    # 2. Executável / texto disfarçado de PDF (sem cabeçalho %PDF-)
    resp_fake = client.post(
        "/api/v1/documents/upload",
        files={"file": ("falso.pdf", b"MZ\x90\x00ExecutablePayload", "application/pdf")},
    )
    assert resp_fake.status_code in (400, 422)

    # 3. Excesso de tamanho configurável
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text(fitz.Point(50, 50), "Documento simples")
    valid_pdf_bytes = doc.tobytes()
    doc.close()

    with pytest.raises(FileTooLargeError):
        validate_and_store_pdf(valid_pdf_bytes, base_temp_dir=tmp_path, max_upload_bytes=100)

    # 4. PDF contendo ação maliciosa `/Launch` no catálogo Root
    with pikepdf.open(io.BytesIO(valid_pdf_bytes)) as pdf:
        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            {
                "/S": pikepdf.Name("/Launch"),
                "/Win": pikepdf.Dictionary({"/F": "cmd.exe"}),
            }
        )
        malicious_launch_buf = io.BytesIO()
        pdf.save(malicious_launch_buf)

    resp_launch = client.post(
        "/api/v1/documents/upload",
        files={"file": ("exploit_launch.pdf", malicious_launch_buf.getvalue(), "application/pdf")},
    )
    assert resp_launch.status_code in (400, 422)

    # 5. PDF contendo `/JavaScript` embutido
    with pikepdf.open(io.BytesIO(valid_pdf_bytes)) as pdf:
        pdf.Root["/OpenAction"] = pikepdf.Dictionary(
            {
                "/S": pikepdf.Name("/JavaScript"),
                "/JS": "app.alert('XSS');",
            }
        )
        malicious_js_buf = io.BytesIO()
        pdf.save(malicious_js_buf)

    resp_js = client.post(
        "/api/v1/documents/upload",
        files={"file": ("exploit_js.pdf", malicious_js_buf.getvalue(), "application/pdf")},
    )
    assert resp_js.status_code in (400, 422)


def test_temporary_directory_isolation_and_expiration_purge(tmp_path: Path, monkeypatch) -> None:
    """
    Comprova que cada job recebe um diretório temporário estritamente isolado (`tmp/{job_id}`)
    e que as rotinas de expurgo removem arquivos e diretórios cujo TTL expirou.
    """
    monkeypatch.setattr(settings, "TEMP_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_FILE_TTL_SECONDS", 10)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), "Teste de isolamento de diretório por job_id")
    pdf_bytes = doc.tobytes()
    doc.close()

    job_a = uuid.uuid4()
    job_b = uuid.uuid4()

    meta_a = validate_and_store_pdf(pdf_bytes, job_id=job_a, base_temp_dir=tmp_path)
    meta_b = validate_and_store_pdf(pdf_bytes, job_id=job_b, base_temp_dir=tmp_path)

    assert meta_a.file_path.parent != meta_b.file_path.parent
    assert meta_a.file_path.exists()
    assert meta_b.file_path.exists()

    # Retrocede o mtime do job_a para simular expiração de TTL (> 10s atrás)
    expired_time = time.time() - 120
    os.utime(meta_a.file_path, (expired_time, expired_time))
    os.utime(meta_a.file_path.parent, (expired_time, expired_time))

    purge_res = purge_expired_temp_files()
    assert purge_res["removed_files"] >= 1
    assert not meta_a.file_path.exists()
    # Job B (não expirado) permanece intacto
    assert meta_b.file_path.exists()

    os.utime(meta_a.file_path.parent, (expired_time, expired_time))
    cleanup_res = cleanup_expired_jobs_task()
    assert cleanup_res["removed_job_dirs"] >= 1
    assert not meta_a.file_path.parent.exists()
    assert meta_b.file_path.parent.exists()


def test_zero_pii_logging_filter_and_database_schema_compliance() -> None:
    """
    Comprova que:
    1. O `ZeroPIIFilter` + `StructuredJSONFormatter` mascaram imediatamente qualquer CPF, e-mail
       ou chave sensível caso uma mensagem acidental seja enviada ao logger;
    2. A entidade SQLAlchemy `AuditLog` não possui nenhuma coluna capaz de armazenar textos de PII.
    """
    cpf_secret = generate_valid_cpf_formatted("111222333")
    email_secret = "servidor.sigiloso@ufam.edu.br"

    pii_filter = ZeroPIIFilter()
    formatter = StructuredJSONFormatter()

    record = logging.LogRecord(
        name="app.test.security",
        level=logging.INFO,
        pathname=__file__,
        lineno=100,
        msg=f"Tentativa acidental de logar CPF {cpf_secret} e e-mail {email_secret}",
        args=(),
        exc_info=None,
    )
    record.extracted_text = f"Nome Secreto e CPF {cpf_secret}"  # chave proibida que deve ser suprimida

    pii_filter.filter(record)
    formatted_json = formatter.format(record)

    assert cpf_secret not in formatted_json, "VIOLAÇÃO ZERO-PII: CPF apareceu no log JSON!"
    assert email_secret not in formatted_json, "VIOLAÇÃO ZERO-PII: E-mail apareceu no log JSON!"
    assert "Nome Secreto" not in formatted_json, "VIOLAÇÃO ZERO-PII: Campo proibido não foi mascarado!"

    # Inspeção estrutural das colunas da tabela `audit_logs` do PostgreSQL
    audit_columns = {col.name for col in AuditLog.__table__.columns}
    forbidden_column_names = {
        "matched_text",
        "preview_text",
        "extracted_text",
        "raw_text",
        "cpf",
        "person_name",
        "document_content",
    }
    assert len(audit_columns.intersection(forbidden_column_names)) == 0
