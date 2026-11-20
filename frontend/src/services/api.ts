"use client";

/**
 * Cliente REST e Hook de Streaming Server-Sent Events (`useDocumentStream`)
 * para o Pipeline Progressivo First-Page-First (`frontend/src/services/api.ts`).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AnalysisCompleteEventPayload,
  CustomRegexRule,
  DetectedOccurrence,
  DocumentOccurrencesResponse,
  DocumentProfile,
  JobStatus,
  PageData,
  PageReadyEventPayload,
  ProgressEventPayload,
  DocumentBadgeStatus,
  PipelineStageNumber,
  RedactionCompleteEventPayload,
  RedactionExecutionResponse,
  RedactionRequest,
  SecurityValidationResult,
  SseEventEnvelope,
  UploadDocumentResponse,
  VerboseStepLog,
} from "@/types/document";

export const PIPELINE_STAGE_LABELS: Record<PipelineStageNumber, string> = {
  1: "Enviando arquivo...",
  2: "Lendo estrutura do PDF...",
  3: "Extraindo conteúdo / Executando OCR...",
  4: "Analisando contexto e identificando dados pessoais (IA)...",
  5: "Aplicando anonimização / gerando documento final...",
  6: "Concluído com sucesso",
};

export function derivePipelineProgress(params: {
  jobStatus: JobStatus;
  latestBackendStage?: string | null;
  processedPages?: number;
  totalPages?: number;
}): {
  badgeStatus: DocumentBadgeStatus;
  stageNumber: PipelineStageNumber;
  stageLabel: string;
  progressPercent: number | null;
} {
  const { jobStatus, latestBackendStage, processedPages = 0, totalPages = 0 } = params;

  if (jobStatus === "COMPLETED") {
    return {
      badgeStatus: "Concluído",
      stageNumber: 6,
      stageLabel: PIPELINE_STAGE_LABELS[6],
      progressPercent: 100,
    };
  }

  if (
    jobStatus === "REDACTING" ||
    jobStatus === "VALIDATING" ||
    latestBackendStage === "ETAPA_4_DECISAO_CONSOLIDADA" ||
    latestBackendStage === "ETAPA_5_REDACAO_FISICA" ||
    latestBackendStage === "ETAPA_6_VALIDACAO_CIRURGICA" ||
    latestBackendStage === "ETAPA_7_EXPORTACAO_AUDITORIA"
  ) {
    return {
      badgeStatus: "Processando",
      stageNumber: 5,
      stageLabel: PIPELINE_STAGE_LABELS[5],
      progressPercent: null,
    };
  }

  if (jobStatus === "AWAITING_REVIEW" || latestBackendStage === "ETAPA_4_AGUARDANDO_REVISAO") {
    return {
      badgeStatus: "Processando",
      stageNumber: 4,
      stageLabel: PIPELINE_STAGE_LABELS[4],
      progressPercent: 100,
    };
  }

  const realPagePct =
    totalPages > 0 && processedPages > 0
      ? Math.min(99, Math.max(1, Math.round((processedPages / totalPages) * 100)))
      : null;

  if (
    jobStatus === "PARTIAL_REVIEW_READY" ||
    latestBackendStage === "ETAPA_3_DETECCAO_LGPD" ||
    latestBackendStage === "ETAPA_3_FIRST_PAGE_READY"
  ) {
    return {
      badgeStatus: "Processando",
      stageNumber: 4,
      stageLabel: PIPELINE_STAGE_LABELS[4],
      progressPercent: realPagePct,
    };
  }

  if (
    jobStatus === "OCR_RUNNING" ||
    latestBackendStage === "ETAPA_2_EXTRACAO_OCR" ||
    latestBackendStage === "ETAPA_2_PERFIL_DOCUMENTAL"
  ) {
    return {
      badgeStatus: "Processando",
      stageNumber: 3,
      stageLabel: PIPELINE_STAGE_LABELS[3],
      progressPercent: realPagePct,
    };
  }

  return {
    badgeStatus: "Processando",
    stageNumber: 2,
    stageLabel: PIPELINE_STAGE_LABELS[2],
    progressPercent: null,
  };
}

const ENV_API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(/\/+$/, "");

function resolveApiBaseUrl(): string {
  if (ENV_API_BASE_URL) {
    return ENV_API_BASE_URL;
  }
  // Usa caminhos relativos same-origin (`/api/v1/...`) roteados pelo rewrite do Next.js
  // (`frontend/next.config.ts`) para evitar bloqueios de CORS no navegador.
  return "";
}

export function getApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${resolveApiBaseUrl()}${normalizedPath}`;
}

export function appendRevisionParam(url: string, rev: number = Date.now()): string {
  const separator = url.includes("?") ? "&" : "?";
  const cleaned = url.replace(/([?&])rev=\d+(&?)/, (_, p1, p2) => (p2 ? p1 : ""));
  const sep = cleaned.includes("?") ? "&" : "?";
  return `${cleaned}${sep}rev=${rev}`;
}

/**
 * Envia o arquivo PDF para validação e início imediato da Página 1 (First-Page-First).
 */
export async function uploadPdfDocument(
  file: File,
  deferRemainingPages: boolean = true,
  customRegexes?: CustomRegexRule[],
  ignoreSeiStandardBlocks: boolean = true
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("ignore_sei_standard_blocks", String(ignoreSeiStandardBlocks));
  if (customRegexes && customRegexes.length > 0) {
    formData.append(
      "custom_regexes",
      JSON.stringify(
        customRegexes.map((r) => ({
          label: r.label,
          pattern: r.pattern,
          category: r.category,
          action_mode: r.action_mode ?? null,
          case_sensitive: Boolean(r.case_sensitive),
        }))
      )
    );
  }

  const response = await fetch(
    getApiUrl(`/api/v1/documents/upload?defer_remaining_pages=${deferRemainingPages}`),
    {
      method: "POST",
      body: formData,
    }
  );

  if (!response.ok) {
    let errorDetail = `Falha no envio do PDF (HTTP ${response.status}).`;
    try {
      const errJson = await response.json();
      if (errJson?.error?.message) {
        errorDetail = errJson.error.message;
      } else if (typeof errJson?.detail === "string") {
        errorDetail = errJson.detail;
      }
    } catch {
      // mantém mensagem padrão
    }
    throw new Error(errorDetail);
  }

  return (await response.json()) as UploadDocumentResponse;
}

/**
 * Envia o arquivo PDF via XMLHttpRequest reportando o progresso real de bytes enviados
 * e transicionando para a leitura estrutural assim que o corpo HTTP termina de subir.
 */
export function uploadPdfDocumentWithProgress(
  file: File,
  deferRemainingPages: boolean = true,
  callbacks?: {
    onUploadProgress?: (percent: number) => void;
    onUploadBodySent?: () => void;
  },
  customRegexes?: CustomRegexRule[],
  ignoreSeiStandardBlocks: boolean = true
): Promise<UploadDocumentResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("ignore_sei_standard_blocks", String(ignoreSeiStandardBlocks));
    if (customRegexes && customRegexes.length > 0) {
      formData.append(
        "custom_regexes",
        JSON.stringify(
          customRegexes.map((r) => ({
            label: r.label,
            pattern: r.pattern,
            category: r.category,
            action_mode: r.action_mode ?? null,
            case_sensitive: Boolean(r.case_sensitive),
          }))
        )
      );
    }

    const xhr = new XMLHttpRequest();
    xhr.open(
      "POST",
      getApiUrl(`/api/v1/documents/upload?defer_remaining_pages=${deferRemainingPages}`),
      true
    );

    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable && evt.total > 0) {
        const pct = Math.min(100, Math.round((evt.loaded / evt.total) * 100));
        callbacks?.onUploadProgress?.(pct);
      }
    };

    xhr.upload.onload = () => {
      callbacks?.onUploadBodySent?.();
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadDocumentResponse);
        } catch {
          reject(new Error("Resposta inválida recebida do servidor após o envio do PDF."));
        }
        return;
      }
      let errorDetail = `Falha no envio do PDF (HTTP ${xhr.status}).`;
      try {
        const errJson = JSON.parse(xhr.responseText);
        if (errJson?.error?.message) {
          errorDetail = errJson.error.message;
        } else if (typeof errJson?.detail === "string") {
          errorDetail = errJson.detail;
        }
      } catch {
        // mantém mensagem padrão
      }
      reject(new Error(errorDetail));
    };

    xhr.onerror = () => {
      reject(new Error("Erro de conexão ao enviar o documento PDF para o servidor."));
    };

    xhr.send(formData);
  });
}

/**
 * Busca o estado consolidado das páginas já processadas e suas ocorrências.
 */
export async function fetchDocumentOccurrences(
  jobId: string
): Promise<DocumentOccurrencesResponse> {
  const response = await fetch(getApiUrl(`/api/v1/documents/${jobId}/occurrences`), {
    method: "GET",
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Não foi possível consultar as ocorrências do job ${jobId}.`);
  }

  return (await response.json()) as DocumentOccurrencesResponse;
}

/**
 * Solicita a aplicação do tarjamento/mascaramento físico definitivo, sanitização estrutural e geração do Relatório Zero-PII.
 */
export async function applyDocumentRedaction(
  jobId: string,
  payload: RedactionRequest
): Promise<RedactionExecutionResponse> {
  const response = await fetch(getApiUrl(`/api/v1/documents/${jobId}/redact`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
    cache: "no-store",
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let errorDetail = `Falha ao aplicar o tarjamento definitivo (HTTP ${response.status}).`;
    try {
      const errJson = await response.json();
      if (errJson?.error?.message) {
        errorDetail = errJson.error.message;
      } else if (typeof errJson?.detail === "string") {
        errorDetail = errJson.detail;
      }
    } catch {
      // mantém mensagem padrão
    }
    throw new Error(errorDetail);
  }

  return (await response.json()) as RedactionExecutionResponse;
}

export function getPageWebpUrl(jobId: string, pageNumber: number): string {
  return getApiUrl(`/api/v1/documents/${jobId}/pages/${pageNumber}/image`);
}

export function getRedactedPdfDownloadUrl(jobId: string, rev?: number): string {
  const base = getApiUrl(`/api/v1/documents/${jobId}/download/pdf`);
  return rev !== undefined ? appendRevisionParam(base, rev) : base;
}

export function getReportPdfDownloadUrl(jobId: string, rev?: number): string {
  const base = getApiUrl(`/api/v1/documents/${jobId}/download/report`);
  return rev !== undefined ? appendRevisionParam(base, rev) : base;
}

export interface UseDocumentStreamState {
  status: JobStatus;
  documentProfile: DocumentProfile;
  isReadyForReview: boolean;
  isAnalysisComplete: boolean;
  isRedactionComplete: boolean;
  processedPages: number;
  totalPages: number;
  progressPercentage: number;
  pagesByNumber: Record<number, PageData>;
  occurrences: DetectedOccurrence[];
  verboseLogs: VerboseStepLog[];
  originalSha256: string | null;
  redactedSha256: string | null;
  auditLog: SecurityValidationResult | null;
  downloadPdfUrl: string | null;
  downloadReportUrl: string | null;
  streamError: string | null;
  refreshOccurrences: () => Promise<void>;
  resetRedactionCompletion: () => void;
  applyRedactionResponse: (res: RedactionExecutionResponse) => void;
}

function mergeVerboseLogs(
  existing: VerboseStepLog[],
  incoming: VerboseStepLog[]
): VerboseStepLog[] {
  const byId = new Map<string, VerboseStepLog>();
  for (const item of existing) {
    byId.set(item.id, item);
  }
  for (const item of incoming) {
    byId.set(item.id, item);
  }
  return Array.from(byId.values()).sort((a, b) => a.sequence - b.sequence);
}

/**
 * Hook customizado `useDocumentStream(jobId)` utilizando a API nativa `EventSource`
 * conectada a `/api/v1/documents/{jobId}/stream`.
 */
export function useDocumentStream(jobId: string | null): UseDocumentStreamState {
  const [status, setStatus] = useState<JobStatus>("RECEIVED");
  const [documentProfile, setDocumentProfile] = useState<DocumentProfile>("RESTRICTED_PROCESS");
  const [isReadyForReview, setIsReadyForReview] = useState<boolean>(false);
  const [isAnalysisComplete, setIsAnalysisComplete] = useState<boolean>(false);
  const [isRedactionComplete, setIsRedactionComplete] = useState<boolean>(false);
  const [processedPages, setProcessedPages] = useState<number>(0);
  const [totalPages, setTotalPages] = useState<number>(0);
  const [progressPercentage, setProgressPercentage] = useState<number>(0);
  const [pagesByNumber, setPagesByNumber] = useState<Record<number, PageData>>({});
  const [occurrences, setOccurrences] = useState<DetectedOccurrence[]>([]);
  const [verboseLogs, setVerboseLogs] = useState<VerboseStepLog[]>([]);
  const [originalSha256, setOriginalSha256] = useState<string | null>(null);
  const [redactedSha256, setRedactedSha256] = useState<string | null>(null);
  const [auditLog, setAuditLog] = useState<SecurityValidationResult | null>(null);
  const [downloadPdfUrl, setDownloadPdfUrl] = useState<string | null>(null);
  const [downloadReportUrl, setDownloadReportUrl] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);

  const syncFullSnapshot = useCallback(async (targetJobId: string) => {
    try {
      const snapshot = await fetchDocumentOccurrences(targetJobId);
      setStatus(snapshot.status);
      if (snapshot.document_profile) {
        setDocumentProfile(snapshot.document_profile);
      }
      setTotalPages(snapshot.total_pages);
      setProcessedPages(snapshot.processed_pages);
      setOriginalSha256(snapshot.original_file_sha256);
      if (snapshot.redacted_file_sha256) {
        setRedactedSha256(snapshot.redacted_file_sha256);
      }
      if (snapshot.audit_log) {
        setAuditLog(snapshot.audit_log);
      }
      if (snapshot.verbose_logs && snapshot.verbose_logs.length > 0) {
        setVerboseLogs((prev) => mergeVerboseLogs(prev, snapshot.verbose_logs || []));
      }

      const mappedPages: Record<number, PageData> = {};
      for (const [k, pageVal] of Object.entries(snapshot.pages || {})) {
        const pNum = Number(k);
        mappedPages[pNum] = {
          ...pageVal,
          image_url: getApiUrl(pageVal.image_url),
        };
      }
      setPagesByNumber(mappedPages);
      setOccurrences(snapshot.occurrences || []);

      if (snapshot.total_pages > 0) {
        setProgressPercentage(
          Math.round((snapshot.processed_pages / snapshot.total_pages) * 100)
        );
      }

      if (
        mappedPages[1] !== undefined ||
        snapshot.status === "PARTIAL_REVIEW_READY" ||
        snapshot.status === "AWAITING_REVIEW" ||
        snapshot.status === "COMPLETED"
      ) {
        setIsReadyForReview(true);
      }

      if (snapshot.status === "AWAITING_REVIEW" || snapshot.status === "COMPLETED") {
        setIsAnalysisComplete(true);
      }

      if (snapshot.status === "COMPLETED") {
        const rev = Date.now();
        setIsRedactionComplete(true);
        setDownloadPdfUrl(getRedactedPdfDownloadUrl(targetJobId, rev));
        setDownloadReportUrl(getReportPdfDownloadUrl(targetJobId, rev));
      } else {
        setIsRedactionComplete(false);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Erro ao sincronizar estado do documento.";
      setStreamError(message);
    }
  }, []);

  const refreshOccurrences = useCallback(async () => {
    if (!jobId) return;
    await syncFullSnapshot(jobId);
  }, [jobId, syncFullSnapshot]);

  const resetRedactionCompletion = useCallback(() => {
    setIsRedactionComplete(false);
    setDownloadPdfUrl(null);
    setDownloadReportUrl(null);
  }, []);

  const applyRedactionResponse = useCallback((res: RedactionExecutionResponse) => {
    const rev = Date.now();
    setStatus(res.status);
    if (res.document_profile) {
      setDocumentProfile(res.document_profile);
    }
    setOriginalSha256(res.original_file_sha256);
    setRedactedSha256(res.redacted_file_sha256);
    setAuditLog(res.audit_log);
    if (res.verbose_logs && res.verbose_logs.length > 0) {
      setVerboseLogs((prev) => mergeVerboseLogs(prev, res.verbose_logs || []));
    }
    setDownloadPdfUrl(appendRevisionParam(getApiUrl(res.download_pdf_url), rev));
    setDownloadReportUrl(appendRevisionParam(getApiUrl(res.download_report_url), rev));
    setIsRedactionComplete(true);
  }, []);

  useEffect(() => {
    if (!jobId) {
      setStatus("RECEIVED");
      setDocumentProfile("RESTRICTED_PROCESS");
      setIsReadyForReview(false);
      setIsAnalysisComplete(false);
      setIsRedactionComplete(false);
      setProcessedPages(0);
      setTotalPages(0);
      setProgressPercentage(0);
      setPagesByNumber({});
      setOccurrences([]);
      setVerboseLogs([]);
      setOriginalSha256(null);
      setRedactedSha256(null);
      setAuditLog(null);
      setDownloadPdfUrl(null);
      setDownloadReportUrl(null);
      setStreamError(null);
      return;
    }

    // 1. Hidrata imediatamente o estado atual caso a Página 1 já tenha terminado no upload
    void syncFullSnapshot(jobId);

    // 2. Abre a conexão Server-Sent Events (EventSource) nativa do navegador
    const streamUrl = getApiUrl(`/api/v1/documents/${jobId}/stream`);
    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    es.addEventListener("verbose_step", (evt: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(evt.data) as SseEventEnvelope<VerboseStepLog>;
        if (envelope?.data) {
          setVerboseLogs((prev) => mergeVerboseLogs(prev, [envelope.data]));
        }
      } catch {
        // Ignora evento malformado isolado
      }
    });

    es.addEventListener("page_ready", (evt: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(evt.data) as SseEventEnvelope<PageReadyEventPayload>;
        const payload = envelope.data;
        const pageNum = payload.page_number;

        setStatus(payload.status);
        if (payload.document_profile) {
          setDocumentProfile(payload.document_profile);
        }
        setTotalPages(payload.total_pages);

        setPagesByNumber((prev) => {
          const existing = prev[pageNum];
          const updatedPage: PageData = {
            page_number: pageNum,
            page_type: existing?.page_type || "TEXTUAL",
            width_px: existing?.width_px || 1240,
            height_px: existing?.height_px || 1754,
            image_url: getApiUrl(payload.image_url),
            ocr_executed: existing?.ocr_executed || false,
            processing_time_ms: existing?.processing_time_ms || 0,
            occurrences: payload.occurrences || [],
          };
          const nextMap = { ...prev, [pageNum]: updatedPage };
          const count = Object.keys(nextMap).length;
          setProcessedPages(count);
          if (payload.total_pages > 0) {
            setProgressPercentage(Math.round((count / payload.total_pages) * 100));
          }
          return nextMap;
        });

        setOccurrences((prevOccs) => {
          const filtered = prevOccs.filter((o) => o.page_number !== pageNum);
          return [...filtered, ...(payload.occurrences || [])].sort(
            (a, b) => a.page_number - b.page_number
          );
        });

        if (pageNum === 1) {
          setIsReadyForReview(true);
        }
      } catch {
        // Ignora evento malformado isolado
      }
    });

    es.addEventListener("progress", (evt: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(evt.data) as SseEventEnvelope<ProgressEventPayload>;
        const payload = envelope.data;
        setProcessedPages(payload.processed_pages);
        setTotalPages(payload.total_pages);
        setProgressPercentage(payload.percentage);
        setStatus(payload.status);
      } catch {
        // Ignora erro de parse
      }
    });

    es.addEventListener("analysis_complete", (evt: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(evt.data) as SseEventEnvelope<AnalysisCompleteEventPayload>;
        setStatus(envelope.data.status);
        if (envelope.data.document_profile) {
          setDocumentProfile(envelope.data.document_profile);
        }
        setTotalPages(envelope.data.total_pages);
        setProcessedPages(envelope.data.total_pages);
        setProgressPercentage(100);
        setIsReadyForReview(true);
        setIsAnalysisComplete(true);
        void syncFullSnapshot(jobId);
      } catch {
        // Ignora erro de parse
      }
    });

    es.addEventListener("redaction_complete", (evt: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(evt.data) as SseEventEnvelope<RedactionCompleteEventPayload>;
        const rev = Date.now();
        setStatus(envelope.data.status);
        setRedactedSha256(envelope.data.redacted_sha256);
        setDownloadPdfUrl(appendRevisionParam(getApiUrl(envelope.data.download_pdf_url), rev));
        setDownloadReportUrl(appendRevisionParam(getApiUrl(envelope.data.download_report_url), rev));
        setIsRedactionComplete(true);
        void syncFullSnapshot(jobId);
        es.close();
      } catch {
        // Ignora erro de parse
      }
    });

    es.addEventListener("error", (evt: MessageEvent<string>) => {
      if (evt.data) {
        try {
          const envelope = JSON.parse(evt.data) as SseEventEnvelope<{ message?: string }>;
          if (envelope?.data?.message) {
            setStreamError(envelope.data.message);
            setStatus("FAILED");
          }
        } catch {
          // Evento nativo de fechamento do EventSource após término do stream
        }
      }
      es.close();
    });

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [jobId, syncFullSnapshot]);

  return {
    status,
    documentProfile,
    isReadyForReview,
    isAnalysisComplete,
    isRedactionComplete,
    processedPages,
    totalPages,
    progressPercentage,
    pagesByNumber,
    occurrences,
    verboseLogs,
    originalSha256,
    redactedSha256,
    auditLog,
    downloadPdfUrl,
    downloadReportUrl,
    streamError,
    refreshOccurrences,
    resetRedactionCompletion,
    applyRedactionResponse,
  };
}
