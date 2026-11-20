"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  appendRevisionParam,
  applyDocumentRedaction,
  derivePipelineProgress,
  fetchDocumentOccurrences,
  getApiUrl,
  getPageWebpUrl,
  getRedactedPdfDownloadUrl,
  getReportPdfDownloadUrl,
  PIPELINE_STAGE_LABELS,
  uploadPdfDocumentWithProgress,
} from "@/services/api";
import type {
  AnalysisCompleteEventPayload,
  CustomRegexRule,
  DetectedOccurrence,
  DocumentOccurrencesResponse,
  DocumentQueueItemState,
  JobStatus,
  ManualRedactionBox,
  PageData,
  PageReadyEventPayload,
  PiiCategory,
  ProgressEventPayload,
  RedactionActionMode,
  RedactionCompleteEventPayload,
  RedactionItem,
  SseEventEnvelope,
  VerboseStepLog,
} from "@/types/document";

const MAX_CONCURRENT_UPLOADS = 2;
const SSE_RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000] as const;
const SNAPSHOT_SAFETY_POLL_INTERVAL_MS = 2500;

function isAnalysisTerminalStatus(status: JobStatus): boolean {
  return (
    status === "AWAITING_REVIEW" ||
    status === "COMPLETED" ||
    status === "FAILED"
  );
}

function preserveMonotonicProgress(
  prev: DocumentQueueItemState,
  nextStageNumber: DocumentQueueItemState["stageNumber"],
  nextProgressPercent: number | null
): number | null {
  if (nextProgressPercent === null) return null;
  const inSameAnalysisPhase =
    prev.stageNumber >= 3 &&
    prev.stageNumber <= 4 &&
    nextStageNumber >= 3 &&
    nextStageNumber <= 4 &&
    prev.jobStatus !== "REDACTING" &&
    prev.jobStatus !== "VALIDATING";
  if (inSameAnalysisPhase && prev.progressPercent !== null) {
    return Math.max(prev.progressPercent, nextProgressPercent);
  }
  return nextProgressPercent;
}

function mergeVerboseLogs(
  existing: VerboseStepLog[],
  incoming: VerboseStepLog[]
): VerboseStepLog[] {
  if (!incoming.length) return existing;
  const byId = new Map<string, VerboseStepLog>();
  for (const item of existing) {
    byId.set(item.id, item);
  }
  for (const item of incoming) {
    byId.set(item.id, item);
  }
  return Array.from(byId.values()).sort((a, b) => a.sequence - b.sequence);
}

function createQueuedDocument(
  file: File,
  customRegexes?: CustomRegexRule[],
  ignoreSeiStandardBlocks: boolean = true
): DocumentQueueItemState {
  const clientId = `doc_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  return {
    clientId,
    file,
    fileName: file.name,
    fileSizeBytes: file.size,
    badgeStatus: "Aguardando",
    stageNumber: 1,
    stageLabel: PIPELINE_STAGE_LABELS[1],
    progressPercent: 0,
    jobId: null,
    jobStatus: "RECEIVED",
    documentProfile: "RESTRICTED_PROCESS",
    processedPages: 0,
    totalPages: 0,
    isReadyForReview: false,
    isAnalysisComplete: false,
    isRedactionComplete: false,
    pagesByNumber: {},
    occurrences: [],
    originalSha256: null,
    redactedSha256: null,
    auditLog: null,
    downloadPdfUrl: null,
    downloadReportUrl: null,
    errorMessage: null,
    customRegexes:
      customRegexes && customRegexes.length > 0 ? customRegexes : undefined,
    ignoreSeiStandardBlocks,
    currentPage: 1,
    deselectedIds: [],
    actionModesById: {},
    manualBoxes: [],
  };
}

function markDocNeedsReapplication(
  prev: DocumentQueueItemState
): Partial<DocumentQueueItemState> {
  if (!prev.isRedactionComplete) return {};
  return {
    isRedactionComplete: false,
    downloadPdfUrl: null,
    downloadReportUrl: null,
    badgeStatus: "Processando",
    jobStatus: "AWAITING_REVIEW",
    stageNumber: 4,
    stageLabel: PIPELINE_STAGE_LABELS[4],
    progressPercent: 100,
  };
}

export function useDocumentQueue() {
  const [documents, setDocuments] = useState<DocumentQueueItemState[]>([]);
  const [activeDocId, setActiveDocId] = useState<string | null>(null);
  const [showAddFilesDropzone, setShowAddFilesDropzone] =
    useState<boolean>(false);
  const [customRegexRules, setCustomRegexRules] = useState<CustomRegexRule[]>(
    []
  );
  const [ignoreSeiStandardBlocks, setIgnoreSeiStandardBlocks] =
    useState<boolean>(true);
  const [verboseLogsByClient, setVerboseLogsByClient] = useState<
    Record<string, VerboseStepLog[]>
  >({});

  // Estado auxiliar do Workspace Split-Screen
  const [syncGroupByDefault, setSyncGroupByDefault] = useState<boolean>(true);
  const [activeOccurrenceId, setActiveOccurrenceId] = useState<string | null>(
    null
  );
  const [isManualDrawMode, setIsManualDrawMode] = useState<boolean>(false);
  const [manualDrawCategory, setManualDrawCategory] =
    useState<PiiCategory>("PERSONAL_COMMON");

  // Estado do Modal Legal, Validação e Exportação
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false);
  const [isApplyingRedaction, setIsApplyingRedaction] =
    useState<boolean>(false);
  const [redactionError, setRedactionError] = useState<string | null>(null);

  const inFlightUploadsRef = useRef<Set<string>>(new Set());
  const eventSourcesRef = useRef<Map<string, EventSource>>(new Map());
  const reconnectTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map()
  );
  const reconnectAttemptsRef = useRef<Map<string, number>>(new Map());
  const documentsRef = useRef<DocumentQueueItemState[]>([]);
  const pollTickCountRef = useRef<number>(0);
  const openStreamRef = useRef<(clientId: string, jobId: string) => void>(
    () => {}
  );
  const latestStageByClientRef = useRef<Map<string, string>>(new Map());
  const mountedRef = useRef<boolean>(true);

  documentsRef.current = documents;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      for (const timer of reconnectTimersRef.current.values()) {
        clearTimeout(timer);
      }
      reconnectTimersRef.current.clear();
      reconnectAttemptsRef.current.clear();
      for (const es of eventSourcesRef.current.values()) {
        es.close();
      }
      eventSourcesRef.current.clear();
    };
  }, []);

  const appendClientVerboseLogs = useCallback(
    (clientId: string, incoming?: VerboseStepLog[] | null) => {
      if (!mountedRef.current || !incoming || incoming.length === 0) return;
      setVerboseLogsByClient((prev) => ({
        ...prev,
        [clientId]: mergeVerboseLogs(prev[clientId] || [], incoming),
      }));
    },
    []
  );

  const updateDocument = useCallback(
    (
      clientId: string,
      updater: (doc: DocumentQueueItemState) => DocumentQueueItemState
    ) => {
      if (!mountedRef.current) return;
      setDocuments((prev) =>
        prev.map((item) => (item.clientId === clientId ? updater(item) : item))
      );
    },
    []
  );

  const clearReconnectForClient = useCallback((clientId: string) => {
    const timer = reconnectTimersRef.current.get(clientId);
    if (timer) {
      clearTimeout(timer);
      reconnectTimersRef.current.delete(clientId);
    }
    reconnectAttemptsRef.current.delete(clientId);
  }, []);

  const closeStreamForClient = useCallback(
    (clientId: string, options?: { clearReconnect?: boolean }) => {
      if (options?.clearReconnect !== false) {
        clearReconnectForClient(clientId);
      }
      const existing = eventSourcesRef.current.get(clientId);
      if (existing) {
        existing.close();
        eventSourcesRef.current.delete(clientId);
      }
    },
    [clearReconnectForClient]
  );

  const syncDocumentSnapshot = useCallback(
    async (
      clientId: string,
      targetJobId: string,
      options?: { silent?: boolean }
    ): Promise<DocumentOccurrencesResponse | null> => {
      try {
        const snapshot = await fetchDocumentOccurrences(targetJobId);
        const logs = snapshot.verbose_logs || [];
        if (logs.length > 0) {
          latestStageByClientRef.current.set(
            clientId,
            logs[logs.length - 1].stage
          );
          appendClientVerboseLogs(clientId, logs);
        }
        const latestStage = latestStageByClientRef.current.get(clientId) || null;

        updateDocument(clientId, (prev) => {
          const mappedPages: Record<number, PageData> = {
            ...prev.pagesByNumber,
          };
          for (const [k, pageVal] of Object.entries(snapshot.pages || {})) {
            const pNum = Number(k);
            mappedPages[pNum] = {
              ...pageVal,
              image_url: getApiUrl(pageVal.image_url),
            };
          }

          const isReady =
            prev.isReadyForReview ||
            mappedPages[1] !== undefined ||
            snapshot.status === "PARTIAL_REVIEW_READY" ||
            snapshot.status === "AWAITING_REVIEW" ||
            snapshot.status === "COMPLETED";

          const isAnalysisDone =
            snapshot.status === "AWAITING_REVIEW" ||
            snapshot.status === "COMPLETED";

          const isRedacted = snapshot.status === "COMPLETED";
          const rev = Date.now();

          if (snapshot.status === "FAILED") {
            closeStreamForClient(clientId);
            return {
              ...prev,
              badgeStatus: "Erro",
              jobStatus: "FAILED",
              stageLabel: "Erro durante o processamento",
              progressPercent: null,
              errorMessage:
                prev.errorMessage ||
                "Falha técnica durante o processamento deste PDF.",
            };
          }

          const nextTotalPages = Math.max(
            prev.totalPages,
            snapshot.total_pages || 0
          );
          const nextProcessedPages = Math.max(
            prev.processedPages,
            snapshot.processed_pages || 0,
            Object.keys(mappedPages).length
          );
          const clampedProcessedPages =
            nextTotalPages > 0
              ? Math.min(nextTotalPages, nextProcessedPages)
              : nextProcessedPages;

          const derived = derivePipelineProgress({
            jobStatus: snapshot.status,
            latestBackendStage: latestStage,
            processedPages: clampedProcessedPages,
            totalPages: nextTotalPages,
          });
          const monotonicPercent = preserveMonotonicProgress(
            prev,
            derived.stageNumber,
            derived.progressPercent
          );

          return {
            ...prev,
            badgeStatus: derived.badgeStatus,
            stageNumber: derived.stageNumber,
            stageLabel: derived.stageLabel,
            progressPercent: monotonicPercent,
            jobStatus: snapshot.status,
            documentProfile: snapshot.document_profile || prev.documentProfile,
            totalPages: nextTotalPages,
            processedPages: clampedProcessedPages,
            isReadyForReview: isReady,
            isAnalysisComplete: isAnalysisDone,
            isRedactionComplete: isRedacted,
            pagesByNumber: mappedPages,
            occurrences:
              snapshot.occurrences &&
              snapshot.occurrences.length >= prev.occurrences.length
                ? snapshot.occurrences
                : isAnalysisDone || isRedacted
                  ? snapshot.occurrences || []
                  : prev.occurrences,
            originalSha256: snapshot.original_file_sha256,
            redactedSha256:
              snapshot.redacted_file_sha256 || prev.redactedSha256,
            auditLog: snapshot.audit_log || prev.auditLog,
            downloadPdfUrl: isRedacted
              ? prev.downloadPdfUrl || getRedactedPdfDownloadUrl(targetJobId, rev)
              : null,
            downloadReportUrl: isRedacted
              ? prev.downloadReportUrl ||
                getReportPdfDownloadUrl(targetJobId, rev)
              : null,
          };
        });

        if (snapshot.status === "COMPLETED" || snapshot.status === "FAILED") {
          closeStreamForClient(clientId);
        }
        return snapshot;
      } catch (err) {
        if (!options?.silent) {
          const msg =
            err instanceof Error
              ? err.message
              : "Erro ao sincronizar estado do documento.";
          updateDocument(clientId, (prev) => ({
            ...prev,
            errorMessage: prev.errorMessage || msg,
          }));
        }
        return null;
      }
    },
    [appendClientVerboseLogs, closeStreamForClient, updateDocument]
  );

  const openStreamForDocument = useCallback(
    (clientId: string, jobId: string) => {
      if (eventSourcesRef.current.has(clientId)) {
        return;
      }

      const streamUrl = getApiUrl(`/api/v1/documents/${jobId}/stream`);
      const es = new EventSource(streamUrl);
      eventSourcesRef.current.set(clientId, es);

      es.addEventListener("open", () => {
        reconnectAttemptsRef.current.set(clientId, 0);
      });

      es.addEventListener("verbose_step", (evt: MessageEvent<string>) => {
        reconnectAttemptsRef.current.set(clientId, 0);
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<VerboseStepLog>;
          const logEntry = envelope?.data;
          if (!logEntry) return;
          appendClientVerboseLogs(clientId, [logEntry]);
          const backendStage = logEntry.stage;
          if (!backendStage) return;
          latestStageByClientRef.current.set(clientId, backendStage);

          updateDocument(clientId, (prev) => {
            if (prev.badgeStatus === "Erro" || prev.badgeStatus === "Concluído") {
              return prev;
            }
            const derived = derivePipelineProgress({
              jobStatus: prev.jobStatus,
              latestBackendStage: backendStage,
              processedPages: prev.processedPages,
              totalPages: prev.totalPages,
            });
            return {
              ...prev,
              badgeStatus: derived.badgeStatus,
              stageNumber: derived.stageNumber,
              stageLabel: derived.stageLabel,
              progressPercent: preserveMonotonicProgress(
                prev,
                derived.stageNumber,
                derived.progressPercent
              ),
            };
          });
        } catch {
          // Ignora evento malformado isolado
        }
      });

      es.addEventListener("page_ready", (evt: MessageEvent<string>) => {
        reconnectAttemptsRef.current.set(clientId, 0);
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<PageReadyEventPayload>;
          const payload = envelope.data;
          const pageNum = payload.page_number;

          updateDocument(clientId, (prev) => {
            const existing = prev.pagesByNumber[pageNum];
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
            const nextPages = { ...prev.pagesByNumber, [pageNum]: updatedPage };
            const nextTotal = Math.max(prev.totalPages, payload.total_pages || 0);
            const count = Math.min(
              nextTotal > 0 ? nextTotal : Number.MAX_SAFE_INTEGER,
              Math.max(prev.processedPages, Object.keys(nextPages).length)
            );

            const filteredOccs = prev.occurrences.filter(
              (o) => o.page_number !== pageNum
            );
            const nextOccs = [
              ...filteredOccs,
              ...(payload.occurrences || []),
            ].sort((a, b) => a.page_number - b.page_number);

            const derived = derivePipelineProgress({
              jobStatus: payload.status,
              latestBackendStage:
                latestStageByClientRef.current.get(clientId) ||
                "ETAPA_3_DETECCAO_LGPD",
              processedPages: count,
              totalPages: nextTotal,
            });

            return {
              ...prev,
              jobStatus: payload.status,
              documentProfile:
                payload.document_profile || prev.documentProfile,
              totalPages: nextTotal,
              processedPages: count,
              pagesByNumber: nextPages,
              occurrences: nextOccs,
              isReadyForReview: prev.isReadyForReview || pageNum === 1,
              badgeStatus: derived.badgeStatus,
              stageNumber: derived.stageNumber,
              stageLabel: derived.stageLabel,
              progressPercent: preserveMonotonicProgress(
                prev,
                derived.stageNumber,
                derived.progressPercent
              ),
            };
          });
        } catch {
          // Ignora evento malformado isolado
        }
      });

      es.addEventListener("progress", (evt: MessageEvent<string>) => {
        reconnectAttemptsRef.current.set(clientId, 0);
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<ProgressEventPayload>;
          const payload = envelope.data;
          updateDocument(clientId, (prev) => {
            const nextTotal = Math.max(prev.totalPages, payload.total_pages || 0);
            const nextProcessed = Math.min(
              nextTotal > 0 ? nextTotal : Number.MAX_SAFE_INTEGER,
              Math.max(prev.processedPages, payload.processed_pages || 0)
            );
            const derived = derivePipelineProgress({
              jobStatus: payload.status,
              latestBackendStage: latestStageByClientRef.current.get(clientId),
              processedPages: nextProcessed,
              totalPages: nextTotal,
            });
            const rawPercent =
              derived.progressPercent ?? Math.round(payload.percentage);
            return {
              ...prev,
              jobStatus: payload.status,
              processedPages: nextProcessed,
              totalPages: nextTotal,
              badgeStatus: derived.badgeStatus,
              stageNumber: derived.stageNumber,
              stageLabel: derived.stageLabel,
              progressPercent: preserveMonotonicProgress(
                prev,
                derived.stageNumber,
                rawPercent
              ),
            };
          });
        } catch {
          // Ignora erro de parse
        }
      });

      es.addEventListener("analysis_complete", (evt: MessageEvent<string>) => {
        reconnectAttemptsRef.current.set(clientId, 0);
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<AnalysisCompleteEventPayload>;
          latestStageByClientRef.current.set(
            clientId,
            "ETAPA_4_AGUARDANDO_REVISAO"
          );
          updateDocument(clientId, (prev) => {
            const nextTotal = Math.max(
              prev.totalPages,
              envelope.data.total_pages || 0
            );
            const derived = derivePipelineProgress({
              jobStatus: envelope.data.status,
              latestBackendStage: "ETAPA_4_AGUARDANDO_REVISAO",
              processedPages: nextTotal,
              totalPages: nextTotal,
            });
            return {
              ...prev,
              jobStatus: envelope.data.status,
              documentProfile:
                envelope.data.document_profile || prev.documentProfile,
              totalPages: nextTotal,
              processedPages: nextTotal,
              isReadyForReview: true,
              isAnalysisComplete: true,
              badgeStatus: derived.badgeStatus,
              stageNumber: derived.stageNumber,
              stageLabel: derived.stageLabel,
              progressPercent: derived.progressPercent,
            };
          });
          void syncDocumentSnapshot(clientId, jobId);
        } catch {
          // Ignora erro de parse
        }
      });

      es.addEventListener("redaction_complete", (evt: MessageEvent<string>) => {
        reconnectAttemptsRef.current.set(clientId, 0);
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<RedactionCompleteEventPayload>;
          const rev = Date.now();
          updateDocument(clientId, (prev) => ({
            ...prev,
            jobStatus: envelope.data.status,
            redactedSha256: envelope.data.redacted_sha256,
            downloadPdfUrl: appendRevisionParam(
              getApiUrl(envelope.data.download_pdf_url),
              rev
            ),
            downloadReportUrl: appendRevisionParam(
              getApiUrl(envelope.data.download_report_url),
              rev
            ),
            isRedactionComplete: true,
            badgeStatus: "Concluído",
            stageNumber: 6,
            stageLabel: PIPELINE_STAGE_LABELS[6],
            progressPercent: 100,
          }));
          void syncDocumentSnapshot(clientId, jobId);
          closeStreamForClient(clientId);
        } catch {
          // Ignora erro de parse
        }
      });

      es.addEventListener("error", (evt: MessageEvent<string>) => {
        if (evt.data) {
          try {
            const envelope = JSON.parse(evt.data) as SseEventEnvelope<{
              message?: string;
            }>;
            if (envelope?.data?.message) {
              updateDocument(clientId, (prev) => ({
                ...prev,
                badgeStatus: "Erro",
                jobStatus: "FAILED",
                stageLabel: "Erro no processamento",
                progressPercent: null,
                errorMessage: envelope.data.message || "Falha no processamento.",
              }));
              closeStreamForClient(clientId);
              return;
            }
          } catch {
            // Evento nativo de fechamento do EventSource após término do stream
          }
        }

        // Queda de conexão ou EOF do stream SSE (ex.: timeout de rede/proxy):
        // fecha a instância atual, ressincroniza via snapshot REST e reconecta com backoff se ainda não concluiu.
        closeStreamForClient(clientId, { clearReconnect: false });

        void (async () => {
          if (!mountedRef.current) return;
          const snapshot = await syncDocumentSnapshot(clientId, jobId, {
            silent: true,
          });
          if (!mountedRef.current) return;

          const latestDoc = documentsRef.current.find(
            (d) => d.clientId === clientId && d.jobId === jobId
          );
          if (!latestDoc) {
            clearReconnectForClient(clientId);
            return;
          }

          const resolvedStatus = snapshot?.status || latestDoc.jobStatus;
          const stillHasPendingPages =
            (snapshot?.total_pages ?? latestDoc.totalPages) > 0 &&
            (snapshot?.processed_pages ?? latestDoc.processedPages) <
              (snapshot?.total_pages ?? latestDoc.totalPages);

          if (
            latestDoc.badgeStatus === "Erro" ||
            latestDoc.badgeStatus === "Concluído" ||
            (isAnalysisTerminalStatus(resolvedStatus) && !stillHasPendingPages)
          ) {
            clearReconnectForClient(clientId);
            return;
          }

          const attempt = reconnectAttemptsRef.current.get(clientId) || 0;
          const delayMs =
            SSE_RECONNECT_BACKOFF_MS[
              Math.min(attempt, SSE_RECONNECT_BACKOFF_MS.length - 1)
            ];
          reconnectAttemptsRef.current.set(clientId, attempt + 1);

          const prevTimer = reconnectTimersRef.current.get(clientId);
          if (prevTimer) clearTimeout(prevTimer);

          const timer = setTimeout(() => {
            reconnectTimersRef.current.delete(clientId);
            if (!mountedRef.current) return;
            const docNow = documentsRef.current.find(
              (d) => d.clientId === clientId && d.jobId === jobId
            );
            if (
              !docNow ||
              docNow.badgeStatus === "Erro" ||
              docNow.badgeStatus === "Concluído" ||
              (isAnalysisTerminalStatus(docNow.jobStatus) &&
                !(
                  docNow.totalPages > 0 &&
                  docNow.processedPages < docNow.totalPages
                ))
            ) {
              return;
            }
            openStreamRef.current(clientId, jobId);
          }, delayMs);

          reconnectTimersRef.current.set(clientId, timer);
        })();
      });
    },
    [
      appendClientVerboseLogs,
      clearReconnectForClient,
      closeStreamForClient,
      syncDocumentSnapshot,
      updateDocument,
    ]
  );

  openStreamRef.current = openStreamForDocument;

  // Rede de segurança (polling leve de snapshot enquanto houver documento ativo em análise):
  // garante que a interface nunca fique congelada caso o SSE caia silenciosamente em proxies ou workers longos.
  const hasActiveStreamingDocs = useMemo(
    () =>
      documents.some(
        (d) =>
          Boolean(d.jobId) &&
          d.badgeStatus !== "Erro" &&
          d.badgeStatus !== "Concluído" &&
          (!isAnalysisTerminalStatus(d.jobStatus) ||
            (d.totalPages > 0 && d.processedPages < d.totalPages))
      ),
    [documents]
  );

  useEffect(() => {
    if (!hasActiveStreamingDocs) {
      pollTickCountRef.current = 0;
      return;
    }

    const intervalId = setInterval(() => {
      if (!mountedRef.current) return;
      pollTickCountRef.current += 1;
      const isHeartbeatTick = pollTickCountRef.current % 3 === 0;

      for (const doc of documentsRef.current) {
        if (
          !doc.jobId ||
          doc.badgeStatus === "Erro" ||
          doc.badgeStatus === "Concluído"
        ) {
          continue;
        }
        const needsSync =
          !isAnalysisTerminalStatus(doc.jobStatus) ||
          (doc.totalPages > 0 && doc.processedPages < doc.totalPages);
        if (!needsSync) continue;

        const es = eventSourcesRef.current.get(doc.clientId);
        const streamClosedOrMissing = !es || es.readyState !== EventSource.OPEN;
        if (streamClosedOrMissing || isHeartbeatTick) {
          void syncDocumentSnapshot(doc.clientId, doc.jobId, { silent: true });
        }
      }
    }, SNAPSHOT_SAFETY_POLL_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [hasActiveStreamingDocs, syncDocumentSnapshot]);

  // Seleciona automaticamente o primeiro documento que ficar pronto para revisão
  useEffect(() => {
    if (activeDocId && documents.some((d) => d.clientId === activeDocId)) {
      return;
    }
    const firstReady = documents.find(
      (d) => d.isReadyForReview && d.badgeStatus !== "Erro"
    );
    if (firstReady) {
      setActiveDocId(firstReady.clientId);
    } else if (activeDocId !== null) {
      setActiveDocId(null);
    }
  }, [activeDocId, documents]);

  // Fila controlada: envia até MAX_CONCURRENT_UPLOADS PDFs em paralelo sem travar PCs legados
  const startUploadForItem = useCallback(
    async (item: DocumentQueueItemState) => {
      const { clientId, file } = item;
      inFlightUploadsRef.current.add(clientId);

      updateDocument(clientId, (prev) => ({
        ...prev,
        badgeStatus: "Carregando",
        stageNumber: 1,
        stageLabel: PIPELINE_STAGE_LABELS[1],
        progressPercent: 0,
        errorMessage: null,
      }));

      try {
        const res = await uploadPdfDocumentWithProgress(
          file,
          true,
          {
            onUploadProgress: (pct) => {
              updateDocument(clientId, (prev) =>
                prev.badgeStatus === "Carregando" && prev.stageNumber === 1
                  ? { ...prev, progressPercent: pct }
                  : prev
              );
            },
            onUploadBodySent: () => {
              updateDocument(clientId, (prev) => ({
                ...prev,
                badgeStatus: "Processando",
                stageNumber: 2,
                stageLabel: PIPELINE_STAGE_LABELS[2],
                progressPercent: null,
              }));
            },
          },
          item.customRegexes,
          item.ignoreSeiStandardBlocks ?? true
        );

        const logs = res.verbose_logs || [];
        if (logs.length > 0) {
          latestStageByClientRef.current.set(
            clientId,
            logs[logs.length - 1].stage
          );
          appendClientVerboseLogs(clientId, logs);
        }
        const latestStage = latestStageByClientRef.current.get(clientId) || null;
        const derived = derivePipelineProgress({
          jobStatus: res.status,
          latestBackendStage: latestStage,
          processedPages: res.processed_pages,
          totalPages: res.total_pages,
        });

        const readyNow =
          res.status === "PARTIAL_REVIEW_READY" ||
          res.status === "AWAITING_REVIEW" ||
          res.status === "COMPLETED" ||
          res.processed_pages >= 1;

        updateDocument(clientId, (prev) => ({
          ...prev,
          jobId: res.job_id,
          jobStatus: res.status,
          documentProfile: res.document_profile || prev.documentProfile,
          originalSha256: res.original_file_sha256,
          totalPages: res.total_pages,
          processedPages: res.processed_pages,
          isReadyForReview: readyNow,
          isAnalysisComplete:
            res.status === "AWAITING_REVIEW" || res.status === "COMPLETED",
          isRedactionComplete: res.status === "COMPLETED",
          badgeStatus: derived.badgeStatus,
          stageNumber: derived.stageNumber,
          stageLabel: derived.stageLabel,
          progressPercent: derived.progressPercent,
        }));

        await syncDocumentSnapshot(clientId, res.job_id);
        if (res.status !== "COMPLETED" && res.status !== "FAILED") {
          openStreamForDocument(clientId, res.job_id);
        }
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.message
            : "Erro inesperado ao enviar o documento PDF.";
        updateDocument(clientId, (prev) => ({
          ...prev,
          badgeStatus: "Erro",
          stageLabel: "Erro no envio / análise",
          progressPercent: null,
          errorMessage: msg,
        }));
      } finally {
        inFlightUploadsRef.current.delete(clientId);
      }
    },
    [
      appendClientVerboseLogs,
      openStreamForDocument,
      syncDocumentSnapshot,
      updateDocument,
    ]
  );

  useEffect(() => {
    const waiting = documents.filter(
      (d) =>
        d.badgeStatus === "Aguardando" &&
        !d.jobId &&
        !inFlightUploadsRef.current.has(d.clientId)
    );
    if (waiting.length === 0) return;

    const availableSlots = Math.max(
      0,
      MAX_CONCURRENT_UPLOADS - inFlightUploadsRef.current.size
    );
    for (const item of waiting.slice(0, availableSlots)) {
      void startUploadForItem(item);
    }
  }, [documents, startUploadForItem]);

  const handleFilesSelected = useCallback(
    (files: File[]) => {
      if (!files.length) return;
      const queued = files.map((f) =>
        createQueuedDocument(f, customRegexRules, ignoreSeiStandardBlocks)
      );
      setDocuments((prev) => [...prev, ...queued]);
      setShowAddFilesDropzone(false);
    },
    [customRegexRules, ignoreSeiStandardBlocks]
  );

  const handleSelectForReview = useCallback((clientId: string) => {
    setActiveDocId(clientId);
    setActiveOccurrenceId(null);
    setIsManualDrawMode(false);
    setRedactionError(null);
    setIsExportModalOpen(false);
  }, []);

  const handleRetryDocument = useCallback(
    (clientId: string) => {
      closeStreamForClient(clientId);
      latestStageByClientRef.current.delete(clientId);
      setVerboseLogsByClient((prev) => {
        const next = { ...prev };
        delete next[clientId];
        return next;
      });
      updateDocument(clientId, (prev) => ({
        ...prev,
        badgeStatus: "Aguardando",
        stageNumber: 1,
        stageLabel: PIPELINE_STAGE_LABELS[1],
        progressPercent: 0,
        jobId: null,
        jobStatus: "RECEIVED",
        processedPages: 0,
        totalPages: 0,
        isReadyForReview: false,
        isAnalysisComplete: false,
        isRedactionComplete: false,
        pagesByNumber: {},
        occurrences: [],
        originalSha256: null,
        redactedSha256: null,
        auditLog: null,
        downloadPdfUrl: null,
        downloadReportUrl: null,
        errorMessage: null,
        currentPage: 1,
      }));
    },
    [closeStreamForClient, updateDocument]
  );

  const handleRemoveDocument = useCallback(
    (clientId: string) => {
      closeStreamForClient(clientId);
      latestStageByClientRef.current.delete(clientId);
      setVerboseLogsByClient((prev) => {
        const next = { ...prev };
        delete next[clientId];
        return next;
      });
      setDocuments((prev) => prev.filter((d) => d.clientId !== clientId));
      if (activeDocId === clientId) {
        setActiveDocId(null);
        setActiveOccurrenceId(null);
        setIsExportModalOpen(false);
      }
    },
    [activeDocId, closeStreamForClient]
  );

  const handleResetAll = useCallback(() => {
    for (const timer of reconnectTimersRef.current.values()) {
      clearTimeout(timer);
    }
    reconnectTimersRef.current.clear();
    reconnectAttemptsRef.current.clear();
    for (const es of eventSourcesRef.current.values()) {
      es.close();
    }
    eventSourcesRef.current.clear();
    latestStageByClientRef.current.clear();
    inFlightUploadsRef.current.clear();
    setDocuments([]);
    setVerboseLogsByClient({});
    setActiveDocId(null);
    setShowAddFilesDropzone(false);
    setActiveOccurrenceId(null);
    setIsManualDrawMode(false);
    setIsExportModalOpen(false);
    setIsApplyingRedaction(false);
    setRedactionError(null);
  }, []);

  const activeDoc = useMemo<DocumentQueueItemState | null>(
    () => documents.find((d) => d.clientId === activeDocId) ?? null,
    [documents, activeDocId]
  );

  const activeVerboseLogs = useMemo<VerboseStepLog[]>(() => {
    if (!activeDocId) return [];
    return verboseLogsByClient[activeDocId] || [];
  }, [activeDocId, verboseLogsByClient]);

  const activeProcessingPage = useMemo<number | null>(() => {
    if (!activeDoc) return null;
    if (
      activeDoc.isAnalysisComplete ||
      activeDoc.isRedactionComplete ||
      activeDoc.badgeStatus === "Erro" ||
      activeDoc.totalPages <= 0 ||
      activeDoc.processedPages >= activeDoc.totalPages
    ) {
      return null;
    }
    for (let i = activeVerboseLogs.length - 1; i >= 0; i -= 1) {
      const details = activeVerboseLogs[i]?.details;
      const pNum =
        details && typeof details.page_number === "number"
          ? details.page_number
          : null;
      if (
        pNum !== null &&
        pNum > activeDoc.processedPages &&
        pNum <= activeDoc.totalPages
      ) {
        return pNum;
      }
    }
    return Math.min(activeDoc.totalPages, activeDoc.processedPages + 1);
  }, [activeDoc, activeVerboseLogs]);

  const deselectedSet = useMemo<Set<string>>(
    () => new Set(activeDoc?.deselectedIds || []),
    [activeDoc?.deselectedIds]
  );

  const getEffectiveMode = useCallback(
    (occ: DetectedOccurrence): RedactionActionMode => {
      if (!activeDoc) return "BLACKOUT";
      if (activeDoc.actionModesById[occ.id]) {
        return activeDoc.actionModesById[occ.id];
      }
      if (deselectedSet.has(occ.id)) {
        return "KEEP";
      }
      return occ.action_mode || occ.recommended_mode || "BLACKOUT";
    },
    [activeDoc, deselectedSet]
  );

  const setActiveDocPage = useCallback(
    (nextPageOrUpdater: number | ((prev: number) => number)) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => {
        const nextPage =
          typeof nextPageOrUpdater === "function"
            ? nextPageOrUpdater(prev.currentPage)
            : nextPageOrUpdater;
        return { ...prev, currentPage: nextPage };
      });
    },
    [activeDocId, updateDocument]
  );

  // Atalhos de Teclado Nativos no Workspace (Seta Esquerda/Direita + tecla M)
  useEffect(() => {
    if (!activeDoc?.isReadyForReview || isExportModalOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "SELECT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }

      if (e.key === "ArrowLeft") {
        e.preventDefault();
        setActiveDocPage((p) => Math.max(1, p - 1));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setActiveDocPage((p) =>
          Math.min(Math.max(1, activeDoc.totalPages), p + 1)
        );
      } else if (e.key === "m" || e.key === "M") {
        e.preventDefault();
        setIsManualDrawMode((prev) => !prev);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [
    activeDoc?.isReadyForReview,
    activeDoc?.totalPages,
    isExportModalOpen,
    setActiveDocPage,
  ]);

  const activeStepNumber = useMemo<number>(() => {
    if (!activeDoc) {
      return documents.some(
        (d) => d.badgeStatus === "Carregando" || d.badgeStatus === "Processando"
      )
        ? 2
        : 1;
    }
    if (activeDoc.isRedactionComplete || activeDoc.jobStatus === "COMPLETED") {
      return 7;
    }
    if (activeDoc.jobStatus === "VALIDATING") return 6;
    if (isApplyingRedaction || activeDoc.jobStatus === "REDACTING") return 5;
    if (activeDoc.isReadyForReview) {
      return activeDoc.isAnalysisComplete ? 4 : 3;
    }
    return 2;
  }, [activeDoc, documents, isApplyingRedaction]);

  const currentPageOccurrences = useMemo<DetectedOccurrence[]>(() => {
    if (!activeDoc) return [];
    return activeDoc.occurrences.filter(
      (o) => o.page_number === activeDoc.currentPage
    );
  }, [activeDoc]);

  const currentPageManualBoxes = useMemo<ManualRedactionBox[]>(() => {
    if (!activeDoc) return [];
    return activeDoc.manualBoxes.filter(
      (b) => b.page_number === activeDoc.currentPage
    );
  }, [activeDoc]);

  const selectedAutoCount = useMemo<number>(() => {
    if (!activeDoc) return 0;
    return activeDoc.occurrences.filter((o) => getEffectiveMode(o) !== "KEEP")
      .length;
  }, [activeDoc, getEffectiveMode]);

  const currentPageImageUrl = useMemo<string | null>(() => {
    if (!activeDoc?.jobId) return null;
    const pageMeta = activeDoc.pagesByNumber[activeDoc.currentPage];
    if (pageMeta?.image_url) return pageMeta.image_url;
    return getPageWebpUrl(activeDoc.jobId, activeDoc.currentPage);
  }, [activeDoc]);

  const nextPageImageUrl = useMemo<string | null>(() => {
    if (!activeDoc?.jobId) return null;
    const nextNum = activeDoc.currentPage + 1;
    if (nextNum > activeDoc.totalPages) return null;
    const nextMeta = activeDoc.pagesByNumber[nextNum];
    return nextMeta?.image_url || null;
  }, [activeDoc]);

  const handleSetOccurrenceActionMode = useCallback(
    (occId: string, mode: RedactionActionMode) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => {
        const targetOcc = prev.occurrences.find((o) => o.id === occId);
        const affectedIds =
          syncGroupByDefault && targetOcc?.group_id
            ? prev.occurrences
                .filter((o) => o.group_id === targetOcc.group_id)
                .map((o) => o.id)
            : [occId];

        const nextModes = { ...prev.actionModesById };
        const nextDeselected = new Set(prev.deselectedIds);
        for (const id of affectedIds) {
          nextModes[id] = mode;
          if (mode === "KEEP") {
            nextDeselected.add(id);
          } else {
            nextDeselected.delete(id);
          }
        }

        return {
          ...prev,
          ...markDocNeedsReapplication(prev),
          actionModesById: nextModes,
          deselectedIds: Array.from(nextDeselected),
        };
      });
    },
    [activeDocId, syncGroupByDefault, updateDocument]
  );

  const handleToggleOccurrence = useCallback(
    (occId: string) => {
      if (!activeDoc) return;
      const targetOcc = activeDoc.occurrences.find((o) => o.id === occId);
      if (!targetOcc) return;
      const currentMode = getEffectiveMode(targetOcc);
      const nextMode: RedactionActionMode =
        currentMode === "KEEP"
          ? targetOcc.recommended_mode || "BLACKOUT"
          : "KEEP";
      handleSetOccurrenceActionMode(occId, nextMode);
    },
    [activeDoc, getEffectiveMode, handleSetOccurrenceActionMode]
  );

  const handleApplyAiRecommendation = useCallback(() => {
    if (!activeDocId) return;
    updateDocument(activeDocId, (prev) => {
      const nextModes: Record<string, RedactionActionMode> = {};
      const nextDeselected: string[] = [];
      for (const occ of prev.occurrences) {
        const rec = occ.recommended_mode || "BLACKOUT";
        nextModes[occ.id] = rec;
        if (rec === "KEEP") nextDeselected.push(occ.id);
      }
      return {
        ...prev,
        ...markDocNeedsReapplication(prev),
        actionModesById: nextModes,
        deselectedIds: nextDeselected,
      };
    });
  }, [activeDocId, updateDocument]);

  const handleSetAllBlackout = useCallback(() => {
    if (!activeDocId) return;
    updateDocument(activeDocId, (prev) => {
      const nextModes: Record<string, RedactionActionMode> = {};
      for (const occ of prev.occurrences) {
        nextModes[occ.id] = "BLACKOUT";
      }
      return {
        ...prev,
        ...markDocNeedsReapplication(prev),
        actionModesById: nextModes,
        deselectedIds: [],
      };
    });
  }, [activeDocId, updateDocument]);

  const handleMaskAllCpfs = useCallback(() => {
    if (!activeDocId) return;
    updateDocument(activeDocId, (prev) => {
      const nextModes = { ...prev.actionModesById };
      const nextDeselected = new Set(prev.deselectedIds);
      for (const occ of prev.occurrences) {
        if (occ.entity_type === "CPF" || occ.entity_type === "RG") {
          nextModes[occ.id] = "PARTIAL_MASK";
          nextDeselected.delete(occ.id);
        }
      }
      return {
        ...prev,
        ...markDocNeedsReapplication(prev),
        actionModesById: nextModes,
        deselectedIds: Array.from(nextDeselected),
      };
    });
  }, [activeDocId, updateDocument]);

  const handleSetAllDocumentSelection = useCallback(
    (selectAll: boolean) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => {
        const nextModes: Record<string, RedactionActionMode> = {};
        if (selectAll) {
          for (const occ of prev.occurrences) {
            nextModes[occ.id] = occ.recommended_mode || "BLACKOUT";
          }
          return {
            ...prev,
            ...markDocNeedsReapplication(prev),
            actionModesById: nextModes,
            deselectedIds: [],
          };
        }
        for (const occ of prev.occurrences) {
          nextModes[occ.id] = "KEEP";
        }
        return {
          ...prev,
          ...markDocNeedsReapplication(prev),
          actionModesById: nextModes,
          deselectedIds: prev.occurrences.map((o) => o.id),
        };
      });
    },
    [activeDocId, updateDocument]
  );

  const handleSetCategorySelection = useCallback(
    (category: PiiCategory, shouldSelect: boolean) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => {
        const nextModes = { ...prev.actionModesById };
        const nextDeselected = new Set(prev.deselectedIds);
        for (const occ of prev.occurrences) {
          if (occ.category === category) {
            nextModes[occ.id] = shouldSelect
              ? occ.recommended_mode || "BLACKOUT"
              : "KEEP";
            if (shouldSelect) {
              nextDeselected.delete(occ.id);
            } else {
              nextDeselected.add(occ.id);
            }
          }
        }
        return {
          ...prev,
          ...markDocNeedsReapplication(prev),
          actionModesById: nextModes,
          deselectedIds: Array.from(nextDeselected),
        };
      });
    },
    [activeDocId, updateDocument]
  );

  const handleSetGroupSelection = useCallback(
    (groupId: string, shouldSelect: boolean) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => {
        const nextModes = { ...prev.actionModesById };
        const nextDeselected = new Set(prev.deselectedIds);
        for (const occ of prev.occurrences) {
          if (occ.group_id === groupId) {
            nextModes[occ.id] = shouldSelect
              ? occ.recommended_mode || "BLACKOUT"
              : "KEEP";
            if (shouldSelect) {
              nextDeselected.delete(occ.id);
            } else {
              nextDeselected.add(occ.id);
            }
          }
        }
        return {
          ...prev,
          ...markDocNeedsReapplication(prev),
          actionModesById: nextModes,
          deselectedIds: Array.from(nextDeselected),
        };
      });
    },
    [activeDocId, updateDocument]
  );

  const handleAddManualBox = useCallback(
    (newBox: Omit<ManualRedactionBox, "id">) => {
      if (!activeDocId) return;
      const manualId = `man_p${newBox.page_number}_${Date.now()}_${Math.random()
        .toString(36)
        .slice(2, 6)}`;
      updateDocument(activeDocId, (prev) => ({
        ...prev,
        ...markDocNeedsReapplication(prev),
        manualBoxes: [...prev.manualBoxes, { ...newBox, id: manualId }],
      }));
    },
    [activeDocId, updateDocument]
  );

  const handleUpdateManualBox = useCallback(
    (id: string, updated: Partial<ManualRedactionBox>) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => ({
        ...prev,
        ...markDocNeedsReapplication(prev),
        manualBoxes: prev.manualBoxes.map((box) =>
          box.id === id ? { ...box, ...updated } : box
        ),
      }));
    },
    [activeDocId, updateDocument]
  );

  const handleRemoveManualBox = useCallback(
    (id: string) => {
      if (!activeDocId) return;
      updateDocument(activeDocId, (prev) => ({
        ...prev,
        ...markDocNeedsReapplication(prev),
        manualBoxes: prev.manualBoxes.filter((box) => box.id !== id),
      }));
    },
    [activeDocId, updateDocument]
  );

  const handleOpenConfirmRedactionModal = useCallback(() => {
    setRedactionError(null);
    setIsExportModalOpen(true);
  }, []);

  const handleBackToEditModes = useCallback(() => {
    if (activeDocId) {
      updateDocument(activeDocId, (prev) => ({
        ...prev,
        ...markDocNeedsReapplication(prev),
      }));
    }
    setIsExportModalOpen(false);
  }, [activeDocId, updateDocument]);

  const handleConfirmAndApplyRedaction = useCallback(
    async (disclaimerAccepted: boolean) => {
      if (!activeDoc?.jobId || !disclaimerAccepted) return;
      const targetClientId = activeDoc.clientId;
      const targetJobId = activeDoc.jobId;

      setIsApplyingRedaction(true);
      setRedactionError(null);
      updateDocument(targetClientId, (prev) => ({
        ...prev,
        badgeStatus: "Processando",
        jobStatus: "REDACTING",
        stageNumber: 5,
        stageLabel: PIPELINE_STAGE_LABELS[5],
        progressPercent: null,
      }));

      try {
        const redactionItems: RedactionItem[] = activeDoc.occurrences.map(
          (o) => ({
            id: o.id,
            action_mode: getEffectiveMode(o),
            masked_preview: o.masked_preview,
          })
        );

        const selectedIds = redactionItems
          .filter((item) => item.action_mode !== "KEEP")
          .map((item) => item.id);

        const normalizedManuals = activeDoc.manualBoxes.map((b) => ({
          page_number: b.page_number,
          x0_norm: b.x0_norm,
          y0_norm: b.y0_norm,
          x1_norm: b.x1_norm,
          y1_norm: b.y1_norm,
          category: b.category,
        }));

        const execRes = await applyDocumentRedaction(targetJobId, {
          selected_ids: selectedIds,
          items: redactionItems,
          redaction_items: redactionItems,
          manual_boxes: normalizedManuals,
          anonymization_disclaimer_acknowledged: disclaimerAccepted,
        });

        if (execRes.verbose_logs && execRes.verbose_logs.length > 0) {
          appendClientVerboseLogs(targetClientId, execRes.verbose_logs);
        }

        const rev = Date.now();
        updateDocument(targetClientId, (prev) => ({
          ...prev,
          jobStatus: execRes.status,
          documentProfile: execRes.document_profile || prev.documentProfile,
          originalSha256: execRes.original_file_sha256,
          redactedSha256: execRes.redacted_file_sha256,
          auditLog: execRes.audit_log,
          downloadPdfUrl: appendRevisionParam(
            getApiUrl(execRes.download_pdf_url),
            rev
          ),
          downloadReportUrl: appendRevisionParam(
            getApiUrl(execRes.download_report_url),
            rev
          ),
          isRedactionComplete: true,
          badgeStatus: "Concluído",
          stageNumber: 6,
          stageLabel: PIPELINE_STAGE_LABELS[6],
          progressPercent: 100,
        }));

        await syncDocumentSnapshot(targetClientId, targetJobId);
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.message
            : "Erro ao aplicar o tarjamento definitivo.";
        setRedactionError(msg);
        updateDocument(targetClientId, (prev) => ({
          ...prev,
          badgeStatus: "Erro",
          stageLabel: "Erro ao gerar documento final",
          progressPercent: null,
          errorMessage: msg,
        }));
      } finally {
        setIsApplyingRedaction(false);
      }
    },
    [
      activeDoc,
      appendClientVerboseLogs,
      getEffectiveMode,
      syncDocumentSnapshot,
      updateDocument,
    ]
  );

  const hasAnyUploadInFlight = documents.some(
    (d) => d.badgeStatus === "Aguardando" || d.badgeStatus === "Carregando"
  );

  return {
    documents,
    activeDocId,
    activeDoc,
    activeVerboseLogs,
    activeProcessingPage,
    activeStepNumber,
    showAddFilesDropzone,
    setShowAddFilesDropzone,
    customRegexRules,
    setCustomRegexRules,
    ignoreSeiStandardBlocks,
    setIgnoreSeiStandardBlocks,
    syncGroupByDefault,
    setSyncGroupByDefault,
    activeOccurrenceId,
    setActiveOccurrenceId,
    isManualDrawMode,
    setIsManualDrawMode,
    manualDrawCategory,
    setManualDrawCategory,
    isExportModalOpen,
    setIsExportModalOpen,
    isApplyingRedaction,
    redactionError,
    hasAnyUploadInFlight,
    deselectedSet,
    currentPageOccurrences,
    currentPageManualBoxes,
    selectedAutoCount,
    currentPageImageUrl,
    nextPageImageUrl,
    setActiveDocPage,
    handleFilesSelected,
    handleSelectForReview,
    handleRetryDocument,
    handleRemoveDocument,
    handleResetAll,
    handleSetOccurrenceActionMode,
    handleToggleOccurrence,
    handleApplyAiRecommendation,
    handleSetAllBlackout,
    handleMaskAllCpfs,
    handleSetAllDocumentSelection,
    handleSetCategorySelection,
    handleSetGroupSelection,
    handleAddManualBox,
    handleUpdateManualBox,
    handleRemoveManualBox,
    handleOpenConfirmRedactionModal,
    handleBackToEditModes,
    handleConfirmAndApplyRedaction,
  };
}
