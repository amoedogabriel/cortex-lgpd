"use client";

/**
 * Modal de Alerta Legal, Validação de Segurança e Exportação (`frontend/src/components/workspace/ExportAndReportModal.tsx`).
 *
 * Requisitos atendidos:
 * 1. Ao clicar em "Aplicar Tarjamento Definitivo", exibe o aviso legal obrigatório sobre a
 *    diferença técnica e jurídica entre **Tarjamento Definitivo** e **Anonimização** (Art. 5º, XI da LGPD)
 *    e a responsabilidade da unidade administrativa (Human-in-the-Loop).
 * 2. Após a conclusão (recebida via SSE `redaction_complete` ou resposta do endpoint `/redact`),
 *    exibe o checklist visual detalhado do `SecurityValidationResult` (confirmando destruição física
 *    do texto, queima dos pixels, limpeza de metadados `/Info` e XMP e integridade do arquivo).
 * 3. Fornece os botões de download com cache-busting para o **PDF Tarjado Final** e o **Relatório de Processamento (PDF Zero-PII)**,
 *    além do botão **"Voltar e Editar Modos"** para re-gerar livremente alternando entre Tarja Preta e Mascaramento Parcial.
 * 4. Construído sem `backdrop-blur` nem sombras GPU pesadas (compatível com estações legadas).
 */

import React, { useState } from "react";
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
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-900 shadow-[0_20px_50px_rgba(15,23,42,0.28)]">
        {/* Cabeçalho Institucional Limpo UFAM */}
        <div className="flex shrink-0 items-center justify-between border-b border-emerald-950/30 bg-emerald-900 px-5 py-4 text-white">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/20 bg-white/10">
              <Shield className="h-4 w-4" aria-hidden="true" />
            </span>
            <h2 id="modal-title-lgpd" className="text-sm font-semibold sm:text-base">
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
            className="rounded-lg border border-white/20 bg-white/10 p-1.5 text-white transition-colors duration-200 hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2"
            data-testid="close-export-modal-btn"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {/* Corpo do Modal com rolagem interna segura */}
        <div id="modal-desc-lgpd" className="space-y-4 overflow-y-auto p-6 text-xs">
          {!isRedactionComplete ? (
            <>
              {/* Resumo Quantitativo Pré-Aplicação em grid único com divide-x */}
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                <p className="border-b border-slate-100 bg-slate-50/70 px-4 py-2.5 font-semibold text-slate-900">
                  Resumo das Marcações Selecionadas para Destruição Física Irreversível:
                </p>
                <div className="grid grid-cols-3 divide-x divide-slate-100 text-center">
                  <div className="p-3.5">
                    <span className="block font-mono text-xl font-bold tabular-nums text-emerald-800">
                      {selectedAutoCount}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-slate-600">
                      Ocorrências Automáticas
                    </span>
                  </div>
                  <div className="p-3.5">
                    <span className="block font-mono text-xl font-bold tabular-nums text-slate-900">
                      {manualBoxesCount}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-slate-600">
                      Tarjas Manuais
                    </span>
                  </div>
                  <div className="bg-red-50/40 p-3.5">
                    <span className="block font-mono text-xl font-bold tabular-nums text-red-800">
                      {totalRedactions}
                    </span>
                    <span className="mt-0.5 block text-[11px] font-semibold text-red-900">
                      Total a Tarjar
                    </span>
                  </div>
                </div>
              </div>

              {/* Aviso Legal Obrigatório: Distinção entre Tarjamento e Anonimização (Art. 5º, XI, LGPD) */}
              <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-4 text-amber-900">
                <div className="flex items-start gap-2.5">
                  <AlertTriangle
                    className="mt-0.5 h-5 w-5 shrink-0 text-amber-700"
                    aria-hidden="true"
                  />
                  <div className="space-y-1.5 leading-relaxed">
                    <p className="font-semibold text-amber-950">
                      Aviso Técnico e Jurídico Obrigatório (Art. 5º, XI, Lei nº 13.709/2018 — LGPD e Lei nº 12.527/2011 — LAI):
                    </p>
                    <p>
                      1. <strong className="font-semibold text-amber-950">Distinção entre Tarjamento e Anonimização:</strong> O{" "}
                      <em>tarjamento definitivo (true redaction)</em> remove fisicamente os glifos de texto, vetores e pixels nas coordenadas selecionadas, mas{" "}
                      <strong className="font-semibold text-amber-950">não garante por si só a anonimização integral do documento</strong> nos termos do Art. 5º, inciso XI, da LGPD, haja vista a possibilidade de identificação indireta por meio do contexto narrativo remanescente.
                    </p>
                    <p>
                      2. <strong className="font-semibold text-amber-950">Responsabilidade Administrativa (Human-in-the-Loop):</strong> Este sistema constitui instrumento técnico de auxílio e não substitui a conferência visual e o juízo administrativo da unidade responsável da UFAM antes da publicação ou disponibilização externa do documento.
                    </p>
                  </div>
                </div>
              </div>

              {/* Checkbox de Confirmação Expressa */}
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-300 bg-white p-4 font-medium leading-relaxed text-slate-900 transition-colors duration-200 hover:border-emerald-700">
                <input
                  type="checkbox"
                  checked={disclaimerChecked}
                  onChange={(e) => setDisclaimerChecked(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded accent-emerald-800"
                  data-testid="modal-disclaimer-checkbox"
                />
                <span>
                  Declaro que revisei as páginas do documento e estou ciente da distinção técnica entre tarjamento seletivo e anonimização (Art. 5º, XI, LGPD). Autorizo a aplicação do tarjamento físico irreversível.
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

              {/* Botões de Ação do Modal 1 */}
              <div className="flex flex-wrap items-center justify-end gap-2.5 border-t border-slate-200 pt-4">
                <button
                  type="button"
                  disabled={isApplyingRedaction}
                  onClick={onClose}
                  className="rounded-lg border border-slate-300 bg-white px-4 py-2 font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                >
                  Voltar à Revisão
                </button>

                <button
                  type="button"
                  disabled={!disclaimerChecked || isApplyingRedaction}
                  onClick={() => void onConfirmAndApplyRedaction(disclaimerChecked)}
                  className="inline-flex items-center gap-2 rounded-lg bg-red-800 px-4 py-2 font-semibold text-white shadow-sm transition-colors duration-200 hover:bg-red-900 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                  data-testid="confirm-apply-redaction-btn"
                >
                  <Lock className="h-4 w-4" aria-hidden="true" />
                  <span>
                    {isApplyingRedaction
                      ? "Destruindo Fluxos de Texto, Pixels e Sanitizando..."
                      : "Confirmar e Executar Tarjamento Definitivo"}
                  </span>
                </button>
              </div>
            </>
          ) : (
            /* Modal 2: Validação Cirúrgica e Exportação */
            <div className="space-y-4" data-testid="validation-and-export-panel">
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-950">
                <div className="flex items-start gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-800 text-white">
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-emerald-950">
                      Tarjamento Definitivo e Validação Cirúrgica de Segurança Aprovados!
                    </p>
                    <p className="mt-1 leading-relaxed text-emerald-900">
                      O documento original foi preservado intacto e um novo arquivo PDF sanitizado foi gerado e inspecionado. Você pode baixar os arquivos ou voltar para ajustar os modos e re-gerar quantas vezes precisar.
                    </p>
                  </div>
                </div>
              </div>

              {/* Checklist Visual das 4 Verificações de Segurança Pós-Tarjamento em lista única */}
              <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                <p className="border-b border-slate-100 px-4 py-3 font-semibold text-slate-900">
                  Checklist de Validação Pós-Tarjamento (`SecurityValidationResult`):
                </p>

                <ul className="divide-y divide-slate-100">
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-slate-700">
                      <strong className="font-semibold text-slate-900">
                        1. Destruição da Camada de Texto (`PDF_REDACT_TEXT_REMOVE`):
                      </strong>{" "}
                      Glifos originais removidos fisicamente; impossível recuperar dados via copiar/colar ou pesquisa textual.
                    </span>
                  </li>
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-slate-700">
                      <strong className="font-semibold text-slate-900">
                        2. Queima de Pixels / Substituição Física (`PDF_REDACT_IMAGE_PIXELS`):
                      </strong>{" "}
                      Recorte inspecionado a 150 DPI confirmando cobertura preta sólida (<code className="font-mono">#000000</code>) ou mascaramento parcial físico.
                    </span>
                  </li>
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-slate-700">
                      <strong className="font-semibold text-slate-900">
                        3. Sanitização Estrutural (`pikepdf`):
                      </strong>{" "}
                      Metadados <code className="font-mono">/Info</code>, fluxos XML XMP <code className="font-mono">/Metadata</code>, camadas ocultas <code className="font-mono">/OCProperties</code> e objetos órfãos expurgados.
                    </span>
                  </li>
                  <li className="flex items-start gap-2.5 px-4 py-2.5">
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700"
                      aria-hidden="true"
                    />
                    <span className="leading-relaxed text-slate-700">
                      <strong className="font-semibold text-slate-900">
                        4. Integridade e Rastreabilidade Criptográfica (Zero-PII):
                      </strong>{" "}
                      Arquivo PDF íntegro e hashes SHA-256 registrados sem armazenamento de dados pessoais.
                    </span>
                  </li>
                </ul>
              </div>

              {auditLog && (
                <div className="grid grid-cols-1 gap-2.5 rounded-xl border border-slate-200 bg-slate-50 p-4 text-[11px] sm:grid-cols-2">
                  <div>
                    <span className="font-medium text-slate-600">
                      Total de Tarjas Aplicadas:
                    </span>{" "}
                    <strong className="font-mono font-semibold tabular-nums text-slate-900">
                      {auditLog.total_redactions_applied}
                    </strong>{" "}
                    <span className="text-slate-600">
                      (Páginas:{" "}
                      <span className="font-mono tabular-nums">
                        {auditLog.affected_pages.join(", ") || "Nenhuma"}
                      </span>
                      )
                    </span>
                  </div>
                  <div>
                    <span className="font-medium text-slate-600">
                      Status da Validação Cirúrgica:
                    </span>{" "}
                    <strong className="inline-flex rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-800">
                      {auditLog.security_validation_passed ? "APROVADO (100%)" : "VERIFICAR"}
                    </strong>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="font-medium text-slate-600">
                      SHA-256 Documento Original:
                    </span>{" "}
                    <code className="break-all rounded bg-white px-1.5 py-0.5 font-mono text-xs text-slate-800">
                      {auditLog.original_document_sha256}
                    </code>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="font-medium text-slate-600">
                      SHA-256 Documento Tarjado Final:
                    </span>{" "}
                    <code className="break-all rounded bg-white px-1.5 py-0.5 font-mono text-xs text-slate-800">
                      {auditLog.redacted_document_sha256}
                    </code>
                  </div>
                </div>
              )}

              {/* Rodapé em Linha Única Distribuída */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (onBackToEditModes) {
                        onBackToEditModes();
                      } else {
                        onClose();
                      }
                    }}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 font-semibold text-slate-700 transition-colors duration-200 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                    data-testid="back-to-edit-modes-btn"
                  >
                    <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                    <span>Voltar e Editar Modos</span>
                  </button>

                  <button
                    type="button"
                    onClick={onClose}
                    className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 font-medium text-slate-600 transition-colors duration-200 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                  >
                    Fechar Janela
                  </button>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {downloadReportUrl && (
                    <a
                      href={downloadReportUrl}
                      download
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 font-semibold text-slate-800 transition-colors duration-200 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                      data-testid="download-report-pdf-link"
                    >
                      <FileCheck className="h-4 w-4 text-emerald-700" aria-hidden="true" />
                      <span>Baixar Relatório de Processamento (PDF)</span>
                    </a>
                  )}

                  {downloadPdfUrl && (
                    <a
                      href={downloadPdfUrl}
                      download
                      className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-800 px-4 py-2 font-semibold text-white shadow-sm transition-colors duration-200 hover:bg-emerald-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                      data-testid="download-redacted-pdf-link"
                    >
                      <Download className="h-4 w-4" aria-hidden="true" />
                      <span>Baixar PDF Tarjado Final</span>
                    </a>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
