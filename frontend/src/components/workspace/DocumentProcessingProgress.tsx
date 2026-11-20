"use client";

import React from "react";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { JobStatus } from "@/types/document";

export interface DocumentProcessingProgressProps {
  status: JobStatus;
  processedPages: number;
  totalPages: number;
  progressPercentage: number | null;
  currentProcessingPage?: number | null;
  isApplyingRedaction?: boolean;
  errorMessage?: string | null;
  onRetry?: () => void;
}

export function DocumentProcessingProgress({
  status,
  processedPages,
  totalPages,
  progressPercentage,
  currentProcessingPage,
  isApplyingRedaction = false,
  errorMessage,
  onRetry,
}: DocumentProcessingProgressProps) {
  const isError = status === "FAILED";
  const isCompleted = status === "COMPLETED";
  const isValidating = status === "VALIDATING";
  const isRedacting =
    !isError &&
    !isCompleted &&
    (isApplyingRedaction || status === "REDACTING" || isValidating);

  const hasKnownPages = totalPages > 0;
  const isStreamingPages =
    !isError &&
    !isCompleted &&
    !isRedacting &&
    hasKnownPages &&
    processedPages < totalPages;
  const isIndeterminateAnalysis =
    !isError &&
    !isCompleted &&
    !isRedacting &&
    !hasKnownPages &&
    status !== "AWAITING_REVIEW";
  const isAnalysisReady =
    !isError &&
    !isCompleted &&
    !isRedacting &&
    !isStreamingPages &&
    !isIndeterminateAnalysis;

  const isBusy = isStreamingPages || isIndeterminateAnalysis || isRedacting;

  const isDeterminate = !isError && !isRedacting && (isCompleted || isAnalysisReady || hasKnownPages);

  const displayPercent = (() => {
    if (!isDeterminate) return null;
    if (isCompleted || isAnalysisReady) return 100;
    const computedFromPages =
      hasKnownPages && processedPages > 0
        ? Math.round((processedPages / totalPages) * 100)
        : 0;
    const raw =
      progressPercentage !== null && Number.isFinite(progressPercentage)
        ? Math.round(progressPercentage)
        : computedFromPages;
    const maxAllowed = isStreamingPages ? 99 : 100;
    return Math.max(0, Math.min(maxAllowed, raw));
  })();

  const safeTotalPages = Math.max(1, totalPages);
  const safeProcessedPages = Math.min(safeTotalPages, Math.max(0, processedPages));
  const honestActivePage = isStreamingPages
    ? currentProcessingPage &&
      currentProcessingPage > safeProcessedPages &&
      currentProcessingPage <= safeTotalPages
      ? currentProcessingPage
      : Math.min(safeTotalPages, safeProcessedPages + 1)
    : null;

  const phaseTitle = isError
    ? "FALHA NO PROCESSAMENTO"
    : isValidating
      ? "VALIDANDO INTEGRIDADE"
      : isRedacting
        ? "APLICANDO TARJAMENTO FÍSICO"
        : isCompleted
          ? "TARJAMENTO CONCLUÍDO"
          : isStreamingPages || isIndeterminateAnalysis
            ? "ANALISANDO DOCUMENTO"
            : "ANÁLISE CONCLUÍDA";

  const ariaValueText = isError
    ? "Falha durante o processamento do documento"
    : isValidating
      ? "Validando integridade Zero-PII do documento"
      : isRedacting
        ? "Aplicando tarjamento físico definitivo"
        : isCompleted
          ? `Tarjamento concluído em ${safeTotalPages} páginas`
          : isStreamingPages
            ? `${safeProcessedPages} de ${safeTotalPages} páginas processadas (${displayPercent ?? 0}%)`
            : isIndeterminateAnalysis
              ? "Preparando análise inicial do documento"
              : `${safeTotalPages} de ${safeTotalPages} páginas prontas para revisão`;

  return (
    <section
      aria-live="polite"
      aria-busy={isBusy}
      className={cn(
        "border-b px-4 py-3.5 transition-colors duration-150",
        isError
          ? "border-red-200 bg-red-50/70"
          : isCompleted
            ? "border-line-subtle bg-brand-subtle/40"
            : isBusy
              ? "border-line-subtle bg-surface-muted"
              : "border-line-subtle bg-surface-card"
      )}
      data-testid="sidebar-sse-progress-banner"
    >
      {/* Linha 1: Estado imediato + Percentual real (tabular-nums) + Status técnico discreto */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {isError ? (
            <AlertCircle
              className="h-3.5 w-3.5 shrink-0 text-red-700"
              aria-hidden="true"
            />
          ) : isBusy ? (
            <Loader2
              className="h-3.5 w-3.5 shrink-0 text-brand-primary motion-safe:animate-spin"
              aria-hidden="true"
            />
          ) : isCompleted ? (
            <ShieldCheck
              className="h-3.5 w-3.5 shrink-0 text-brand-primary"
              aria-hidden="true"
            />
          ) : (
            <CheckCircle2
              className="h-3.5 w-3.5 shrink-0 text-brand-primary"
              aria-hidden="true"
            />
          )}

          <span
            className={cn(
              "truncate text-[11px] font-bold uppercase tracking-wider",
              isError
                ? "text-red-800"
                : isCompleted || isAnalysisReady
                  ? "text-brand-ink"
                  : "text-ink-primary"
            )}
          >
            {phaseTitle}
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {isDeterminate && displayPercent !== null && (
            <span
              className={cn(
                "font-mono text-sm font-bold tabular-nums",
                isCompleted || isAnalysisReady
                  ? "text-brand-ink"
                  : "text-ink-primary"
              )}
              data-testid="processing-progress-percent"
            >
              {displayPercent}%
            </span>
          )}
          <span
            className="rounded border border-line-subtle bg-surface-subtle px-1.5 py-0.5 font-mono text-[10px] font-medium text-ink-muted"
            title={`Status técnico do pipeline: ${status}`}
          >
            {status}
          </span>
        </div>
      </div>

      {/* Linha 2: Barra de progresso horizontal real, fluida e monotônica (apenas width / transform) */}
      <div
        role="progressbar"
        aria-label="Progresso do processamento do documento"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={
          isDeterminate && displayPercent !== null ? displayPercent : undefined
        }
        aria-valuetext={ariaValueText}
        className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface-canvas"
      >
        {isError ? (
          <div className="h-full w-full rounded-full bg-red-600" />
        ) : isDeterminate && displayPercent !== null ? (
          <div
            className="h-full rounded-full bg-brand-primary transition-[width] duration-300 ease-out"
            style={{ width: `${displayPercent}%` }}
          />
        ) : (
          <div className="progress-indeterminate-bar h-full rounded-full bg-brand-primary" />
        )}
      </div>

      {/* Linha 3: Contagem explícita, página em processamento e orientação de revisão parcial */}
      {isStreamingPages && (
        <div className="mt-2 space-y-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5 text-xs">
            <span
              className="font-medium tabular-nums text-ink-primary"
              data-testid="processing-pages-summary"
            >
              {safeProcessedPages} de {safeTotalPages} páginas processadas
            </span>
            {honestActivePage !== null && (
              <span
                className="font-mono text-[11px] tabular-nums text-brand-ink"
                data-testid="processing-current-page"
              >
                Processando página {honestActivePage}...
              </span>
            )}
          </div>
          <p className="text-[11px] leading-relaxed text-ink-muted">
            Revisão parcial liberada — você já pode navegar e ajustar as páginas
            prontas.
          </p>
        </div>
      )}

      {isIndeterminateAnalysis && (
        <p className="mt-2 text-xs text-ink-secondary">
          Preparando estrutura do PDF e extraindo páginas iniciais...
        </p>
      )}

      {isRedacting && (
        <p className="mt-2 text-xs text-ink-secondary">
          {isValidating
            ? "Executando validação cirúrgica Zero-PII e gerando hashes SHA-256..."
            : "Removendo dados pessoais da camada física do PDF..."}
        </p>
      )}

      {isAnalysisReady && (
        <p
          className="mt-2 text-xs tabular-nums text-ink-secondary"
          data-testid="processing-pages-summary"
        >
          {safeTotalPages} de {safeTotalPages} páginas prontas para conferência
          final.
        </p>
      )}

      {isCompleted && (
        <p
          className="mt-2 text-xs font-medium tabular-nums text-brand-ink"
          data-testid="processing-pages-summary"
        >
          PDF tarjado e relatório Zero-PII gerados ({safeTotalPages} páginas).
        </p>
      )}

      {isError && (
        <div
          role="alert"
          className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-200 bg-surface-card px-2.5 py-2 text-xs text-red-900"
        >
          <span className="min-w-0 flex-1">
            {errorMessage ||
              "Não foi possível concluir o processamento deste documento."}
          </span>
          {onRetry && (
            <Button variant="danger-outline" size="xs" onClick={onRetry}>
              <RotateCcw className="h-3 w-3" aria-hidden="true" />
              <span>Tentar novamente</span>
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
