"use client";

import React, { useCallback, useRef, useState } from "react";
import {
  CheckCircle2,
  Lock,
  ShieldAlert,
  Upload,
  XCircle,
} from "lucide-react";
import { Card } from "@/components/ui";
import { cn } from "@/lib/utils";

export interface DropzoneUploadProps {
  onFilesSelected: (files: File[]) => void;
  isUploading?: boolean;
  uploadError?: string | null;
  maxSizeMb?: number;
  compact?: boolean;
}

const TAXONOMY_ITEMS = [
  {
    title: "1. Dado Pessoal Comum",
    dotClass: "bg-category-commonAccent",
    description:
      "Nome, CPF, RG, Título de Eleitor, CNH, SIAPE, Matrícula UFAM, E-mail, Telefone, Assinatura",
  },
  {
    title: "2. Dado Pessoal Sensível",
    dotClass: "bg-category-sensitiveAccent",
    description:
      "Art. 5º, II, LGPD: Saúde (CID, Laudos), Genética, Biometria, Filiação Sindical, Etnia, Religião",
  },
  {
    title: "3. Financeiro e Bancário",
    dotClass: "bg-category-financialAccent",
    description:
      "Agência, Conta Corrente, PIX, Remuneração (tratado com alto sigilo, distinto do Art. 5º, II)",
  },
  {
    title: "4. Menores e Dependentes",
    dotClass: "bg-category-minorAccent",
    description:
      "Art. 14, LGPD: Crianças, Adolescentes, Dependentes Funcionais e Pensão",
  },
];

/**
 * Componente leve de Drag-and-Drop e Upload Múltiplo de PDFs (`components/upload/DropzoneUpload.tsx`).
 * - Estética SaaS minimalista com alto contraste (WCAG 2.2 AA) e zero filtros pesados em GPU.
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
  const [localValidationError, setLocalValidationError] = useState<
    string | null
  >(null);

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
    <div className="space-y-4">
      {/* Zona de Drag-and-Drop Múltiplo SaaS */}
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
          "group cursor-pointer rounded-xl border-2 border-dashed text-center transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-2",
          compact ? "px-5 py-4" : "px-6 py-8 sm:py-10",
          displayedError && !isDragOver
            ? "border-red-300 bg-red-50/50 hover:border-red-400"
            : isDragOver
            ? "border-brand-primary bg-brand-subtle"
            : isUploading
            ? "border-brand-border bg-brand-subtle/50 hover:border-brand-primary"
            : "border-line bg-surface-muted hover:border-brand-primary hover:bg-brand-subtle/40"
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

        <div className="mx-auto flex max-w-xl flex-col items-center space-y-2.5">
          <div
            className={cn(
              "flex items-center justify-center rounded-xl border transition-colors duration-150",
              isDragOver
                ? "border-brand-primary bg-brand-primary text-white"
                : "border-line-subtle bg-surface-card text-brand-primary shadow-xs group-hover:border-brand-border group-hover:bg-brand-subtle",
              compact ? "h-9 w-9" : "h-11 w-11"
            )}
          >
            <Upload
              className={compact ? "h-4 w-4" : "h-5 w-5"}
              aria-hidden="true"
            />
          </div>

          <div className="space-y-1">
            <p
              className={cn(
                "font-semibold tracking-tight text-ink-primary",
                compact ? "text-sm" : "text-[15px]"
              )}
            >
              {isUploading ? (
                <span>
                  Arraste ou{" "}
                  <span className="text-brand-primary underline decoration-brand-primary underline-offset-2">
                    clique aqui
                  </span>{" "}
                  para adicionar mais PDFs à fila de processamento
                </span>
              ) : (
                <span>
                  Arraste e solte um ou mais arquivos PDF aqui, ou{" "}
                  <span className="text-brand-primary underline decoration-brand-primary underline-offset-2">
                    clique para selecionar
                  </span>
                </span>
              )}
            </p>
            <p className="text-xs leading-relaxed text-ink-muted">
              Aceita múltiplos arquivos{" "}
              <strong className="font-semibold text-ink-primary">.PDF</strong>{" "}
              (textuais nativos, digitalizados/escaneados ou híbridos) • Limite
              máximo:{" "}
              <strong className="font-mono font-semibold tabular-nums text-ink-primary">
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
          className="flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-950"
        >
          <XCircle
            className="mt-0.5 h-4 w-4 shrink-0 text-red-700"
            aria-hidden="true"
          />
          <div>
            <p className="font-semibold text-red-900">
              Arquivo rejeitado na validação local
            </p>
            <p className="mt-0.5 leading-relaxed text-red-800">
              {displayedError}
            </p>
          </div>
        </div>
      )}

      {!compact && (
        <>
          {/* Avisos Normativos Institucionais Obrigatórios (LGPD / LAI / Art. 5º, XI) */}
          <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2">
            <Card className="p-4 text-xs text-ink-secondary">
              <div className="flex items-center gap-2 font-semibold text-ink-primary">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-brand-border bg-brand-subtle text-brand-ink">
                  <Lock className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span>Processamento 100% Local e Conformidade LGPD / LAI</span>
              </div>
              <p className="mt-2 leading-relaxed">
                Instrumento técnico de apoio à decisão administrativa em
                conformidade com a{" "}
                <strong className="font-semibold text-ink-primary">
                  Lei nº 13.709/2018 (LGPD)
                </strong>{" "}
                e a{" "}
                <strong className="font-semibold text-ink-primary">
                  Lei nº 12.527/2011 (LAI)
                </strong>
                . Nenhum documento é enviado para APIs externas de IA. Os dados
                pessoais detectados não são armazenados no banco de dados nem
                nos logs de auditoria (<em>Política Zero-PII</em>).
              </p>
            </Card>

            <section className="rounded-xl border border-amber-200 bg-amber-50/70 p-4 text-xs text-amber-950 shadow-xs">
              <div className="flex items-center gap-2 font-semibold text-amber-950">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-amber-200 bg-amber-100 text-amber-800">
                  <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span>
                  Distinção Legal: Tarjamento vs. Anonimização (Art. 5º, XI,
                  LGPD)
                </span>
              </div>
              <p className="mt-2 leading-relaxed text-amber-900">
                O{" "}
                <strong className="font-semibold text-amber-950">
                  tarjamento definitivo (true redaction)
                </strong>{" "}
                destrói irreversivelmente os fluxos de texto, vetores e pixels
                nas regiões selecionadas, mas{" "}
                <strong className="font-semibold text-amber-950">
                  não garante por si só a anonimização integral do documento
                </strong>{" "}
                caso o contexto remanescente permita identificação indireta. A
                conferência humana (<em>Human-in-the-Loop</em>) antes da
                publicação é obrigatória.
              </p>
            </section>
          </div>

          {/* Painel Unificado das 4 Categorias Taxonômicas */}
          <section
            aria-label="Classificação Automática Estrita"
            className="overflow-hidden rounded-xl border border-line-subtle bg-surface-card text-xs shadow-xs"
          >
            <div className="flex items-center gap-2 border-b border-line-subtle bg-surface-muted px-4 py-2.5 font-semibold text-ink-primary">
              <CheckCircle2
                className="h-4 w-4 text-brand-primary"
                aria-hidden="true"
              />
              <span>
                Classificação Automática Estrita (Seção 2 da Especificação
                UFAM):
              </span>
            </div>
            <ul className="grid grid-cols-1 divide-y divide-line-subtle sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x lg:divide-line-subtle">
              {TAXONOMY_ITEMS.map((item) => (
                <li key={item.title} className="px-4 py-3.5">
                  <div className="flex items-center gap-2 font-semibold text-ink-primary">
                    <span
                      className={cn(
                        "inline-block h-2.5 w-2.5 shrink-0 rounded-sm",
                        item.dotClass
                      )}
                      aria-hidden="true"
                    />
                    <span>{item.title}</span>
                  </div>
                  <span className="mt-1.5 block text-[11px] leading-relaxed text-ink-muted">
                    {item.description}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
