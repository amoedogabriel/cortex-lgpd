/**
 * Contratos Tipados Estritos do Frontend Thin-Client (`frontend/src/types/document.ts`).
 * Alinhados 1:1 com os schemas Pydantic v2 e endpoints FastAPI do Backend UFAM.
 */

export type JobStatus =
  | "RECEIVED"
  | "ANALYZING"
  | "OCR_RUNNING"
  | "PARTIAL_REVIEW_READY"
  | "AWAITING_REVIEW"
  | "REDACTING"
  | "VALIDATING"
  | "COMPLETED"
  | "FAILED";

export type PiiCategory =
  | "PERSONAL_COMMON"
  | "PERSONAL_SENSITIVE"
  | "FINANCIAL_BANKING"
  | "MINOR_OR_DEPENDENT";

export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW";

export type PageContentType = "TEXTUAL" | "SCANNED" | "HYBRID";

/**
 * Modo de ação de proteção sobre cada ocorrência detectada:
 * - `BLACKOUT`: Tarja preta sólida (100% oculto)
 * - `PARTIAL_MASK`: Mascaramento parcial físico (`***.729.160-**`)
 * - `KEEP`: Manter visível sem tarjar
 */
export type RedactionActionMode = "BLACKOUT" | "PARTIAL_MASK" | "KEEP";

/**
 * Perfil semântico do documento detectado na Página 1:
 * - `PUBLIC_TRANSPARENCY`: Editais, Resultados, Homologações, Listas de Aprovados
 * - `RESTRICTED_PROCESS`: Despachos, Processos SEI, Laudos, Folhas de Pagamento
 */
export type DocumentProfile = "PUBLIC_TRANSPARENCY" | "RESTRICTED_PROCESS";

/**
 * Regra de expressão regular personalizada configurada na sessão antes do envio de PDFs.
 */
export interface CustomRegexRule {
  id: string;
  label: string;
  pattern: string;
  category: PiiCategory;
  action_mode?: RedactionActionMode;
  case_sensitive?: boolean;
}

/**
 * Entrada de log verbose estruturado por etapa do processamento (estritamente Zero-PII).
 */
export interface VerboseStepLog {
  id: string;
  sequence: number;
  step_number: number;
  stage: string;
  stage_label: string;
  level: "INFO" | "WARNING" | "ERROR" | string;
  message: string;
  duration_ms?: number | null;
  details?: Record<string, unknown>;
  timestamp: string;
}

/**
 * Coordenadas normalizadas universais no intervalo [0.0, 1.0] relativas à largura e altura da página.
 * No `<svg viewBox="0 0 1000 1000">`, multiplica-se por 1000.
 */
export interface BoundingBoxNormalized {
  page_number: number;
  x0_norm: number;
  y0_norm: number;
  x1_norm: number;
  y1_norm: number;
  category: PiiCategory;
}

/**
 * Caixa de tarjamento manual desenhada ou ajustada pelo servidor público na interface de revisão.
 */
export interface ManualRedactionBox extends BoundingBoxNormalized {
  id: string;
  label?: string;
}

/**
 * Ocorrência de dado pessoal detectada pelo motor híbrido (Regex + Validador Matemático + spaCy NLP + OCR ONNX).
 */
export interface DetectedOccurrence {
  id: string;
  page_number: number;
  entity_type: string;
  category: PiiCategory;
  x0_norm: number;
  y0_norm: number;
  x1_norm: number;
  y1_norm: number;
  confidence_score: number;
  confidence_level: ConfidenceLevel;
  group_id: string;
  detector_source: string;
  selected?: boolean;
  action_mode?: RedactionActionMode;
  recommended_mode?: RedactionActionMode;
  masked_preview?: string;
  is_continuation?: boolean;
  preview_text?: string;
}

/**
 * Item individual de decisão enviado ao endpoint `/redact`.
 */
export interface RedactionItem {
  id: string;
  action_mode: RedactionActionMode;
  masked_preview?: string;
}

/**
 * Metadados e ocorrências de uma página renderizada pelo servidor como `.webp` a 150 DPI.
 */
export interface PageData {
  page_number: number;
  page_type: PageContentType;
  width_px: number;
  height_px: number;
  image_url: string;
  ocr_executed: boolean;
  processing_time_ms: number;
  occurrences: DetectedOccurrence[];
}

/**
 * Payload enviado para `POST /api/v1/documents/{job_id}/redact`.
 */
export interface RedactionRequest {
  selected_ids: string[] | null;
  items?: RedactionItem[];
  redaction_items?: RedactionItem[];
  global_mode?: RedactionActionMode;
  manual_boxes: BoundingBoxNormalized[];
  anonymization_disclaimer_acknowledged: boolean;
}

/**
 * Resultado da validação cirúrgica pós-tarjamento e trilha de auditoria Zero-PII.
 */
export interface SecurityValidationResult {
  job_id: string;
  original_document_sha256: string;
  redacted_document_sha256: string;
  count_personal_common: number;
  count_personal_sensitive: number;
  count_financial_banking: number;
  count_minor_or_dependent: number;
  total_redactions_applied: number;
  category_counts: Record<PiiCategory, number>;
  affected_pages: number[];
  total_pages: number;
  security_validation_passed: boolean;
  created_at: string;
}

/**
 * Resposta de `POST /api/v1/documents/upload`.
 */
export interface UploadDocumentResponse {
  job_id: string;
  status: JobStatus;
  document_profile?: DocumentProfile;
  original_file_sha256: string;
  file_size_bytes: number;
  total_pages: number;
  processed_pages: number;
  ignore_sei_standard_blocks?: boolean;
  verbose_logs?: VerboseStepLog[];
  stream_url: string;
  occurrences_url: string;
  first_page_image_url: string;
}

/**
 * Resposta de `GET /api/v1/documents/{job_id}/occurrences`.
 */
export interface DocumentOccurrencesResponse {
  job_id: string;
  status: JobStatus;
  document_profile?: DocumentProfile;
  original_file_sha256: string;
  redacted_file_sha256?: string | null;
  total_pages: number;
  processed_pages: number;
  pages: Record<string, PageData>;
  occurrences: DetectedOccurrence[];
  verbose_logs?: VerboseStepLog[];
  audit_log?: SecurityValidationResult | null;
}

/**
 * Resposta de `POST /api/v1/documents/{job_id}/redact`.
 */
export interface RedactionExecutionResponse {
  job_id: string;
  status: JobStatus;
  document_profile?: DocumentProfile;
  original_file_sha256: string;
  redacted_file_sha256: string;
  verbose_logs?: VerboseStepLog[];
  audit_log: SecurityValidationResult;
  download_pdf_url: string;
  download_report_url: string;
}

/**
 * Etapas reais de processamento exibidas no card individual de progresso (1 a 6).
 */
export type PipelineStageNumber = 1 | 2 | 3 | 4 | 5 | 6;

export type DocumentBadgeStatus =
  | "Aguardando"
  | "Carregando"
  | "Processando"
  | "Concluído"
  | "Erro";

export interface DocumentQueueItemState {
  clientId: string;
  file: File;
  fileName: string;
  fileSizeBytes: number;
  badgeStatus: DocumentBadgeStatus;
  stageNumber: PipelineStageNumber;
  stageLabel: string;
  progressPercent: number | null; // `null` indica progresso indeterminado honesto
  jobId: string | null;
  jobStatus: JobStatus;
  documentProfile: DocumentProfile;
  processedPages: number;
  totalPages: number;
  isReadyForReview: boolean;
  isAnalysisComplete: boolean;
  isRedactionComplete: boolean;
  pagesByNumber: Record<number, PageData>;
  occurrences: DetectedOccurrence[];
  originalSha256: string | null;
  redactedSha256: string | null;
  auditLog: SecurityValidationResult | null;
  downloadPdfUrl: string | null;
  downloadReportUrl: string | null;
  errorMessage: string | null;
  customRegexes?: CustomRegexRule[];
  ignoreSeiStandardBlocks?: boolean;
  // Estado isolado de revisão HITL por documento
  currentPage: number;
  deselectedIds: string[];
  actionModesById: Record<string, RedactionActionMode>;
  manualBoxes: ManualRedactionBox[];
}

/**
 * Envelope padronizado de eventos Server-Sent Events (SSE) emitidos por `/api/v1/documents/{job_id}/stream`.
 */
export interface SseEventEnvelope<T = Record<string, unknown>> {
  event:
    | "verbose_step"
    | "page_ready"
    | "progress"
    | "analysis_complete"
    | "redaction_complete"
    | "error";
  job_id: string;
  timestamp: string;
  data: T;
}

export interface PageReadyEventPayload {
  page_number: number;
  total_pages: number;
  status: JobStatus;
  document_profile?: DocumentProfile;
  image_url: string;
  occurrences_count: number;
  occurrences: DetectedOccurrence[];
}

export interface ProgressEventPayload {
  processed_pages: number;
  total_pages: number;
  percentage: number;
  status: JobStatus;
}

export interface AnalysisCompleteEventPayload {
  status: JobStatus;
  document_profile?: DocumentProfile;
  total_pages: number;
  total_occurrences: number;
}

export interface RedactionCompleteEventPayload {
  status: JobStatus;
  original_sha256: string;
  redacted_sha256: string;
  total_redactions_applied: number;
  affected_pages: number[];
  security_validation_passed: boolean;
  download_pdf_url: string;
  download_report_url: string;
}
