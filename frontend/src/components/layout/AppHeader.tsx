"use client";

import React from "react";
import { Check, FilePlus2, Lock, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

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

export interface AppHeaderProps {
  activeStepNumber: number;
  hasActiveDoc: boolean;
  documentsCount: number;
  showAddFilesDropzone: boolean;
  onToggleAddFilesDropzone: () => void;
  onResetAll: () => void;
}

export function AppHeader({
  activeStepNumber,
  hasActiveDoc,
  documentsCount,
  showAddFilesDropzone,
  onToggleAddFilesDropzone,
  onResetAll,
}: AppHeaderProps) {
  return (
    <header className="bg-brand-header text-white">
      {/* Faixa Institucional UFAM — Responsiva em Mobile sem Colisão */}
      <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-3 px-4 py-3 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <div
            aria-hidden="true"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-white/30 bg-white/10 text-xs font-bold tracking-wider text-white"
          >
            UFAM
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] font-bold leading-snug tracking-tight text-white sm:text-[17px]">
              Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM
            </h1>
            <p className="mt-0.5 text-[11px] leading-snug text-emerald-100 sm:text-xs">
              Universidade Federal do Amazonas • LGPD (Lei nº 13.709/2018) e LAI (Lei nº 12.527/2011)
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 lg:shrink-0">
          <span
            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-white/25 bg-white/10 px-3 text-xs font-semibold text-white"
            title="Todo o processamento de OCR ONNX, NLP spaCy e Redação Física PyMuPDF ocorre em servidor institucional isolado, sem envio para nuvem externa."
          >
            <Lock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>Processamento Local Seguro • Zero-PII</span>
          </span>

          {hasActiveDoc && (
            <Button
              variant="header-ghost"
              size="sm"
              aria-expanded={showAddFilesDropzone}
              onClick={onToggleAddFilesDropzone}
            >
              <FilePlus2 className="h-3.5 w-3.5" aria-hidden="true" />
              <span>
                {showAddFilesDropzone ? "Ocultar Anexação" : "Anexar Mais PDFs"}
              </span>
            </Button>
          )}

          {documentsCount > 0 && (
            <Button variant="header-solid" size="sm" onClick={onResetAll}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Limpar Fila</span>
            </Button>
          )}
        </div>
      </div>

      {/* Stepper Horizontal Compacto de 7 Etapas (Baixa Carga Cognitiva) */}
      <nav
        aria-label="Etapas do fluxo de tarjamento"
        className="w-full border-b border-line-subtle bg-surface-card text-ink-primary"
      >
        <div className="mx-auto w-full max-w-[1400px] px-4 py-2 sm:px-6 lg:px-8">
          <ol className="grid w-full grid-cols-2 gap-1.5 sm:grid-cols-4 lg:grid-cols-7">
            {WORKFLOW_STEPS.map((step) => {
              const isCompleted = step.number < activeStepNumber;
              const isCurrent = step.number === activeStepNumber;

              return (
                <li
                  key={step.number}
                  aria-current={isCurrent ? "step" : undefined}
                  title={`${step.label} — ${step.shortDesc}`}
                  className={cn(
                    "flex min-w-0 items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition-colors duration-150",
                    isCurrent &&
                      "border-amber-400 bg-amber-50 text-amber-950",
                    isCompleted &&
                      "border-brand-border bg-brand-subtle text-brand-ink",
                    !isCurrent &&
                      !isCompleted &&
                      "border-line-subtle bg-surface-muted text-ink-muted"
                  )}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold tabular-nums",
                      isCompleted && "bg-brand-primary text-white",
                      isCurrent && "bg-amber-500 text-amber-950",
                      !isCurrent &&
                        !isCompleted &&
                        "border border-line-strong bg-surface-card text-ink-muted"
                    )}
                  >
                    {isCompleted ? (
                      <Check className="h-3 w-3" />
                    ) : (
                      step.number
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="block truncate font-semibold leading-tight">
                      {step.label}
                    </span>
                    <span className="hidden truncate text-[10px] leading-tight text-current opacity-85 xl:block">
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
  );
}
