# Guia de Implantação, Segurança e Operação Institucional — UFAM

Este documento descreve os procedimentos de implantação em produção, configuração de variáveis de ambiente, integração com o provedor de identidade institucional (**Keycloak OIDC**), política de retenção zero de arquivos transitórios (`tmpfs`) e rotinas de backup do banco de dados de auditoria para o **Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM**.

---

## 1. Topologia de Implantação Recomendada (STI / UFAM)

A arquitetura foi dimensionada para operar integralmente *on-premises* na infraestrutura da Superintendência de Tecnologia da Informação (STI/UFAM), sem qualquer dependência de serviços externos de nuvem ou APIs de terceiros:

| Componente | Container / Serviço | Recursos Mínimos Recomendados | Papel Arquitetural |
| :--- | :--- | :--- | :--- |
| **Reverse Proxy** | Nginx Institucional | 1 vCPU, 512 MB RAM | Terminação TLS 1.3, cabeçalhos de segurança HTTP, desativação de buffering para SSE (`X-Accel-Buffering: no`) |
| **Frontend Thin-Client** | `frontend` (Next.js 15) | 1 vCPU, 512 MB RAM | Interface leve em cores sólidas + visualizador WebP/SVG Single-Page |
| **Backend API** | `backend` (FastAPI + Uvicorn) | 2 vCPUs, 2 GB RAM | Validação de upload `pikepdf`, prioridade da Página 1, streaming SSE e download de documentos |
| **Worker Assíncrono** | `worker` (Celery + ONNX + spaCy) | 4 vCPUs, 4 GB RAM | Processamento de páginas 2..N, OCR acelerado via ONNX Runtime em CPU, redação física `PyMuPDF` e validação cirúrgica |
| **Agendador de Expurgo** | `beat` (Celery Beat) | 0.5 vCPU, 256 MB RAM | Execução periódica de `purge_expired_temp_files` e `cleanup_expired_jobs_task` |
| **Cache e Mensageria** | `redis` (Redis 7 Alpine) | 1 vCPU, 1 GB RAM | Broker Celery, estado transitório de revisão (TTL estrito de 2h) e canais Pub/Sub por job |
| **Banco de Auditoria** | `postgres` (PostgreSQL 16 Alpine) | 2 vCPUs, 2 GB RAM, Disco SSD | Persistência exclusiva de metadados de jobs e contadores quantitativos `AuditLog` (Zero-PII) |

---

## 2. Referência Completa de Variáveis de Ambiente (`.env`)

Todas as variáveis documentadas em [`.env.example`](file:///C:/Users/gbas9/Desktop/ferramenta/.env.example) devem ser revisadas antes da subida em produção:

### 2.1. Aplicação e Limites de Upload
- `APP_ENV=production`: Ativa políticas estritas de segurança e desabilita recarregamento automático.
- `LOG_LEVEL=INFO`: Emite logs estruturados em JSON filtrados pelo `ZeroPIIFilter`.
- `MAX_UPLOAD_SIZE_BYTES=52428800`: Limite máximo por PDF enviado (padrão: 50 MB).
- `MAX_PDF_PAGES=500`: Número máximo de páginas aceitas por documento.

### 2.2. Armazenamento Transitório Isolado e Política Zero-Retention
- `TEMP_STORAGE_DIR=/tmp/ufam_redaction_spool`: Caminho montado em volume `tmpfs` (RAM) compartilhado apenas entre `backend` e `worker`.
- `TEMP_FILE_TTL_SECONDS=1800`: Tempo máximo de vida (30 minutos) dos arquivos `original.pdf`, `redacted.pdf`, `report.pdf` e imagens `.webp` no volume temporário.
- `CLEANUP_INTERVAL_SECONDS=300`: Intervalo (5 minutos) em que o Celery Beat varre o diretório `tmpfs`, sobrescreve o cabeçalho dos arquivos expirados com zeros (`\x00`) e remove os inodes e chaves do Redis.

### 2.3. Autenticação e Integração com Keycloak OIDC Institucional
O módulo [`backend/app/core/security.py`](file:///C:/Users/gbas9/Desktop/ferramenta/backend/app/core/security.py) implementa validação de tokens JWT OAuth2/OIDC com cache de chaves públicas JWKS e mapeamento de papéis RBAC (`realm_access.roles` e `resource_access.<client>.roles`):
- `KEYCLOAK_ENABLED=true`: Habilita a exigência obrigatória de token Bearer JWT emitido pelo Keycloak da UFAM.
- `KEYCLOAK_SERVER_URL=https://auth.ufam.edu.br`: URL base do servidor Keycloak institucional.
- `KEYCLOAK_REALM=ufam`: Nome do Realm institucional.
- `KEYCLOAK_CLIENT_ID=ufam-redaction-api`: Identificador do cliente OIDC configurado no Keycloak.
- `KEYCLOAK_ALGORITHMS=RS256`: Algoritmo assimétrico de assinatura aceito.

**Perfis RBAC suportados (`UserRole`):**
- `OPERATOR`: Servidor público autorizado a anexar documentos, revisar marcações e exportar PDFs tarjados;
- `REVIEWER`: Revisor administrativo de segunda instância;
- `AUDITOR` / `DPO`: Acesso de leitura às métricas agregadas e trilhas de auditoria (`AuditLog`) sem acesso a documentos transitórios;
- `ADMIN`: Administração técnica da plataforma.

---

## 3. Subida Completa com Docker Compose

1. Construa e inicie os serviços:
   ```bash
   docker compose up --build -d
   ```
2. Aplique as migrações de esquema do PostgreSQL via Alembic:
   ```bash
   docker compose exec backend alembic upgrade head
   ```
3. Inspecione os logs JSON estruturados:
   ```bash
   docker compose logs -f backend worker
   ```

---

## 4. Configuração do Proxy Reverso Nginx para Server-Sent Events (SSE)

Para que o streaming progressivo página a página (`/api/v1/documents/{job_id}/stream`) entregue o evento `page_ready` da Página 1 imediatamente sem retenção em buffer de proxy, configure o bloco `location` do Nginx com:

```nginx
location /api/v1/documents/ {
    proxy_pass http://backend:8000/api/v1/documents/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Essencial para Server-Sent Events (First-Page-First sem atraso)
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
    proxy_read_timeout 3600s;
}
```

---

## 5. Procedimentos de Backup e Auditoria (PostgreSQL 16)

Como a tabela `audit_logs` armazena **exclusivamente metadados criptográficos e contadores quantitativos** (Zero-PII), os backups do banco de dados não contêm dados pessoais sensíveis extraídos dos documentos processados.

### Backup Diário Automatizado (`pg_dump`)
```bash
docker compose exec -T postgres pg_dump -U ufam_admin -d ufam_redaction_db -F c > backup_auditoria_ufam_$(date +%Y%m%d).dump
```

### Restauração de Backup (`pg_restore`)
```bash
docker compose exec -T postgres pg_restore -U ufam_admin -d ufam_redaction_db --clean < backup_auditoria_ufam_YYYYMMDD.dump
```
