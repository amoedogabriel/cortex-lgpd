"use client";

import React from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  Eye,
  FileCheck,
  FileText,
  Loader2,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PIPELINE_STAGE_LABELS } from "@/services/api";
import type {
  DocumentBadgeStatus,
  DocumentQueueItemState,
  PipelineStageNumber,
} from "@/types/document";

export interface DocumentProgressCardProps {
  item: DocumentQueueItemState;
  isActive: boolean;
  onSelectForReview: (clientId: string) => void;
  onRetry: (clientId: string) => void;
  onRemove: (clientId: string) => void;
}

const STAGE_STEPS: { num: PipelineStageNumber; short: string }[] = [
  { num: 1, short: "Envio" },
  { num: 2, short: "Estrutura" },
  { num: 3, short: "OCR/Texto" },
  { num: 4, short: "IA LGPD" },
  { num: 5, short: "Tarjamento" },
  { num: 6, short: "Concluído" },
];

export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  const mb = kb / 1024;
  return `${mb.toFixed(2)} MB`;
}

function getBadgeClasses(status: DocumentBadgeStatus): string {
  switch (status) {
    case "Aguardando":
      return "bg-[#f1f5f9] text-[#334155]";
    case "Carregando":
      return "bg-[#eff6ff] text-[#1d4ed8]";
    case "Processando":
      return "bg-[#fef3c7] text-[#92400e]";
    case "Concluído":
      return "bg-[#d1fae5] text-[#065f46]";
    case "Erro":
      return "bg-[#fef2f2] text-[#b91c1c]";
  }
}

export function DocumentProgressCard({
  item,
  isActive,
  onSelectForReview,
  onRetry,
  onRemove,
}: DocumentProgressCardProps) {
  const isError = item.badgeStatus === "Erro";
  const isDone = item.badgeStatus === "Concluído";
  const isInFlight =
    item.badgeStatus === "Carregando" || item.badgeStatus === "Processando";
  const isDeterminate = item.progressPercent !== null && !isError;
  const clampedPercent =
    item.progressPercent !== null
      ? Math.max(0, Math.min(100, Math.round(item.progressPercent)))
      : null;

  const activeStageText = isError
    ? item.stageLabel || "Falha ao processar documento"
    : item.stageLabel || PIPELINE_STAGE_LABELS[item.stageNumber];

  const isWaitingHumanReview =
    !isError &&
    !isDone &&
    item.jobStatus === "AWAITING_REVIEW" &&
    !item.isRedactionComplete;

  const progressSummaryText = isError
    ? "Interrompido"
    : isWaitingHumanReview
    ? `${item.processedPages}/${Math.max(1, item.totalPages)} pág. • Pronto para revisar e aplicar`
    : isDeterminate && clampedPercent !== null
    ? item.totalPages > 0 && item.stageNumber >= 3 && item.stageNumber <= 4
      ? `${item.processedPages}/${item.totalPages} pág. (${clampedPercent}%)`
      : `${clampedPercent}%`
    : "Processamento em andamento...";

  return (
    <article
      aria-label={`Progresso do documento ${item.fileName}`}
      aria-busy={isInFlight && !isWaitingHumanReview}
      className={cn(
        "rounded-[12px] border bg-white px-5 py-4 shadow-[0_1px_2px_rgba(15,23,42,0.05)] transition-all duration-200",
        isError
          ? "border-[#fecaca] bg-[#fef2f2]/40"
          : isActive
          ? "border-[#0e5a43]"
          : "border-[#e2e8f0] hover:border-[#cbd5e1]"
      )}
    >
      {/* Linha 1: Ícone + Nome + Tamanho + Badges Pastéis + SHA-256 + Ações */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] border",
              isError
                ? "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]"
                : "border-[#e2e8f0] bg-[#f8fafc] text-[#0e5a43]"
            )}
          >
            <FileText className="h-4 w-4" aria-hidden="true" />
          </span>

          <span
            className="truncate text-[15px] font-semibold text-[#0f172a]"
            title={item.fileName}
          >
            {item.fileName}
          </span>

          <span className="shrink-0 font-mono text-[12px] tabular-nums text-[#475569]">
            {formatFileSize(item.fileSizeBytes)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "inline-flex h-[22px] items-center gap-1 rounded-full px-2.5 text-[12px] font-semibold",
              getBadgeClasses(item.badgeStatus)
            )}
          >
            {isDone && <CheckCircle2 className="h-3 w-3" aria-hidden="true" />}
            {isError && <AlertCircle className="h-3 w-3" aria-hidden="true" />}
            {item.badgeStatus === "Aguardando" && (
              <Clock className="h-3 w-3" aria-hidden="true" />
            )}
            {isInFlight && (
              <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden="true" />
            )}
            <span>{item.badgeStatus}</span>
          </span>

          {item.isReadyForReview && !isError && (
            <button
              type="button"
              onClick={() => onSelectForReview(item.clientId)}
              aria-pressed={isActive}
              className={cn(
                "inline-flex items-center gap-1 text-[12px] font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2",
                isActive
                  ? "h-[22px] rounded-full bg-[#d1fae5] px-2.5 text-[#065f46]"
                  : "h-[26px] rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[#0f172a] hover:bg-[#f1f5f9]"
              )}
            >
              <Eye className="h-3 w-3" aria-hidden="true" />
              <span>{isActive ? "Em Revisão" : "Revisar"}</span>
            </button>
          )}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {isActive && item.originalSha256 && (
            <span className="font-mono text-[12px] tabular-nums text-[#475569]">
              SHA-256 Orig: {item.originalSha256.slice(0, 16)}...
            </span>
          )}

          {isDone && item.downloadPdfUrl && (
            <a
              href={item.downloadPdfUrl}
              download
              className="inline-flex h-[26px] items-center gap-1 rounded-[8px] border border-[#0e5a43] bg-[#0e5a43] px-2.5 text-[12px] font-semibold text-white transition-all duration-200 hover:bg-[#0b4a37] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            >
              <Download className="h-3 w-3" aria-hidden="true" />
              <span>PDF</span>
            </a>
          )}

          {isDone && item.downloadReportUrl && (
            <a
              href={item.downloadReportUrl}
              download
              className="inline-flex h-[26px] items-center gap-1 rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            >
              <FileCheck className="h-3 w-3 text-[#0e5a43]" aria-hidden="true" />
              <span>Relatório</span>
            </a>
          )}

          <button
            type="button"
            onClick={() => onRemove(item.clientId)}
            title="Remover documento da lista"
            aria-label={`Remover ${item.fileName}`}
            className="inline-flex h-7 w-7 items-center justify-center rounded-[8px] border border-[#e2e8f0] bg-white text-[#475569] transition-all duration-200 hover:border-[#fecaca] hover:bg-[#fef2f2] hover:text-[#991b1b] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>

      {/* Linha 2: Barra de Progresso Fina + Texto Compacto de Etapa/Páginas */}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={isDeterminate && clampedPercent !== null ? clampedPercent : undefined}
          aria-valuetext={activeStageText}
          className="h-[6px] min-w-[140px] flex-1 overflow-hidden rounded-full bg-[#e2e8f0]"
        >
          {isError ? (
            <div className="h-full w-full rounded-full bg-[#dc2626]" />
          ) : isDeterminate && clampedPercent !== null ? (
            <div
              className="h-full rounded-full bg-[#0e5a43] transition-[width] duration-200"
              style={{ width: `${clampedPercent}%` }}
            />
          ) : (
            <div className="progress-indeterminate-bar h-full rounded-full bg-[#0e5a43]" />
          )}
        </div>

        <div
          className="flex flex-wrap items-center gap-1.5 text-[12px] text-[#475569]"
          aria-live="polite"
        >
          <span
            className={cn(
              "font-medium",
              isError ? "text-[#b91c1c]" : isDone ? "text-[#065f46]" : "text-[#475569]"
            )}
          >
            Etapa {item.stageNumber}/6: {activeStageText}
          </span>
          <span aria-hidden="true">•</span>
          <span className="font-mono tabular-nums text-[#475569]">
            {progressSummaryText}
          </span>
        </div>
      </div>

      {/* Mini-Pipeline Ultracompacta das 6 Etapas Reais */}
      <ol className="mt-2.5 grid grid-cols-3 gap-1 sm:grid-cols-6">
        {STAGE_STEPS.map((st) => {
          const completed = !isError && (isDone || st.num < item.stageNumber);
          const current = !isError && !isDone && st.num === item.stageNumber;
          return (
            <li
              key={st.num}
              aria-current={current ? "step" : undefined}
              className={cn(
                "truncate rounded-[6px] px-1.5 py-0.5 text-center text-[10px] transition-colors duration-200",
                completed && "bg-[#ecfdf5] font-medium text-[#065f46]",
                current && "bg-[#fef3c7] font-semibold text-[#451a03]",
                !completed && !current && "bg-[#f8fafc] text-[#475569]"
              )}
            >
              {st.num}. {st.short}
            </li>
          );
        })}
      </ol>

      {/* Erro claro + Botão Tentar Novamente */}
      {isError && (
        <div
          role="alert"
          className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-[#fecaca] bg-[#fef2f2] px-3 py-2 text-[12px] text-[#991b1b]"
        >
          <span className="min-w-0 flex-1">
            {item.errorMessage || "Não foi possível concluir o processamento deste documento."}
          </span>
          <button
            type="button"
            onClick={() => onRetry(item.clientId)}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-[8px] border border-[#fecaca] bg-white px-2.5 text-[12px] font-semibold text-[#991b1b] transition-all duration-200 hover:bg-[#fef2f2] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
          >
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
            <span>Tentar novamente</span>
          </button>
        </div>
      )}
    </article>
  );
}
