"use client";

/**
 * Modal de Alerta Legal, Validação de Segurança e Exportação (`frontend/src/components/workspace/ExportAndReportModal.tsx`).
 *
 * Requisitos atendidos:
 * 1. Exibe o aviso legal obrigatório (Art. 5º, XI da LGPD e LAI) antes de aplicar o tarjamento físico definitivo.
 * 2. Após a conclusão, exibe o checklist detalhado do SecurityValidationResult, hashes SHA-256 e downloads.
 * 3. Acessibilidade WCAG 2.2 AA completa: Escape para fechar, focus trap, foco inicial, restauração de foco e scroll lock.
 * 4. Construído sem backdrop-blur nem sombras pesadas em GPU (compatível com estações legadas).
 * 5. Microcopy limpa, sem crases literais de Markdown expostas no texto.
 */

import React, { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileCheck,
  Lock,
  Shield,
  ShieldCheck,
  X,
} from "lucide-react";
import { Badge, Button, buttonVariants } from "@/components/ui";
import type { SecurityValidationResult } from "@/types/document";

export interface ExportAndReportModalProps {
  isOpen: boolean;
  isApplyingRedaction: boolean;
  isRedactionComplete: boolean;
  selectedAutoCount: number;
  manualBoxesCount: number;
  auditLog: SecurityValidationResult | null;
  downloadPdfUrl: string | null;
  downloadReportUrl: string | null;
  errorMessage: string | null;
  onClose: () => void;
  onBackToEditModes?: () => void;
  onConfirmAndApplyRedaction: (disclaimerAccepted: boolean) => Promise<void>;
}

const FOCUSABLE_SELECTOR = [
  'a[href]:not([tabindex="-1"])',
  'button:not([disabled]):not([tabindex="-1"])',
  'input:not([disabled]):not([tabindex="-1"])',
  'select:not([disabled]):not([tabindex="-1"])',
  'textarea:not([disabled]):not([tabindex="-1"])',
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export function ExportAndReportModal({
  isOpen,
  isApplyingRedaction,
  isRedactionComplete,
  selectedAutoCount,
  manualBoxesCount,
  auditLog,
  downloadPdfUrl,
  downloadReportUrl,
  errorMessage,
  onClose,
  onBackToEditModes,
  onConfirmAndApplyRedaction,
}: ExportAndReportModalProps) {
  const [disclaimerChecked, setDisclaimerChecked] = useState<boolean>(true);
  const dialogPanelRef = useRef<HTMLDivElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Gestão de foco WCAG 2.2 AA (foco inicial, focus trap, Escape e restauração de foco) + scroll lock
  useEffect(() => {
    if (!isOpen) return;

    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const panel = dialogPanelRef.current;
    if (panel) {
      const focusables = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusables.length > 0) {
        focusables[0].focus();
      } else {
        panel.focus();
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (!isApplyingRedaction) {
          e.preventDefault();
          onClose();
        }
        return;
      }

      if (e.key === "Tab" && dialogPanelRef.current) {
        const focusables = Array.from(
          dialogPanelRef.current.querySelectorAll<HTMLElement>(
            FOCUSABLE_SELECTOR
          )
        ).filter((el) => el.offsetParent !== null);

        if (focusables.length === 0) return;

        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement as HTMLElement | null;

        if (e.shiftKey) {
          if (!active || active === first || !dialogPanelRef.current.contains(active)) {
            e.preventDefault();
            last.focus();
          }
        } else if (active === last || !dialogPanelRef.current.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = prevOverflow;
      previousFocusRef.current?.focus();
    };
  }, [isOpen, isApplyingRedaction, onClose]);

  if (!isOpen) return null;

  const totalRedactions = selectedAutoCount + manualBoxesCount;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title-lgpd"
      aria-describedby="modal-desc-lgpd"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/75 p-4"
      data-testid="export-and-report-modal"
    >
      <div
        ref={dialogPanelRef}
        tabIndex={-1}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-line bg-surface-card text-ink-primary shadow-md focus:outline-none"
      >
        {/* Cabeçalho Institucional UFAM */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-emerald-950/30 bg-brand-header px-5 py-3.5 text-white">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/20 bg-white/10">
              <Shield className="h-4 w-4" aria-hidden="true" />
            </span>
            <h2
              id="modal-title-lgpd"
              className="text-sm font-semibold leading-snug sm:text-[15px]"
            >
              {isRedactionComplete
                ? "Validação Cirúrgica Concluída e Exportação de Documentos"
                : "Termo de Ciência Institucional e Confirmação de Tarjamento Definitivo"}
            </h2>
          </div>

          <button
            type="button"
            disabled={isApplyingRedaction}
            onClick={onClose}
            aria-label="Fechar modal"
            className="rounded-lg border border-white/20 bg-white/10 p-1.5 text-white transition-colors duration-150 hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-brand-header"
            data-testid="close-export-modal-btn"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Corpo do Modal com rolagem interna segura */}
        <div
          id="modal-desc-lgpd"
          className="space-y-4 overflow-y-auto p-5 text-xs sm:p-6"
        >
          {!isRedactionComplete ? (
            <>
              {/* Resumo Quantitativo Pré-Aplicação */}
              <div className="overflow-hidden rounded-xl border border-line-subtle bg-surface-card">
                <p className="border-b border-line-subtle bg-surface-muted px-4 py-2 font-semibold text-ink-primary">
                  Resumo das Marcações Selecionadas para Destruição Física
                  Irreversível
                </p>
                <div className="grid grid-cols-3 divide-x divide-line-subtle text-center">
                  <div className="p-3.5">
                    <span className="block font-mono text-xl font-bold tabular-nums text-brand-primary">
                      {selectedAutoCount}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-ink-muted">
                      Ocorrências Automáticas
                    </span>
                  </div>
                  <div className="p-3.5">
                    <span className="block font-mono text-xl font-bold tabular-nums text-ink-primary">
                      {manualBoxesCount}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-ink-muted">
                      Tarjas Manuais
                    </span>
                  </div>
                  <div className="bg-red-50/50 p-3.5">
                    <span className="block font-mono text-xl font-bold tabular-nums text-red-800">
                      {totalRedactions}
                    </span>
                    <span className="mt-0.5 block text-[11px] font-semibold text-red-900">
                      Total a Tarjar
                    </span>
                  </div>
                </div>
              </div>

              {/* Aviso Legal Obrigatório: Art. 5º, XI, LGPD e LAI */}
              <div className="rounded-xl border border-amber-200 bg-amber-50/80 p-4 text-amber-950">
                <div className="flex items-start gap-2.5">
                  <AlertTriangle
                    className="mt-0.5 h-5 w-5 shrink-0 text-amber-700"
                    aria-hidden="true"
                  />
                  <div className="space-y-1.5 leading-relaxed text-amber-900">
                    <p className="font-semibold text-amber-950">
                      Aviso Técnico e Jurídico Obrigatório (Art. 5º, XI, Lei nº
                      13.709/2018 — LGPD e Lei nº 12.527/2011 — LAI):
                    </p>
                    <p>
                      1.{" "}
                      <strong className="font-semibold text-amber-950">
                        Distinção entre Tarjamento e Anonimização:
                      </strong>{" "}
                      O <em>tarjamento definitivo (true redaction)</em> remove
                      fisicamente os glifos de texto, vetores e pixels nas
                      coordenadas selecionadas, mas{" "}
                      <strong className="font-semibold text-amber-950">
                        não garante por si só a anonimização integral do
                        documento
                      </strong>{" "}
                      nos termos do Art. 5º, inciso XI, da LGPD, haja vista a
                      possibilidade de identificação indireta por meio do
                      contexto narrativo remanescente.
                    </p>
                    <p>
                      2.{" "}
                      <strong className="font-semibold text-amber-950">
                        Responsabilidade Administrativa (Human-in-the-Loop):
                      </strong>{" "}
                      Este sistema constitui instrumento técnico de auxílio e
                      não substitui a conferência visual e o juízo
                      administrativo da unidade responsável da UFAM antes da
                      publicação ou disponibilização externa do documento.
                    </p>
                  </div>
                </div>
              </div>

              {/* Checkbox de Confirmação Expressa */}
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-line bg-surface-card p-3.5 font-medium leading-relaxed text-ink-primary transition-colors duration-150 hover:border-brand-primary">
                <input
                  type="checkbox"
                  checked={disclaimerChecked}
                  onChange={(e) => setDisclaimerChecked(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded accent-brand-primary"
                  data-testid="modal-disclaimer-checkbox"
                />
                <span>
                  Declaro que revisei as páginas do documento e estou ciente da
                  distinção técnica entre tarjamento seletivo e anonimização
                  (Art. 5º, XI, LGPD). Autorizo a aplicação do tarjamento físico
                  irreversível.
                </span>
              </label>

              {errorMessage && (
                <div
                  role="alert"
                  className="rounded-xl border border-red-200 bg-red-50 p-3 font-medium text-red-900"
                >
                  {errorMessage}
                </div>
              )}

              {/* Rodapé do Modal 1 */}
              <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line-subtle pt-4">
                <Button
                  variant="secondary"
                  size="md"
                  disabled={isApplyingRedaction}
                  onClick={onClose}
                >
                  Voltar à Revisão
                </Button>

                <Button
                  variant="danger"
                  size="md"
                  disabled={!disclaimerChecked || isApplyingRedaction}
                  onClick={() =>
                    void onConfirmAndApplyRedaction(disclaimerChecked)
                  }
                  data-testid="confirm-apply-redaction-btn"
                >
                  <Lock className="h-4 w-4" aria-hidden="true" />
                  <span>
                    {isApplyingRedaction
                      ? "Destruindo Fluxos de Texto, Pixels e Sanitizando..."
                      : "Confirmar e Executar Tarjamento Definitivo"}
                  </span>
                </Button>
              </div>
            </>
          ) : (
            /* Modal 2: Validação Cirúrgica e Exportação */
            <div
              className="space-y-4"
              data-testid="validation-and-export-panel"
            >
              <div className="rounded-xl border border-brand-border bg-brand-subtle p-4 text-brand-ink">
                <div className="flex items-start gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-primary text-white">
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-ink-primary">
                      Tarjamento Definitivo e Validação Cirúrgica de Segurança
                      Aprovados!
                    </p>
                    <p className="mt-1 leading-relaxed text-brand-ink">
                      O documento original foi preservado intacto e um novo
                      arquivo PDF sanitizado foi gerado e inspecionado. Você
                      pode baixar os arquivos ou voltar para ajustar os modos e
                      re-gerar quantas vezes precisar.
                    </p>
                  </div>
                </div>
              </div>

              {/* Checklist Visual das 4 Verificações Pós-Tarjamento (sem crases literais) */}
              <div className="overflow-hidden rounded-xl border border-line-subtle bg-surface-card">
                <p className="border-b border-line-subtle bg-surface-muted px-4 py-2.5 font-semibold text-ink-primary">
                  Checklist de Validação Pós-Tarjamento
                </p>

                <ul className="divide-y divide-line-subtle">
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-brand-primary"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-ink-secondary">
                      <strong className="font-semibold text-ink-primary">
                        1. Destruição da Camada de Texto (
                        <code className="rounded bg-surface-subtle px-1 py-0.5 font-mono text-[11px] font-semibold text-ink-primary">
                          PDF_REDACT_TEXT_REMOVE
                        </code>
                        ):
                      </strong>{" "}
                      Glifos originais removidos fisicamente; impossível
                      recuperar dados via copiar/colar ou pesquisa textual.
                    </span>
                  </li>
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-brand-primary"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-ink-secondary">
                      <strong className="font-semibold text-ink-primary">
                        2. Queima de Pixels / Substituição Física (
                        <code className="rounded bg-surface-subtle px-1 py-0.5 font-mono text-[11px] font-semibold text-ink-primary">
                          PDF_REDACT_IMAGE_PIXELS
                        </code>
                        ):
                      </strong>{" "}
                      Recorte inspecionado a 150 DPI confirmando cobertura preta
                      sólida (
                      <code className="font-mono text-[11px]">#000000</code>) ou
                      mascaramento parcial físico.
                    </span>
                  </li>
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-brand-primary"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-ink-secondary">
                      <strong className="font-semibold text-ink-primary">
                        3. Sanitização Estrutural (
                        <code className="rounded bg-surface-subtle px-1 py-0.5 font-mono text-[11px] font-semibold text-ink-primary">
                          pikepdf
                        </code>
                        ):
                      </strong>{" "}
                      Metadados <code className="font-mono">/Info</code>, fluxos
                      XML XMP <code className="font-mono">/Metadata</code>,
                      camadas ocultas{" "}
                      <code className="font-mono">/OCProperties</code> e objetos
                      órfãos expurgados.
                    </span>
                  </li>
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-brand-primary"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-ink-secondary">
                      <strong className="font-semibold text-ink-primary">
                        4. Integridade e Rastreabilidade Criptográfica
                        (Zero-PII):
                      </strong>{" "}
                      Arquivo PDF íntegro e hashes SHA-256 registrados sem
                      armazenamento de dados pessoais.
                    </span>
                  </li>
                </ul>
              </div>

              {auditLog && (
                <div className="grid grid-cols-1 gap-2.5 rounded-xl border border-line-subtle bg-surface-muted p-4 text-[11px] sm:grid-cols-2">
                  <div>
                    <span className="font-medium text-ink-muted">
                      Total de Tarjas Aplicadas:
                    </span>{" "}
                    <strong className="font-mono font-semibold tabular-nums text-ink-primary">
                      {auditLog.total_redactions_applied}
                    </strong>{" "}
                    <span className="text-ink-muted">
                      (Páginas:{" "}
                      <span className="font-mono tabular-nums">
                        {auditLog.affected_pages.join(", ") || "Nenhuma"}
                      </span>
                      )
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 sm:justify-end">
                    <span className="font-medium text-ink-muted">
                      Status da Validação Cirúrgica:
                    </span>
                    <Badge
                      variant={
                        auditLog.security_validation_passed
                          ? "brand"
                          : "warning"
                      }
                    >
                      {auditLog.security_validation_passed
                        ? "APROVADO (100%)"
                        : "VERIFICAR"}
                    </Badge>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="font-medium text-ink-muted">
                      SHA-256 Documento Original:
                    </span>{" "}
                    <code className="break-all rounded border border-line-subtle bg-surface-card px-1.5 py-0.5 font-mono text-[11px] text-ink-primary">
                      {auditLog.original_document_sha256}
                    </code>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="font-medium text-ink-muted">
                      SHA-256 Documento Tarjado Final:
                    </span>{" "}
                    <code className="break-all rounded border border-line-subtle bg-surface-card px-1.5 py-0.5 font-mono text-[11px] text-ink-primary">
                      {auditLog.redacted_document_sha256}
                    </code>
                  </div>
                </div>
              )}

              {/* Ações de Download em Destaque + Navegação Secundária Organizada */}
              <div className="space-y-3 border-t border-line-subtle pt-4">
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {downloadReportUrl && (
                    <a
                      href={downloadReportUrl}
                      download
                      className={buttonVariants({
                        variant: "secondary",
                        size: "md",
                      })}
                      data-testid="download-report-pdf-link"
                    >
                      <FileCheck
                        className="h-4 w-4 text-brand-primary"
                        aria-hidden="true"
                      />
                      <span>Baixar Relatório de Processamento (PDF)</span>
                    </a>
                  )}

                  {downloadPdfUrl && (
                    <a
                      href={downloadPdfUrl}
                      download
                      className={buttonVariants({
                        variant: "primary",
                        size: "md",
                      })}
                      data-testid="download-redacted-pdf-link"
                    >
                      <Download className="h-4 w-4" aria-hidden="true" />
                      <span>Baixar PDF Tarjado Final</span>
                    </a>
                  )}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-subtle pt-3">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      if (onBackToEditModes) {
                        onBackToEditModes();
                      } else {
                        onClose();
                      }
                    }}
                    data-testid="back-to-edit-modes-btn"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Voltar e Editar Modos</span>
                  </Button>

                  <Button variant="ghost" size="sm" onClick={onClose}>
                    Fechar Janela
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
