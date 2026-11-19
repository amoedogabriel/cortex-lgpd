"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Download,
  FileCheck,
  FilePlus2,
  Lock,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { CustomRegexPanel } from "@/components/upload/CustomRegexPanel";
import { DocumentProgressCard } from "@/components/upload/DocumentProgressCard";
import { DropzoneUpload } from "@/components/upload/DropzoneUpload";
import {
  ExportAndReportModal,
  LightweightPageViewer,
  OccurrencesSidebar,
} from "@/components/workspace";
import { cn } from "@/lib/utils";
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
  DocumentQueueItemState,
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

interface WorkflowStep {
  number: number;
  label: string;
  shortDesc: string;
}

const WORKFLOW_STEPS: WorkflowStep[] = [
  { number: 1, label: "1. Anexar", shortDesc: "Validação segura do PDF" },
  { number: 2, label: "2. Analisar", shortDesc: "Extração + OCR ONNX + NLP" },
  { number: 3, label: "3. Revisar", shortDesc: "Conferência página a página" },
  { number: 4, label: "4. Selecionar", shortDesc: "Tarja Preta ou Máscara Parcial" },
  { number: 5, label: "5. Aplicar", shortDesc: "Redação física irreversível" },
  { number: 6, label: "6. Validar", shortDesc: "Inspeção cirúrgica de segurança" },
  { number: 7, label: "7. Exportar", shortDesc: "PDF final + Relatório Zero-PII" },
];

const MAX_CONCURRENT_UPLOADS = 2;

function createQueuedDocument(
  file: File,
  customRegexes?: CustomRegexRule[]
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
    customRegexes: customRegexes && customRegexes.length > 0 ? customRegexes : undefined,
    currentPage: 1,
    deselectedIds: [],
    actionModesById: {},
    manualBoxes: [],
  };
}

export default function HomePage() {
  const [documents, setDocuments] = useState<DocumentQueueItemState[]>([]);
  const [activeDocId, setActiveDocId] = useState<string | null>(null);
  const [showAddFilesDropzone, setShowAddFilesDropzone] = useState<boolean>(false);
  const [customRegexRules, setCustomRegexRules] = useState<CustomRegexRule[]>([]);

  // Estado auxiliar do Workspace Split-Screen
  const [syncGroupByDefault, setSyncGroupByDefault] = useState<boolean>(true);
  const [activeOccurrenceId, setActiveOccurrenceId] = useState<string | null>(null);
  const [isManualDrawMode, setIsManualDrawMode] = useState<boolean>(false);
  const [manualDrawCategory, setManualDrawCategory] =
    useState<PiiCategory>("PERSONAL_COMMON");

  // Estado do Modal Legal, Validação e Exportação
  const [isExportModalOpen, setIsExportModalOpen] = useState<boolean>(false);
  const [isApplyingRedaction, setIsApplyingRedaction] = useState<boolean>(false);
  const [redactionError, setRedactionError] = useState<string | null>(null);

  const inFlightUploadsRef = useRef<Set<string>>(new Set());
  const eventSourcesRef = useRef<Map<string, EventSource>>(new Map());
  const latestStageByClientRef = useRef<Map<string, string>>(new Map());
  const mountedRef = useRef<boolean>(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      for (const es of eventSourcesRef.current.values()) {
        es.close();
      }
      eventSourcesRef.current.clear();
    };
  }, []);

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

  const closeStreamForClient = useCallback((clientId: string) => {
    const existing = eventSourcesRef.current.get(clientId);
    if (existing) {
      existing.close();
      eventSourcesRef.current.delete(clientId);
    }
  }, []);

  const syncDocumentSnapshot = useCallback(
    async (clientId: string, targetJobId: string) => {
      try {
        const snapshot = await fetchDocumentOccurrences(targetJobId);
        const logs = snapshot.verbose_logs || [];
        if (logs.length > 0) {
          latestStageByClientRef.current.set(
            clientId,
            logs[logs.length - 1].stage
          );
        }
        const latestStage = latestStageByClientRef.current.get(clientId) || null;

        updateDocument(clientId, (prev) => {
          const mappedPages: Record<number, PageData> = {};
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

          const derived = derivePipelineProgress({
            jobStatus: snapshot.status,
            latestBackendStage: latestStage,
            processedPages: snapshot.processed_pages,
            totalPages: snapshot.total_pages,
          });

          return {
            ...prev,
            badgeStatus: derived.badgeStatus,
            stageNumber: derived.stageNumber,
            stageLabel: derived.stageLabel,
            progressPercent: derived.progressPercent,
            jobStatus: snapshot.status,
            documentProfile: snapshot.document_profile || prev.documentProfile,
            totalPages: snapshot.total_pages,
            processedPages: snapshot.processed_pages,
            isReadyForReview: isReady,
            isAnalysisComplete: isAnalysisDone,
            isRedactionComplete: isRedacted,
            pagesByNumber: mappedPages,
            occurrences: snapshot.occurrences || [],
            originalSha256: snapshot.original_file_sha256,
            redactedSha256:
              snapshot.redacted_file_sha256 || prev.redactedSha256,
            auditLog: snapshot.audit_log || prev.auditLog,
            downloadPdfUrl: isRedacted
              ? prev.downloadPdfUrl || getRedactedPdfDownloadUrl(targetJobId, rev)
              : null,
            downloadReportUrl: isRedacted
              ? prev.downloadReportUrl || getReportPdfDownloadUrl(targetJobId, rev)
              : null,
          };
        });
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.message
            : "Erro ao sincronizar estado do documento.";
        updateDocument(clientId, (prev) => ({
          ...prev,
          errorMessage: prev.errorMessage || msg,
        }));
      }
    },
    [closeStreamForClient, updateDocument]
  );

  const openStreamForDocument = useCallback(
    (clientId: string, jobId: string) => {
      if (eventSourcesRef.current.has(clientId)) {
        return;
      }

      const streamUrl = getApiUrl(`/api/v1/documents/${jobId}/stream`);
      const es = new EventSource(streamUrl);
      eventSourcesRef.current.set(clientId, es);

      es.addEventListener("verbose_step", (evt: MessageEvent<string>) => {
        try {
          const envelope = JSON.parse(evt.data) as SseEventEnvelope<VerboseStepLog>;
          const backendStage = envelope?.data?.stage;
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
              progressPercent: derived.progressPercent,
            };
          });
        } catch {
          // Ignora evento malformado isolado
        }
      });

      es.addEventListener("page_ready", (evt: MessageEvent<string>) => {
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
            const count = Object.keys(nextPages).length;

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
              totalPages: payload.total_pages,
            });

            return {
              ...prev,
              jobStatus: payload.status,
              documentProfile:
                payload.document_profile || prev.documentProfile,
              totalPages: payload.total_pages,
              processedPages: count,
              pagesByNumber: nextPages,
              occurrences: nextOccs,
              isReadyForReview: prev.isReadyForReview || pageNum === 1,
              badgeStatus: derived.badgeStatus,
              stageNumber: derived.stageNumber,
              stageLabel: derived.stageLabel,
              progressPercent: derived.progressPercent,
            };
          });
        } catch {
          // Ignora evento malformado isolado
        }
      });

      es.addEventListener("progress", (evt: MessageEvent<string>) => {
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<ProgressEventPayload>;
          const payload = envelope.data;
          updateDocument(clientId, (prev) => {
            const derived = derivePipelineProgress({
              jobStatus: payload.status,
              latestBackendStage: latestStageByClientRef.current.get(clientId),
              processedPages: payload.processed_pages,
              totalPages: payload.total_pages,
            });
            return {
              ...prev,
              jobStatus: payload.status,
              processedPages: payload.processed_pages,
              totalPages: payload.total_pages,
              badgeStatus: derived.badgeStatus,
              stageNumber: derived.stageNumber,
              stageLabel: derived.stageLabel,
              progressPercent:
                derived.progressPercent ?? Math.round(payload.percentage),
            };
          });
        } catch {
          // Ignora erro de parse
        }
      });

      es.addEventListener("analysis_complete", (evt: MessageEvent<string>) => {
        try {
          const envelope = JSON.parse(
            evt.data
          ) as SseEventEnvelope<AnalysisCompleteEventPayload>;
          latestStageByClientRef.current.set(
            clientId,
            "ETAPA_4_AGUARDANDO_REVISAO"
          );
          updateDocument(clientId, (prev) => {
            const derived = derivePipelineProgress({
              jobStatus: envelope.data.status,
              latestBackendStage: "ETAPA_4_AGUARDANDO_REVISAO",
              processedPages: envelope.data.total_pages,
              totalPages: envelope.data.total_pages,
            });
            return {
              ...prev,
              jobStatus: envelope.data.status,
              documentProfile:
                envelope.data.document_profile || prev.documentProfile,
              totalPages: envelope.data.total_pages,
              processedPages: envelope.data.total_pages,
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
            }
          } catch {
            // Evento nativo de fechamento do EventSource após término do stream
          }
        }
        closeStreamForClient(clientId);
      });
    },
    [closeStreamForClient, syncDocumentSnapshot, updateDocument]
  );

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

  // Fila controlada: envia até `MAX_CONCURRENT_UPLOADS` PDFs em paralelo sem travar PCs antigos
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
          item.customRegexes
        );

        const logs = res.verbose_logs || [];
        if (logs.length > 0) {
          latestStageByClientRef.current.set(
            clientId,
            logs[logs.length - 1].stage
          );
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
    [openStreamForDocument, syncDocumentSnapshot, updateDocument]
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
      const queued = files.map((f) => createQueuedDocument(f, customRegexRules));
      setDocuments((prev) => [...prev, ...queued]);
      setShowAddFilesDropzone(false);
    },
    [customRegexRules]
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
    for (const es of eventSourcesRef.current.values()) {
      es.close();
    }
    eventSourcesRef.current.clear();
    latestStageByClientRef.current.clear();
    inFlightUploadsRef.current.clear();
    setDocuments([]);
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
      return documents.some((d) => d.badgeStatus === "Carregando" || d.badgeStatus === "Processando")
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

  const markDocNeedsReapplication = (
    prev: DocumentQueueItemState
  ): Partial<DocumentQueueItemState> => {
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
  };

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
    [activeDoc, getEffectiveMode, syncDocumentSnapshot, updateDocument]
  );

  const hasAnyUploadInFlight = documents.some(
    (d) => d.badgeStatus === "Aguardando" || d.badgeStatus === "Carregando"
  );

  return (
    <div className="flex min-h-screen flex-col bg-[#f8fafc] text-[#0f172a]">
      {/* Header Institucional Sólido UFAM + Stepper Claro */}
      <header className="bg-[#0e4d3a] text-white">
        <div className="mx-auto flex w-full max-w-[1376px] flex-wrap items-center justify-between gap-6 px-8 py-4 lg:flex-nowrap">
          <div className="flex min-w-0 flex-1 items-center gap-3.5">
            <div
              aria-hidden="true"
              className="grid h-12 w-12 shrink-0 place-items-center rounded-[8px] border border-white/40 text-[14px] font-bold tracking-wider text-white"
            >
              UFAM
            </div>
            <div className="min-w-0">
              <h1 className="text-[18px] font-bold leading-tight text-white">
                Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM
              </h1>
              <p className="mt-0.5 text-[12px] text-[#d1fae5]">
                Universidade Federal do Amazonas • Proteção de Dados Pessoais (LGPD — Lei nº 13.709/2018) e Acesso à Informação (LAI — Lei nº 12.527/2011)
              </p>
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2.5">
            <span
              className="inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-full border border-white/30 bg-white/[0.12] px-3 text-[12px] font-semibold text-white"
              title="Todo o processamento de OCR ONNX, NLP spaCy e Redação Física PyMuPDF ocorre em servidor institucional isolado, sem envio para nuvem externa."
            >
              <Lock className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Processamento Local Seguro • Zero-PII</span>
            </span>

            {activeDoc && (
              <button
                type="button"
                aria-expanded={showAddFilesDropzone}
                onClick={() => setShowAddFilesDropzone((prev) => !prev)}
                className="inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-[8px] border border-white/30 bg-white/[0.12] px-3 text-[12px] font-semibold text-white transition-all duration-200 hover:bg-white/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0e4d3a]"
              >
                <FilePlus2 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>
                  {showAddFilesDropzone ? "Ocultar Anexação" : "Anexar Mais PDFs"}
                </span>
              </button>
            )}

            {documents.length > 0 && (
              <button
                type="button"
                onClick={handleResetAll}
                className="inline-flex h-[30px] items-center gap-1.5 whitespace-nowrap rounded-[8px] border border-white/30 bg-white px-3 text-[12px] font-semibold text-[#0e4d3a] transition-all duration-200 hover:bg-[#ecfdf5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-[#0e4d3a]"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Limpar Fila</span>
              </button>
            )}
          </div>
        </div>

        {/* Stepper Claro de 7 Etapas sem Truncamento */}
        <nav
          aria-label="Etapas do fluxo de tarjamento"
          className="w-full border-b border-[#e2e8f0] bg-white text-[#0f172a]"
        >
          <div className="w-full px-8 py-3">
            <ol className="grid w-full grid-cols-2 gap-2 sm:grid-cols-4 lg:flex lg:gap-2">
              {WORKFLOW_STEPS.map((step) => {
                const isCompleted = step.number < activeStepNumber;
                const isCurrent = step.number === activeStepNumber;

                return (
                  <li
                    key={step.number}
                    aria-current={isCurrent ? "step" : undefined}
                    className={cn(
                      "flex min-w-0 flex-1 items-center gap-2.5 rounded-[10px] px-3.5 py-2.5 text-[13px] transition-all duration-200",
                      isCurrent &&
                        "bg-[#fef3c7] text-[#451a03] shadow-[0_0_0_2px_#f59e0b_inset]",
                      isCompleted && "bg-[#ecfdf5] text-[#065f46]",
                      !isCurrent && !isCompleted && "bg-[#f8fafc] text-[#475569]"
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        "grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full text-[11px] font-bold tabular-nums",
                        isCompleted && "bg-[#0e5a43] text-white",
                        isCurrent && "bg-[#f59e0b] text-[#451a03]",
                        !isCurrent &&
                          !isCompleted &&
                          "border-[1.5px] border-[#94a3b8] bg-white text-[#475569]"
                      )}
                    >
                      {isCompleted ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : (
                        step.number
                      )}
                    </span>
                    <div className="min-w-0">
                      <span className="block font-semibold leading-tight">
                        {step.label}
                      </span>
                      <span className="block text-[11px] leading-snug opacity-85">
                        {step.shortDesc}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        </nav>
      </header>

      {/* Conteúdo Principal */}
      {activeDoc && activeDoc.isReadyForReview ? (
        <main className="mx-auto grid w-full max-w-[1376px] flex-1 grid-cols-1 items-start gap-6 px-8 pb-4 pt-6 lg:grid-cols-[minmax(0,1fr)_392px]">
          {/* Coluna Esquerda: Sessão/Documento Unificado + Visualizador */}
          <div className="flex min-w-0 flex-col gap-4">
            {showAddFilesDropzone && (
              <section aria-label="Envio de documentos PDF">
                <div className="rounded-[12px] border border-[#e2e8f0] bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,0.05)]">
                  <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-[#e2e8f0] pb-4">
                    <div>
                      <h2 className="text-[16px] font-semibold tracking-tight text-[#0f172a]">
                        Anexar Documento(s) PDF para Identificação e Tarjamento Definitivo
                      </h2>
                      <p className="mt-1 text-[12px] text-[#475569]">
                        Arquitetura Thin-Client (WebP + SVG) com suporte a múltiplos arquivos e liberação imediata da Página 1 (<em>First-Page-First</em>).
                      </p>
                    </div>
                    <span className="rounded-full bg-[#f1f5f9] px-3 py-1 text-[12px] font-medium text-[#334155]">
                      Otimizado para Estações Administrativas UFAM
                    </span>
                  </div>

                  <DropzoneUpload
                    onFilesSelected={handleFilesSelected}
                    isUploading={hasAnyUploadInFlight}
                    uploadError={null}
                    maxSizeMb={50}
                    compact
                  />
                  <CustomRegexPanel
                    rules={customRegexRules}
                    onChange={setCustomRegexRules}
                    disabled={hasAnyUploadInFlight}
                  />
                </div>
              </section>
            )}

            {documents.length > 0 && (
              <section
                aria-label="Progresso individual dos documentos"
                className="flex flex-col gap-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-[12px] font-semibold uppercase tracking-wider text-[#475569]">
                    Documentos na Sessão ({documents.length})
                  </h2>
                  {documents.length > 1 && (
                    <span className="text-[12px] text-[#475569]">
                      Clique em <strong className="font-semibold text-[#0f172a]">Revisar</strong> em qualquer documento pronto para alternar a visualização.
                    </span>
                  )}
                </div>

                <div className="flex flex-col gap-3">
                  {documents.map((docItem) => (
                    <DocumentProgressCard
                      key={docItem.clientId}
                      item={docItem}
                      isActive={docItem.clientId === activeDocId}
                      onSelectForReview={handleSelectForReview}
                      onRetry={handleRetryDocument}
                      onRemove={handleRemoveDocument}
                    />
                  ))}
                </div>

                {activeDoc.isRedactionComplete && (
                  <div className="flex flex-wrap items-center gap-2 rounded-[12px] border border-[#a7f3d0] bg-[#ecfdf5] px-4 py-2.5">
                    <button
                      type="button"
                      onClick={() => setIsExportModalOpen(true)}
                      className="inline-flex h-9 items-center gap-1.5 rounded-[8px] border border-[#a7f3d0] bg-white px-3.5 text-[13px] font-semibold text-[#065f46] transition-all duration-200 hover:bg-[#d1fae5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
                    >
                      <ShieldCheck className="h-4 w-4 text-[#0e5a43]" aria-hidden="true" />
                      <span>Ver Checklist de Segurança</span>
                    </button>

                    {activeDoc.downloadPdfUrl && (
                      <a
                        href={activeDoc.downloadPdfUrl}
                        download
                        className="inline-flex h-9 items-center gap-1.5 rounded-[8px] border border-[#0e5a43] bg-[#0e5a43] px-3.5 text-[13px] font-semibold text-white transition-all duration-200 hover:bg-[#0b4a37] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
                      >
                        <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>Baixar PDF Tarjado</span>
                      </a>
                    )}

                    {activeDoc.downloadReportUrl && (
                      <a
                        href={activeDoc.downloadReportUrl}
                        download
                        className="inline-flex h-9 items-center gap-1.5 rounded-[8px] border border-[#cbd5e1] bg-white px-3.5 text-[13px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
                      >
                        <FileCheck className="h-3.5 w-3.5 text-[#0e5a43]" aria-hidden="true" />
                        <span>Relatório (Zero-PII)</span>
                      </a>
                    )}
                  </div>
                )}
              </section>
            )}

            <section aria-label="Workspace Split-Screen de Revisão" className="min-w-0">
              <LightweightPageViewer
                currentPage={activeDoc.currentPage}
                totalPages={activeDoc.totalPages}
                pageImageUrl={currentPageImageUrl}
                nextPageImageUrl={nextPageImageUrl}
                pageOccurrences={currentPageOccurrences}
                pageManualBoxes={currentPageManualBoxes}
                deselectedIds={deselectedSet}
                actionModesById={activeDoc.actionModesById}
                activeOccurrenceId={activeOccurrenceId}
                isManualDrawMode={isManualDrawMode}
                manualDrawCategory={manualDrawCategory}
                onPageChange={setActiveDocPage}
                onToggleOccurrence={handleToggleOccurrence}
                onSelectOccurrence={setActiveOccurrenceId}
                onToggleManualDrawMode={() =>
                  setIsManualDrawMode((prev) => !prev)
                }
                onManualDrawCategoryChange={setManualDrawCategory}
                onAddManualBox={handleAddManualBox}
                onUpdateManualBox={handleUpdateManualBox}
                onRemoveManualBox={handleRemoveManualBox}
              />
            </section>
          </div>

          {/* Coluna Direita: Painel Lateral de Decisão Alinhado ao Topo */}
          <OccurrencesSidebar
            status={activeDoc.jobStatus}
            documentProfile={activeDoc.documentProfile}
            processedPages={activeDoc.processedPages}
            totalPages={activeDoc.totalPages}
            progressPercentage={activeDoc.progressPercent ?? 0}
            currentPage={activeDoc.currentPage}
            allOccurrences={activeDoc.occurrences}
            manualBoxes={activeDoc.manualBoxes}
            deselectedIds={deselectedSet}
            actionModesById={activeDoc.actionModesById}
            activeOccurrenceId={activeOccurrenceId}
            isApplyingRedaction={isApplyingRedaction}
            applyToGroupByDefault={syncGroupByDefault}
            onApplyToGroupChange={setSyncGroupByDefault}
            onPageChange={setActiveDocPage}
            onSelectOccurrence={setActiveOccurrenceId}
            onToggleOccurrence={handleToggleOccurrence}
            onSetOccurrenceActionMode={handleSetOccurrenceActionMode}
            onApplyAiRecommendation={handleApplyAiRecommendation}
            onSetAllBlackout={handleSetAllBlackout}
            onMaskAllCpfs={handleMaskAllCpfs}
            onSetAllDocumentSelection={handleSetAllDocumentSelection}
            onSetCategorySelection={handleSetCategorySelection}
            onSetGroupSelection={handleSetGroupSelection}
            onRemoveManualBox={handleRemoveManualBox}
            onOpenConfirmRedactionModal={handleOpenConfirmRedactionModal}
          />

          {/* Modal de Alerta Legal (Art. 5º, XI, LGPD), Validação Cirúrgica e Exportação */}
          <ExportAndReportModal
            isOpen={isExportModalOpen}
            isApplyingRedaction={isApplyingRedaction}
            isRedactionComplete={activeDoc.isRedactionComplete}
            selectedAutoCount={selectedAutoCount}
            manualBoxesCount={activeDoc.manualBoxes.length}
            auditLog={activeDoc.auditLog}
            downloadPdfUrl={activeDoc.downloadPdfUrl}
            downloadReportUrl={activeDoc.downloadReportUrl}
            errorMessage={redactionError}
            onClose={() => setIsExportModalOpen(false)}
            onBackToEditModes={handleBackToEditModes}
            onConfirmAndApplyRedaction={handleConfirmAndApplyRedaction}
          />
        </main>
      ) : (
        <main className="mx-auto w-full max-w-[1376px] flex-1 space-y-5 px-8 pb-4 pt-6">
          {/* Lista Individual de Progresso por Documento */}
          {documents.length > 0 && (
            <section
              aria-label="Progresso individual dos documentos"
              className="space-y-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-[12px] font-semibold uppercase tracking-wider text-[#475569]">
                  Documentos na Sessão ({documents.length})
                </h2>
                <span className="text-[12px] text-[#475569]">
                  Clique em <strong className="font-semibold text-[#0f172a]">Revisar</strong> em qualquer documento pronto para alternar a visualização.
                </span>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {documents.map((docItem) => (
                  <DocumentProgressCard
                    key={docItem.clientId}
                    item={docItem}
                    isActive={docItem.clientId === activeDocId}
                    onSelectForReview={handleSelectForReview}
                    onRetry={handleRetryDocument}
                    onRemove={handleRemoveDocument}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Área de Upload Inicial */}
          <section aria-label="Envio de documentos PDF" className="space-y-4">
            <div className="rounded-[12px] border border-[#e2e8f0] bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,0.05)]">
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-[#e2e8f0] pb-4">
                <div>
                  <h2 className="text-[16px] font-semibold tracking-tight text-[#0f172a]">
                    Anexar Documento(s) PDF para Identificação e Tarjamento Definitivo
                  </h2>
                  <p className="mt-1 text-[12px] text-[#475569]">
                    Arquitetura Thin-Client (WebP + SVG) com suporte a múltiplos arquivos e liberação imediata da Página 1 (<em>First-Page-First</em>).
                  </p>
                </div>
                <span className="rounded-full bg-[#f1f5f9] px-3 py-1 text-[12px] font-medium text-[#334155]">
                  Otimizado para Estações Administrativas UFAM
                </span>
              </div>

              <DropzoneUpload
                onFilesSelected={handleFilesSelected}
                isUploading={hasAnyUploadInFlight}
                uploadError={null}
                maxSizeMb={50}
                compact={false}
              />
              <CustomRegexPanel
                rules={customRegexRules}
                onChange={setCustomRegexRules}
                disabled={hasAnyUploadInFlight}
              />
            </div>
          </section>
        </main>
      )}

      {/* Rodapé Institucional Unificado com Atalhos */}
      <footer className="mt-auto border-t border-[#e2e8f0] bg-white text-[#475569]">
        <div className="mx-auto flex w-full max-w-[1376px] flex-wrap items-center justify-between gap-4 px-8 py-3.5 text-[12px]">
          <div className="flex flex-wrap items-center gap-3 text-[12px] text-[#475569]">
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[#334155]">
                ←
              </kbd>
              <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[#334155]">
                →
              </kbd>
              <span>Navegar páginas</span>
            </span>
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[#334155]">
                M
              </kbd>
              <span>Desenho manual</span>
            </span>
          </div>

          <div className="text-right text-[12px] text-[#475569]">
            Universidade Federal do Amazonas (UFAM) • Sistema de Identificação e Tarjamento de Dados Pessoais • Conformidade LGPD (Lei nº 13.709/2018) e LAI (Lei nº 12.527/2011)
          </div>
        </div>
      </footer>
    </div>
  );
}
