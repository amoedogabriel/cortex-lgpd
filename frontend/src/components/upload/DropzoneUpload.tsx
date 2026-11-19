"use client";

import React, { useCallback, useRef, useState } from "react";
import {
  CheckCircle2,
  Lock,
  ShieldAlert,
  Upload,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

export interface DropzoneUploadProps {
  onFilesSelected: (files: File[]) => void;
  isUploading?: boolean;
  uploadError?: string | null;
  maxSizeMb?: number;
  compact?: boolean;
}

/**
 * Componente leve de Drag-and-Drop e Upload Múltiplo de PDFs (`components/upload/DropzoneUpload.tsx`).
 * - Otimizado para PCs antigos (bordas nítidas de alto contraste, sem blur nem drop-shadow).
 * - Aceita um ou múltiplos arquivos `.pdf` (`application/pdf`) simultaneamente.
 */
export function DropzoneUpload({
  onFilesSelected,
  isUploading = false,
  uploadError = null,
  maxSizeMb = 50,
  compact = false,
}: DropzoneUploadProps) {
  const [isDragOver, setIsDragOver] = useState<boolean>(false);
  const [localValidationError, setLocalValidationError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const maxBytes = maxSizeMb * 1024 * 1024;

  const validateAndSubmitFiles = useCallback(
    (fileList: FileList | null | undefined) => {
      if (!fileList || fileList.length === 0) return;
      setLocalValidationError(null);

      const validFiles: File[] = [];
      const errors: string[] = [];

      for (let i = 0; i < fileList.length; i += 1) {
        const candidate = fileList.item(i);
        if (!candidate) continue;

        const lowerName = candidate.name.toLowerCase();
        const isPdfExtension = lowerName.endsWith(".pdf");
        const isPdfMime =
          candidate.type === "application/pdf" || candidate.type === "";

        if (!isPdfExtension || !isPdfMime) {
          errors.push(
            `"${candidate.name}": formato inválido (aceita exclusivamente .pdf).`
          );
          continue;
        }

        if (candidate.size === 0) {
          errors.push(`"${candidate.name}": o arquivo está vazio (0 bytes).`);
          continue;
        }

        if (candidate.size > maxBytes) {
          const sizeMb = (candidate.size / (1024 * 1024)).toFixed(1);
          errors.push(
            `"${candidate.name}": excede o limite de ${maxSizeMb} MB (${sizeMb} MB).`
          );
          continue;
        }

        validFiles.push(candidate);
      }

      if (errors.length > 0) {
        setLocalValidationError(errors.join(" "));
      }

      if (validFiles.length > 0) {
        onFilesSelected(validFiles);
      }
    },
    [maxBytes, maxSizeMb, onFilesSelected]
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);
      validateAndSubmitFiles(e.dataTransfer.files);
    },
    [validateAndSubmitFiles]
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      validateAndSubmitFiles(e.target.files);
      e.target.value = "";
    },
    [validateAndSubmitFiles]
  );

  const displayedError = localValidationError || uploadError;

  return (
    <div className="space-y-5">
      {/* Zona de Drag-and-Drop Múltiplo SaaS Claro */}
      <div
        role="button"
        tabIndex={0}
        aria-label="Selecionar ou arrastar um ou mais arquivos PDF para análise"
        aria-busy={isUploading}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        className={cn(
          "group cursor-pointer rounded-xl border border-dashed text-center transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2",
          compact ? "px-5 py-4" : "px-6 py-10",
          displayedError && !isDragOver
            ? "border-red-300 bg-red-50/40 hover:border-red-400"
            : isDragOver
            ? "border-emerald-600 bg-emerald-50/80 ring-2 ring-emerald-600/20"
            : isUploading
            ? "border-emerald-400 bg-emerald-50/40 hover:border-emerald-600"
            : "border-slate-300 bg-slate-50/60 hover:border-emerald-600 hover:bg-emerald-50/30"
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,application/pdf"
          onChange={handleFileInputChange}
          className="hidden"
          data-testid="pdf-file-input"
        />

        <div className="mx-auto flex max-w-xl flex-col items-center space-y-3">
          <div
            className={cn(
              "flex items-center justify-center rounded-xl border shadow-sm transition-colors duration-200",
              isDragOver
                ? "border-emerald-700 bg-emerald-800 text-white"
                : "border-slate-200 bg-white text-emerald-800 group-hover:border-emerald-300 group-hover:bg-emerald-50",
              compact ? "h-10 w-10" : "h-12 w-12"
            )}
          >
            <Upload className={compact ? "h-4 w-4" : "h-5 w-5"} aria-hidden="true" />
          </div>

          <div className="space-y-1.5">
            <p
              className={cn(
                "font-semibold tracking-tight text-slate-900",
                compact ? "text-sm" : "text-[15px]"
              )}
            >
              {isUploading ? (
                <span>
                  Arraste ou{" "}
                  <span className="text-emerald-800 underline decoration-emerald-700 underline-offset-2">
                    clique aqui
                  </span>{" "}
                  para adicionar mais PDFs à fila de processamento
                </span>
              ) : (
                <span>
                  Arraste e solte um ou mais arquivos PDF aqui, ou{" "}
                  <span className="text-emerald-800 underline decoration-emerald-700 underline-offset-2">
                    clique para selecionar
                  </span>
                </span>
              )}
            </p>
            <p className="text-xs leading-relaxed text-slate-500">
              Aceita múltiplos arquivos <strong className="font-semibold text-slate-700">.PDF</strong> (textuais nativos, digitalizados/escaneados ou híbridos) • Limite máximo:{" "}
              <strong className="font-mono font-semibold tabular-nums text-slate-700">
                {maxSizeMb} MB
              </strong>{" "}
              por arquivo
            </p>
          </div>
        </div>
      </div>

      {/* Exibição de Erro de Validação Local */}
      {displayedError && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-3.5 text-xs text-red-950"
        >
          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-700" aria-hidden="true" />
          <div>
            <p className="font-semibold text-red-900">Arquivo rejeitado na validação local</p>
            <p className="mt-0.5 leading-relaxed text-red-800">{displayedError}</p>
          </div>
        </div>
      )}

      {!compact && (
        <>
          {/* Aviso Normativo Institucional Obrigatório (LGPD / LAI / Distinção Tarjamento vs. Anonimização) */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 text-xs text-slate-600 shadow-sm">
              <div className="flex items-center gap-2.5 font-semibold text-slate-900">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-50 text-emerald-800">
                  <Lock className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span>Processamento 100% Local e Conformidade LGPD / LAI</span>
              </div>
              <p className="mt-2.5 leading-relaxed">
                Instrumento técnico de apoio à decisão administrativa em conformidade com a{" "}
                <strong className="font-semibold text-slate-900">Lei nº 13.709/2018 (LGPD)</strong> e a{" "}
                <strong className="font-semibold text-slate-900">Lei nº 12.527/2011 (LAI)</strong>. Nenhum documento é enviado para APIs externas de IA. Os dados pessoais detectados não são armazenados no banco de dados nem nos logs de auditoria (<em>Política Zero-PII</em>).
              </p>
            </section>

            <section className="rounded-2xl border border-amber-200 bg-amber-50/70 p-5 text-xs text-amber-900 shadow-sm">
              <div className="flex items-center gap-2.5 font-semibold text-amber-950">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-100 text-amber-800">
                  <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span>Distinção Legal: Tarjamento vs. Anonimização (Art. 5º, XI, LGPD)</span>
              </div>
              <p className="mt-2.5 leading-relaxed">
                O <strong className="font-semibold text-amber-950">tarjamento definitivo (true redaction)</strong> destrói irreversivelmente os fluxos de texto, vetores e pixels nas regiões selecionadas, mas{" "}
                <strong className="font-semibold text-amber-950">não garante por si só a anonimização integral do documento</strong> caso o contexto remanescente permita identificação indireta. A conferência humana (<em>Human-in-the-Loop</em>) antes da publicação é obrigatória.
              </p>
            </section>
          </div>

          {/* Painel Unificado das 4 Categorias Taxonômicas (sem caixas coloridas aninhadas) */}
          <section
            aria-label="Classificação Automática Estrita"
            className="overflow-hidden rounded-2xl border border-slate-200 bg-white text-xs shadow-sm"
          >
            <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-3.5 font-semibold text-slate-900">
              <CheckCircle2 className="h-4 w-4 text-emerald-700" aria-hidden="true" />
              <span>Classificação Automática Estrita (Seção 2 da Especificação UFAM):</span>
            </div>
            <ul className="grid grid-cols-1 divide-y divide-slate-100 sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x lg:divide-slate-100">
              <li className="px-5 py-4">
                <div className="flex items-center gap-2 font-semibold text-slate-900">
                  <span
                    className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm bg-blue-600"
                    aria-hidden="true"
                  />
                  <span>1. Dado Pessoal Comum</span>
                </div>
                <span className="mt-1.5 block text-[11px] leading-relaxed text-slate-600">
                  Nome, CPF, RG, Título de Eleitor, CNH, SIAPE, Matrícula UFAM, E-mail, Telefone, Assinatura
                </span>
              </li>
              <li className="px-5 py-4">
                <div className="flex items-center gap-2 font-semibold text-slate-900">
                  <span
                    className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm bg-red-500"
                    aria-hidden="true"
                  />
                  <span>2. Dado Pessoal Sensível</span>
                </div>
                <span className="mt-1.5 block text-[11px] leading-relaxed text-slate-600">
                  Art. 5º, II, LGPD: Saúde (CID, Laudos), Genética, Biometria, Filiação Sindical, Etnia, Religião
                </span>
              </li>
              <li className="px-5 py-4">
                <div className="flex items-center gap-2 font-semibold text-slate-900">
                  <span
                    className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm bg-violet-600"
                    aria-hidden="true"
                  />
                  <span>3. Financeiro e Bancário</span>
                </div>
                <span className="mt-1.5 block text-[11px] leading-relaxed text-slate-600">
                  Agência, Conta Corrente, PIX, Remuneração (tratado com alto sigilo, distinto do Art. 5º, II)
                </span>
              </li>
              <li className="px-5 py-4">
                <div className="flex items-center gap-2 font-semibold text-slate-900">
                  <span
                    className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm bg-amber-500"
                    aria-hidden="true"
                  />
                  <span>4. Menores e Dependentes</span>
                </div>
                <span className="mt-1.5 block text-[11px] leading-relaxed text-slate-600">
                  Art. 14, LGPD: Crianças, Adolescentes, Dependentes Funcionais e Pensão
                </span>
              </li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
