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
import { Badge, Button, buttonVariants } from "@/components/ui";
import type { BadgeVariant } from "@/components/ui";
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
  compact?: boolean;
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

function getBadgeVariant(status: DocumentBadgeStatus): BadgeVariant {
  switch (status) {
    case "Aguardando":
      return "neutral";
    case "Carregando":
      return "info";
    case "Processando":
      return "warning";
    case "Concluído":
      return "brand";
    case "Erro":
      return "danger";
  }
}

export function DocumentProgressCard({
  item,
  isActive,
  compact = false,
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

  // Progressive disclosure: oculta mini-pipeline redundante quando já pronto no workspace
  const showMiniPipeline = !(compact && (isWaitingHumanReview || isDone));

  return (
    <article
      aria-label={`Progresso do documento ${item.fileName}`}
      aria-busy={isInFlight && !isWaitingHumanReview}
      className={cn(
        "rounded-xl border bg-surface-card shadow-xs transition-colors duration-150",
        compact ? "px-4 py-3" : "px-4 py-3.5 sm:px-5",
        isError
          ? "border-red-200 bg-red-50/40"
          : isActive
          ? "border-brand-primary"
          : "border-line-subtle hover:border-line"
      )}
    >
      {/* Linha 1: Identificação do arquivo + Status + Ações */}
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-2 sm:flex-1">
          <div className="flex min-w-0 max-w-full items-center gap-2">
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border",
                isError
                  ? "border-red-200 bg-red-50 text-red-700"
                  : "border-line-subtle bg-surface-muted text-brand-primary"
              )}
            >
              <FileText className="h-3.5 w-3.5" aria-hidden="true" />
            </span>

            <span
              className="min-w-0 truncate text-sm font-semibold text-ink-primary sm:max-w-[340px]"
              title={item.fileName}
            >
              {item.fileName}
            </span>
          </div>

          <span className="shrink-0 font-mono text-[11px] tabular-nums text-ink-muted">
            {formatFileSize(item.fileSizeBytes)}
          </span>

          <Badge variant={getBadgeVariant(item.badgeStatus)}>
            {isDone && <CheckCircle2 className="h-3 w-3" aria-hidden="true" />}
            {isError && <AlertCircle className="h-3 w-3" aria-hidden="true" />}
            {item.badgeStatus === "Aguardando" && (
              <Clock className="h-3 w-3" aria-hidden="true" />
            )}
            {isInFlight && (
              <Loader2
                className="h-3 w-3 motion-safe:animate-spin"
                aria-hidden="true"
              />
            )}
            <span>{item.badgeStatus}</span>
          </Badge>

          {item.isReadyForReview && !isError && (
            <button
              type="button"
              onClick={() => onSelectForReview(item.clientId)}
              aria-pressed={isActive}
              className={cn(
                "inline-flex items-center gap-1 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-2",
                isActive
                  ? "h-6 rounded-full border border-brand-border bg-brand-subtle px-2.5 text-brand-ink"
                  : "h-7 rounded-lg border border-line bg-surface-card px-2.5 text-ink-primary hover:bg-surface-subtle"
              )}
            >
              <Eye className="h-3 w-3" aria-hidden="true" />
              <span>{isActive ? "Em Revisão" : "Revisar"}</span>
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {isActive && item.originalSha256 && (
            <span
              className="rounded-md bg-surface-subtle px-2 py-0.5 font-mono text-[11px] tabular-nums text-ink-secondary"
              title={`SHA-256 original completo: ${item.originalSha256}`}
            >
              SHA-256 Orig: {item.originalSha256.slice(0, 16)}...
            </span>
          )}

          {isDone && item.downloadPdfUrl && (
            <a
              href={item.downloadPdfUrl}
              download
              className={buttonVariants({ variant: "primary", size: "xs" })}
            >
              <Download className="h-3 w-3" aria-hidden="true" />
              <span>PDF</span>
            </a>
          )}

          {isDone && item.downloadReportUrl && (
            <a
              href={item.downloadReportUrl}
              download
              className={buttonVariants({ variant: "secondary", size: "xs" })}
            >
              <FileCheck
                className="h-3 w-3 text-brand-primary"
                aria-hidden="true"
              />
              <span>Relatório</span>
            </a>
          )}

          <Button
            variant="ghost"
            size="icon"
            onClick={() => onRemove(item.clientId)}
            title="Remover documento da lista"
            aria-label={`Remover ${item.fileName}`}
            className="border border-line-subtle bg-surface-card text-ink-muted hover:border-red-200 hover:bg-red-50 hover:text-red-800"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </div>

      {/* Linha 2: Barra de Progresso Fina + Resumo Honesto de Etapa/Páginas */}
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <div
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={
            isDeterminate && clampedPercent !== null ? clampedPercent : undefined
          }
          aria-valuetext={activeStageText}
          className="h-1.5 min-w-[120px] flex-1 overflow-hidden rounded-full bg-surface-canvas"
        >
          {isError ? (
            <div className="h-full w-full rounded-full bg-red-600" />
          ) : isDeterminate && clampedPercent !== null ? (
            <div
              className="h-full rounded-full bg-brand-primary transition-[width] duration-150"
              style={{ width: `${clampedPercent}%` }}
            />
          ) : (
            <div className="progress-indeterminate-bar h-full rounded-full bg-brand-primary" />
          )}
        </div>

        <div
          className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-muted"
          aria-live="polite"
        >
          <span
            className={cn(
              "font-medium",
              isError
                ? "text-red-800"
                : isDone
                ? "text-brand-ink"
                : "text-ink-secondary"
            )}
          >
            Etapa {item.stageNumber}/6: {activeStageText}
          </span>
          <span aria-hidden="true">•</span>
          <span className="font-mono tabular-nums text-ink-muted">
            {progressSummaryText}
          </span>
        </div>
      </div>

      {/* Mini-Pipeline das 6 Etapas (exibida durante processamento ativo) */}
      {showMiniPipeline && (
        <ol className="mt-2 grid grid-cols-3 gap-1 sm:grid-cols-6">
          {STAGE_STEPS.map((st) => {
            const completed = !isError && (isDone || st.num < item.stageNumber);
            const current = !isError && !isDone && st.num === item.stageNumber;
            return (
              <li
                key={st.num}
                aria-current={current ? "step" : undefined}
                className={cn(
                  "truncate rounded-md px-1.5 py-0.5 text-center text-[10px] transition-colors duration-150",
                  completed && "bg-brand-subtle font-medium text-brand-ink",
                  current && "bg-amber-100 font-semibold text-amber-950",
                  !completed && !current && "bg-surface-muted text-ink-muted"
                )}
              >
                {st.num}. {st.short}
              </li>
            );
          })}
        </ol>
      )}

      {/* Erro claro + Botão Tentar Novamente */}
      {isError && (
        <div
          role="alert"
          className="mt-2.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900"
        >
          <span className="min-w-0 flex-1">
            {item.errorMessage ||
              "Não foi possível concluir o processamento deste documento."}
          </span>
          <Button
            variant="danger-outline"
            size="xs"
            onClick={() => onRetry(item.clientId)}
          >
            <RotateCcw className="h-3 w-3" aria-hidden="true" />
            <span>Tentar novamente</span>
          </Button>
        </div>
      )}
    </article>
  );
}
