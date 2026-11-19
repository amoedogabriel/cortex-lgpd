"use client";

import React, { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  XCircle,
} from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { VerboseStepLog } from "@/types/document";

export interface ProcessingVerbosePanelProps {
  logs: VerboseStepLog[];
}

/**
 * Painel Discreto e Compacto de Auditoria de Etapas (`verbose-processing-panel`).
 * - Exibe permanentemente o resumo compacto das etapas executadas (Zero-PII) para
 *   transparência institucional e compatibilidade direta com `redaction_workflow.spec.ts`.
 * - Detalhes técnicos completos (mensagens, timestamps e payload JSON) ficam recolhidos
 *   sob demanda (progressive disclosure) para não poluir o workspace.
 */
export function ProcessingVerbosePanel({ logs }: ProcessingVerbosePanelProps) {
  const [showDetails, setShowDetails] = useState<boolean>(false);

  const stageSummary = useMemo(() => {
    const byStage = new Map<string, VerboseStepLog>();
    for (const entry of logs) {
      byStage.set(`${entry.step_number}:${entry.stage}`, entry);
    }
    return Array.from(byStage.values()).sort((a, b) => a.sequence - b.sequence);
  }, [logs]);

  if (logs.length === 0) return null;

  return (
    <Card
      className="overflow-hidden"
      data-testid="verbose-processing-panel"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-subtle bg-surface-muted px-4 py-2">
        <div className="flex items-center gap-2">
          <Activity
            className="h-3.5 w-3.5 text-brand-primary"
            aria-hidden="true"
          />
          <h3 className="text-xs font-semibold text-ink-primary">
            Trilha Técnica de Processamento (Zero-PII)
          </h3>
          <Badge variant="neutral" className="font-mono tabular-nums">
            {stageSummary.length} {stageSummary.length === 1 ? "etapa" : "etapas"}
          </Badge>
        </div>

        <button
          type="button"
          aria-expanded={showDetails}
          aria-controls="verbose-processing-details"
          onClick={() => setShowDetails((prev) => !prev)}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold text-ink-secondary transition-colors duration-150 hover:bg-surface-subtle hover:text-ink-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus"
        >
          {showDetails ? (
            <>
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Ocultar detalhes</span>
            </>
          ) : (
            <>
              <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Ver detalhes ({logs.length})</span>
            </>
          )}
        </button>
      </div>

      {/* Resumo Compacto Sempre Visível das Etapas Executadas */}
      <ol
        aria-label="Resumo das etapas técnicas executadas"
        className="flex flex-wrap items-center gap-1.5 px-4 py-2.5"
      >
        {stageSummary.map((entry) => {
          const isError = entry.level === "ERROR";
          const isWarn = entry.level === "WARNING";
          return (
            <li
              key={entry.id}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium",
                isError
                  ? "border-red-200 bg-red-50 text-red-900"
                  : isWarn
                  ? "border-amber-200 bg-amber-50 text-amber-900"
                  : "border-line-subtle bg-surface-muted text-ink-secondary"
              )}
              title={entry.message}
            >
              {isError ? (
                <XCircle
                  className="h-3 w-3 shrink-0 text-red-700"
                  aria-hidden="true"
                />
              ) : isWarn ? (
                <AlertTriangle
                  className="h-3 w-3 shrink-0 text-amber-700"
                  aria-hidden="true"
                />
              ) : (
                <CheckCircle2
                  className="h-3 w-3 shrink-0 text-brand-primary"
                  aria-hidden="true"
                />
              )}
              <span>{entry.stage_label || entry.stage}</span>
              {typeof entry.duration_ms === "number" && (
                <span className="font-mono text-[10px] tabular-nums text-ink-muted">
                  {Math.round(entry.duration_ms)} ms
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {/* Detalhes Cronológicos sob Demanda (Progressive Disclosure) */}
      {showDetails && (
        <div
          id="verbose-processing-details"
          className="max-h-56 overflow-y-auto border-t border-line-subtle bg-surface-card"
        >
          <ol className="divide-y divide-line-subtle text-[11px]">
            {logs.map((entry) => (
              <li
                key={entry.id}
                className="flex flex-wrap items-start justify-between gap-2 px-4 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold text-ink-primary">
                      {entry.stage_label || entry.stage}
                    </span>
                    <span className="rounded bg-surface-subtle px-1.5 py-0.5 font-mono text-[10px] text-ink-muted">
                      {entry.level}
                    </span>
                  </div>
                  <p className="mt-0.5 leading-relaxed text-ink-secondary">
                    {entry.message}
                  </p>
                </div>
                <div className="shrink-0 text-right font-mono text-[10px] tabular-nums text-ink-muted">
                  {typeof entry.duration_ms === "number" && (
                    <span className="block">{Math.round(entry.duration_ms)} ms</span>
                  )}
                  <span>
                    {new Date(entry.timestamp).toLocaleTimeString("pt-BR")}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}
