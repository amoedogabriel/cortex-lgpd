"use client";

/**
 * Painel Lateral de Ocorrências, Recomendação Inteligente e Escolha Manual 3-Estados
 * (`frontend/src/components/workspace/OccurrencesSidebar.tsx`).
 *
 * Requisitos atendidos:
 * 1. Banner de Perfil do Documento no topo indicando a classificação automática (`RESTRICTED_PROCESS` vs. `PUBLIC_TRANSPARENCY`) e a Recomendação IA.
 * 2. Barra de Ação Rápida Global (1 Clique):
 *    - `Usar Recomendação IA` (aplica `recommended_mode` em todas as ocorrências);
 *    - `Tarjar Tudo (Preto)` (define `BLACKOUT` para todas as ocorrências);
 *    - `Mascarar CPFs (***.XXX.XXX-**)` (define `PARTIAL_MASK` para todos os CPFs do documento).
 * 3. Seletor Individual de 3 Estados por Ocorrência (`[■ Tarjar]` -> `BLACKOUT`, `[*** Mascarar]` -> `PARTIAL_MASK`, `[○ Manter]` -> `KEEP`), sincronizado por `group_id` quando marcado.
 * 4. Barra de status indicando o progresso do streaming SSE, filtros por categoria LGPD e paginação em fatias de 25 itens.
 */

import React, { useMemo, useState } from "react";
import {
  CheckSquare,
  Filter,
  Keyboard,
  Layers,
  Shield,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  DetectedOccurrence,
  DocumentProfile,
  JobStatus,
  ManualRedactionBox,
  PiiCategory,
  RedactionActionMode,
} from "@/types/document";

export interface OccurrencesSidebarProps {
  status: JobStatus;
  documentProfile?: DocumentProfile;
  processedPages: number;
  totalPages: number;
  progressPercentage: number;
  currentPage: number;
  allOccurrences: DetectedOccurrence[];
  manualBoxes: ManualRedactionBox[];
  deselectedIds: Set<string>;
  actionModesById?: Record<string, RedactionActionMode>;
  activeOccurrenceId: string | null;
  isApplyingRedaction: boolean;
  applyToGroupByDefault?: boolean;
  onApplyToGroupChange?: (enabled: boolean) => void;
  onPageChange: (page: number) => void;
  onSelectOccurrence: (occId: string | null) => void;
  onToggleOccurrence: (occId: string) => void;
  onSetOccurrenceActionMode?: (occId: string, mode: RedactionActionMode) => void;
  onApplyAiRecommendation?: () => void;
  onSetAllBlackout?: () => void;
  onMaskAllCpfs?: () => void;
  onSetAllDocumentSelection: (selectAll: boolean) => void;
  onSetCategorySelection: (category: PiiCategory, shouldSelect: boolean) => void;
  onSetGroupSelection: (groupId: string, shouldSelect: boolean) => void;
  onRemoveManualBox: (manualId: string) => void;
  onOpenConfirmRedactionModal: () => void;
}

const CATEGORY_METADATA: Record<
  PiiCategory,
  { label: string; shortLabel: string; badgeClass: string; dotClass: string }
> = {
  PERSONAL_COMMON: {
    label: "Dado Pessoal Comum",
    shortLabel: "Comum",
    badgeClass: "border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]",
    dotClass: "bg-[#2563eb]",
  },
  PERSONAL_SENSITIVE: {
    label: "Dado Pessoal Sensível (Art. 5º, II)",
    shortLabel: "Sensível",
    badgeClass: "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]",
    dotClass: "bg-[#dc2626]",
  },
  FINANCIAL_BANKING: {
    label: "Dado Financeiro / Bancário",
    shortLabel: "Financeiro",
    badgeClass: "border-[#ddd6fe] bg-[#f5f3ff] text-[#6d28d9]",
    dotClass: "bg-[#7c3aed]",
  },
  MINOR_OR_DEPENDENT: {
    label: "Menor / Dependente (Art. 14)",
    shortLabel: "Menor/Dep.",
    badgeClass: "border-[#fde68a] bg-[#fffbeb] text-[#b45309]",
    dotClass: "bg-[#d97706]",
  },
};

const MAX_DOM_ITEMS_PER_SLICE = 25;

function getEffectiveOccurrenceMode(
  occ: DetectedOccurrence,
  deselectedIds: Set<string>,
  actionModesById?: Record<string, RedactionActionMode>
): RedactionActionMode {
  if (actionModesById && actionModesById[occ.id]) {
    return actionModesById[occ.id];
  }
  if (deselectedIds.has(occ.id)) {
    return "KEEP";
  }
  return occ.action_mode || occ.recommended_mode || "BLACKOUT";
}

export function OccurrencesSidebar({
  status,
  documentProfile = "RESTRICTED_PROCESS",
  processedPages,
  totalPages,
  progressPercentage,
  currentPage,
  allOccurrences,
  manualBoxes,
  deselectedIds,
  actionModesById,
  activeOccurrenceId,
  isApplyingRedaction,
  applyToGroupByDefault: controlledApplyToGroup,
  onApplyToGroupChange,
  onPageChange,
  onSelectOccurrence,
  onToggleOccurrence,
  onSetOccurrenceActionMode,
  onApplyAiRecommendation,
  onSetAllBlackout,
  onMaskAllCpfs,
  onSetAllDocumentSelection,
  onSetCategorySelection,
  onSetGroupSelection,
  onRemoveManualBox,
  onOpenConfirmRedactionModal,
}: OccurrencesSidebarProps) {
  const [scopeFilter, setScopeFilter] = useState<"CURRENT_PAGE" | "ALL_PAGES">(
    "CURRENT_PAGE"
  );
  const [categoryFilter, setCategoryFilter] = useState<PiiCategory | "ALL">("ALL");
  const [internalApplyToGroup, setInternalApplyToGroup] = useState<boolean>(true);
  const [listSlicePage, setListSlicePage] = useState<number>(1);

  const applyToGroupByDefault =
    controlledApplyToGroup !== undefined ? controlledApplyToGroup : internalApplyToGroup;

  const handleGroupSyncCheckboxChange = (checked: boolean) => {
    setInternalApplyToGroup(checked);
    onApplyToGroupChange?.(checked);
  };

  // Mapa de quantas vezes cada `group_id` se repete no documento inteiro
  const groupCountsMap = useMemo<Record<string, number>>(() => {
    const counts: Record<string, number> = {};
    for (const occ of allOccurrences) {
      counts[occ.group_id] = (counts[occ.group_id] || 0) + 1;
    }
    return counts;
  }, [allOccurrences]);

  // Ocorrências filtradas conforme escopo (página atual vs. documento) e categoria
  const filteredOccurrences = useMemo<DetectedOccurrence[]>(() => {
    return allOccurrences.filter((occ) => {
      if (scopeFilter === "CURRENT_PAGE" && occ.page_number !== currentPage) {
        return false;
      }
      if (categoryFilter !== "ALL" && occ.category !== categoryFilter) {
        return false;
      }
      return true;
    });
  }, [allOccurrences, scopeFilter, currentPage, categoryFilter]);

  // Paginação interna da lista para jamais montar mais de 25 linhas simultâneas no DOM
  const totalSlicePages = Math.max(
    1,
    Math.ceil(filteredOccurrences.length / MAX_DOM_ITEMS_PER_SLICE)
  );
  const effectiveSlicePage = Math.min(listSlicePage, totalSlicePages);

  const visibleOccurrencesSlice = useMemo<DetectedOccurrence[]>(() => {
    const startIdx = (effectiveSlicePage - 1) * MAX_DOM_ITEMS_PER_SLICE;
    return filteredOccurrences.slice(startIdx, startIdx + MAX_DOM_ITEMS_PER_SLICE);
  }, [filteredOccurrences, effectiveSlicePage]);

  const { totalSelectedAuto, totalBlackoutCount, totalMaskedCount } = useMemo(() => {
    let selected = 0;
    let blackout = 0;
    let masked = 0;
    for (const o of allOccurrences) {
      const m = getEffectiveOccurrenceMode(o, deselectedIds, actionModesById);
      if (m !== "KEEP") {
        selected += 1;
        if (m === "PARTIAL_MASK") {
          masked += 1;
        } else {
          blackout += 1;
        }
      }
    }
    return {
      totalSelectedAuto: selected,
      totalBlackoutCount: blackout,
      totalMaskedCount: masked,
    };
  }, [allOccurrences, deselectedIds, actionModesById]);

  const totalRedactionsToApply = totalSelectedAuto + manualBoxes.length;

  const handleCheckboxToggle = (occ: DetectedOccurrence) => {
    const effectiveMode = getEffectiveOccurrenceMode(occ, deselectedIds, actionModesById);
    const currentlySelected = effectiveMode !== "KEEP";
    const repeatCount = groupCountsMap[occ.group_id] || 1;
    if (applyToGroupByDefault && repeatCount > 1) {
      onSetGroupSelection(occ.group_id, !currentlySelected);
    } else {
      onToggleOccurrence(occ.id);
    }
  };

  const handleSelectModeButton = (occ: DetectedOccurrence, mode: RedactionActionMode) => {
    if (onSetOccurrenceActionMode) {
      onSetOccurrenceActionMode(occ.id, mode);
      return;
    }
    if (mode === "KEEP") {
      if (!deselectedIds.has(occ.id)) {
        onToggleOccurrence(occ.id);
      }
    } else if (deselectedIds.has(occ.id)) {
      onToggleOccurrence(occ.id);
    }
  };

  const isPublicTransparency = documentProfile === "PUBLIC_TRANSPARENCY";
  const isStreamingPages = processedPages < totalPages;

  return (
    <aside
      className="overflow-hidden rounded-[12px] border border-[#e2e8f0] bg-white shadow-[0_1px_2px_rgba(15,23,42,0.05)] lg:sticky lg:top-4"
      aria-label="Painel de Ocorrências e Produtividade"
    >
      {/* Bloco 1 — Recomendação IA e Ações em Lote */}
      <section
        className="border-b border-[#a7f3d0] bg-[#ecfdf5] p-4"
        data-testid="document-profile-banner"
      >
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-[8px] border border-[#a7f3d0] bg-white text-[#065f46]">
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <span className="block text-[12px] font-semibold uppercase tracking-wide text-[#065f46]">
              Recomendação Inteligente
            </span>
            <p className="my-1 text-[15px] font-semibold leading-snug text-[#0f172a]">
              {isPublicTransparency
                ? "Perfil Detectado: Edital / Lista Pública — Recomendação IA: Mascaramento Parcial de CPF (***.XXX.XXX-**)"
                : "Perfil Detectado: Despacho / Processo Restrito — Recomendação IA: Tarjamento Total (Preto)"}
            </p>
            <p className="mb-3 text-[13px] leading-relaxed text-[#334155]">
              {isPublicTransparency
                ? "Documento de transparência ativa: CPFs e RGs podem ser mascarados parcialmente (***.XXX.XXX-**) para conciliar publicidade e LGPD."
                : "Processo administrativo interno/funcional: recomenda-se Tarja Preta Total (100%) em todos os dados pessoais, financeiros e sensíveis."}
            </p>
          </div>
        </div>

        {/* Botões em Lote Alinhados (sem esticar Tarjar Tudo em 100%) */}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              if (onApplyAiRecommendation) {
                onApplyAiRecommendation();
              } else {
                onSetAllDocumentSelection(true);
              }
            }}
            className="h-9 rounded-[8px] border border-[#0e5a43] bg-[#0e5a43] px-3.5 text-[13px] font-semibold text-white transition-all duration-200 hover:bg-[#0b4a37] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="quick-action-ai-rec-btn"
          >
            Usar Recomendação IA
          </button>

          <button
            type="button"
            onClick={() => {
              onMaskAllCpfs?.();
            }}
            className="h-9 rounded-[8px] border border-[#cbd5e1] bg-white px-3.5 text-[13px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="quick-action-mask-cpfs-btn"
          >
            Mascarar CPFs (***.XXX.XXX-**)
          </button>

          <button
            type="button"
            onClick={() => {
              if (onSetAllBlackout) {
                onSetAllBlackout();
              } else {
                onSetAllDocumentSelection(true);
              }
            }}
            className="h-9 rounded-[8px] border border-[#fecaca] bg-white px-3.5 text-[13px] font-semibold text-[#991b1b] transition-all duration-200 hover:bg-[#fef2f2] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="quick-action-blackout-all-btn"
          >
            Tarjar Tudo (Preto)
          </button>
        </div>
      </section>

      {/* Bloco 2 — Progresso das Páginas */}
      <section
        aria-live="polite"
        aria-busy={isStreamingPages}
        className="border-b border-[#e2e8f0] p-4"
        data-testid="sidebar-sse-progress-banner"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-[13px] font-semibold tabular-nums text-[#0f172a]">
            Páginas prontas: {processedPages} de {Math.max(1, totalPages)} ({progressPercentage}%)
          </span>
          <span className="inline-flex h-[22px] items-center rounded-full bg-[#f1f5f9] px-2.5 font-mono text-[11px] font-medium text-[#334155]">
            {status}
          </span>
        </div>

        <p className="mt-1.5 text-[12px] leading-relaxed text-[#475569]">
          {isStreamingPages
            ? `Páginas prontas: ${processedPages} de ${totalPages} — Você já pode revisar e marcar as páginas disponíveis enquanto o restante é processado.`
            : `Todas as ${totalPages} páginas foram analisadas e estão prontas para conferência final.`}
        </p>

        <div className="mt-2.5 h-[6px] w-full overflow-hidden rounded-full bg-[#e2e8f0]">
          <div
            className="h-full rounded-full bg-[#0e5a43] transition-[width] duration-200"
            style={{ width: `${Math.min(100, progressPercentage)}%` }}
          />
        </div>
      </section>

      {/* Bloco 3 — Filtros por Categoria LGPD e Sincronização group_id */}
      <section className="border-b border-[#e2e8f0] p-4" aria-label="Filtros e ações em lote">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-[#0f172a]">
            <Filter className="h-3.5 w-3.5 text-[#0e5a43]" aria-hidden="true" />
            <span>Filtros Rápidos e Seleção em Lote</span>
          </div>
          <span
            aria-live="polite"
            className="font-mono text-[12px] font-medium tabular-nums text-[#475569]"
          >
            {totalSelectedAuto}/{allOccurrences.length} auto ({totalBlackoutCount} pretas, {totalMaskedCount} masc.) + {manualBoxes.length} manuais
          </span>
        </div>

        {/* Botões Compactos: Marcar Todas (Doc) / Desmarcar Todas */}
        <div className="mt-2.5 grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => onSetAllDocumentSelection(true)}
            className="inline-flex h-[28px] items-center justify-center gap-1.5 rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="select-all-doc-btn"
          >
            <CheckSquare className="h-3.5 w-3.5 text-[#0e5a43]" aria-hidden="true" />
            <span>Marcar Todas (Doc)</span>
          </button>

          <button
            type="button"
            onClick={() => onSetAllDocumentSelection(false)}
            className="inline-flex h-[28px] items-center justify-center gap-1.5 rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="deselect-all-doc-btn"
          >
            <Square className="h-3.5 w-3.5 text-[#475569]" aria-hidden="true" />
            <span>Desmarcar Todas</span>
          </button>
        </div>

        {/* Chips Pastéis Interativos por Categoria LGPD */}
        <fieldset className="mt-2.5">
          <legend className="block text-[11px] font-medium text-[#475569]">
            Selecionar / Filtrar por Categoria LGPD:
          </legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {(
              [
                "PERSONAL_COMMON",
                "PERSONAL_SENSITIVE",
                "FINANCIAL_BANKING",
                "MINOR_OR_DEPENDENT",
              ] as PiiCategory[]
            ).map((cat) => {
              const meta = CATEGORY_METADATA[cat];
              const catItems = allOccurrences.filter((o) => o.category === cat);
              const catSelectedCount = catItems.filter(
                (o) => getEffectiveOccurrenceMode(o, deselectedIds, actionModesById) !== "KEEP"
              ).length;
              const allCatSelected =
                catItems.length > 0 && catSelectedCount === catItems.length;

              return (
                <div
                  key={cat}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition-all duration-200",
                    meta.badgeClass,
                    catItems.length === 0 && "opacity-70",
                    categoryFilter === cat && "ring-2 ring-[#0e6b4f]/50"
                  )}
                >
                  <button
                    type="button"
                    aria-pressed={categoryFilter === cat}
                    onClick={() =>
                      setCategoryFilter((prev) => (prev === cat ? "ALL" : cat))
                    }
                    className="inline-flex items-center gap-1.5 text-left text-[12px] font-semibold hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
                    title={`Filtrar lista por ${meta.label}`}
                  >
                    <span
                      className={cn("h-2 w-2 shrink-0 rounded-full", meta.dotClass)}
                      aria-hidden="true"
                    />
                    <span className="tabular-nums">
                      {meta.shortLabel} ({catSelectedCount}/{catItems.length})
                    </span>
                  </button>

                  <button
                    type="button"
                    disabled={catItems.length === 0}
                    onClick={() => onSetCategorySelection(cat, !allCatSelected)}
                    className="shrink-0 rounded-full border border-current/25 bg-white/80 px-1.5 py-0.5 text-[10px] font-semibold transition-colors duration-200 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
                    title={
                      allCatSelected
                        ? `Desmarcar todas de ${meta.label}`
                        : `Marcar todas de ${meta.label}`
                    }
                    data-testid={`batch-category-btn-${cat}`}
                  >
                    {allCatSelected ? "Desmarcar" : "Marcar"}
                  </button>
                </div>
              );
            })}
          </div>
        </fieldset>

        {/* Checkbox Global de Agrupamento por `group_id` */}
        <label className="mt-3 flex cursor-pointer items-start gap-2.5 text-[12px] leading-relaxed text-[#334155]">
          <input
            type="checkbox"
            checked={applyToGroupByDefault}
            onChange={(e) => handleGroupSyncCheckboxChange(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded accent-[#0e5a43] focus-visible:ring-2 focus-visible:ring-[#0e6b4f]"
            data-testid="toggle-group-sync-checkbox"
          />
          <span>
            <strong className="font-semibold text-[#0f172a]">
              Aplicar a todas as repetições deste dado no documento (`group_id`):
            </strong>{" "}
            ao alterar o modo (`Tarjar`, `Mascarar` ou `Manter`) de um CPF ou Nome repetido em várias páginas, sincroniza automaticamente todas as ocorrências idênticas.
          </span>
        </label>
      </section>

      {/* Bloco 4 — Abas de Escopo e Lista com Divisores (sem cartões aninhados) */}
      <section aria-label="Lista de ocorrências detectadas">
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 pb-3 pt-3">
          <div
            className="flex gap-1.5"
            role="group"
            aria-label="Escopo da lista de ocorrências"
          >
            <button
              type="button"
              aria-pressed={scopeFilter === "CURRENT_PAGE"}
              onClick={() => {
                setScopeFilter("CURRENT_PAGE");
                setListSlicePage(1);
              }}
              className={cn(
                "h-[30px] rounded-[8px] border px-3 text-[12px] font-semibold tabular-nums transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1",
                scopeFilter === "CURRENT_PAGE"
                  ? "border-[#0e5a43] bg-[#0e5a43] text-white"
                  : "border-[#cbd5e1] bg-white text-[#0f172a] hover:bg-[#f1f5f9]"
              )}
              data-testid="scope-current-page-btn"
            >
              Página {currentPage} (
              {allOccurrences.filter((o) => o.page_number === currentPage).length})
            </button>
            <button
              type="button"
              aria-pressed={scopeFilter === "ALL_PAGES"}
              onClick={() => {
                setScopeFilter("ALL_PAGES");
                setListSlicePage(1);
              }}
              className={cn(
                "h-[30px] rounded-[8px] border px-3 text-[12px] font-semibold tabular-nums transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1",
                scopeFilter === "ALL_PAGES"
                  ? "border-[#0e5a43] bg-[#0e5a43] text-white"
                  : "border-[#cbd5e1] bg-white text-[#0f172a] hover:bg-[#f1f5f9]"
              )}
              data-testid="scope-all-pages-btn"
            >
              Todo o Doc ({allOccurrences.length})
            </button>
          </div>

          {categoryFilter !== "ALL" && (
            <button
              type="button"
              onClick={() => setCategoryFilter("ALL")}
              className="h-[26px] rounded-full border border-[#fde68a] bg-[#fffbeb] px-2.5 text-[11px] font-semibold text-[#b45309] transition-colors duration-200 hover:bg-[#fef3c7] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            >
              Limpar Filtro ({CATEGORY_METADATA[categoryFilter].shortLabel}) ✕
            </button>
          )}
        </div>

        {/* Lista Paginada (25 itens/bloco) separada por divisores */}
        <div
          className="max-h-[420px] overflow-y-auto"
          data-testid="occurrences-list"
        >
          {visibleOccurrencesSlice.length === 0 ? (
            <div className="border-t border-[#e2e8f0] px-4 py-6 text-center text-[12px] text-[#475569]">
              Nenhuma ocorrência automática para o filtro selecionado.
            </div>
          ) : (
            visibleOccurrencesSlice.map((occ) => {
              const effectiveMode = getEffectiveOccurrenceMode(
                occ,
                deselectedIds,
                actionModesById
              );
              const isSelected = effectiveMode !== "KEEP";
              const isFocused = activeOccurrenceId === occ.id;
              const catMeta =
                CATEGORY_METADATA[occ.category] || CATEGORY_METADATA.PERSONAL_COMMON;
              const repeatCount = groupCountsMap[occ.group_id] || 1;
              const isBlankContinuation =
                Boolean(occ.is_continuation) || occ.masked_preview === "";
              const maskedPreviewText = isBlankContinuation
                ? "(cont. branca)"
                : occ.masked_preview || "***.XXX.XXX-**";

              return (
                <div
                  key={occ.id}
                  data-testid={`occurrence-item-${occ.id}`}
                  className={cn(
                    "border-t border-[#e2e8f0] px-4 py-[14px] transition-colors duration-200",
                    isFocused && "bg-[#ecfdf5]/60",
                    !isSelected && "opacity-75"
                  )}
                >
                  {/* Linha 1: Checkbox + Tipo + Valor + Botão Ver Pág. */}
                  <div className="flex items-start gap-2">
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => handleCheckboxToggle(occ)}
                        className="mt-0.5 h-4 w-4 shrink-0 rounded accent-[#0e5a43]"
                        data-testid={`occ-checkbox-${occ.id}`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-[13px] font-bold text-[#0f172a]">
                            {occ.entity_type}
                          </span>
                          {occ.preview_text && (
                            <span className="font-mono text-[12px] tabular-nums text-[#475569]">
                              {occ.preview_text}
                            </span>
                          )}
                          {effectiveMode === "PARTIAL_MASK" && (
                            <span className="rounded border border-[#bfdbfe] bg-[#eff6ff] px-1.5 py-0.5 font-mono text-[11px] font-medium text-[#1d4ed8]">
                              → {maskedPreviewText}
                            </span>
                          )}
                        </div>

                        {/* Linha 2: Chip Pastel da Categoria + Metadados */}
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <span
                            className={cn(
                              "inline-flex h-[22px] items-center rounded-full border px-2 text-[11px] font-semibold",
                              catMeta.badgeClass
                            )}
                          >
                            {catMeta.shortLabel}
                          </span>
                          <span className="font-mono text-[12px] tabular-nums text-[#475569]">
                            Pág. {occ.page_number} · Conf:{" "}
                            {Math.round(occ.confidence_score * 100)}%
                          </span>
                          {repeatCount > 1 && (
                            <span
                              className="inline-flex h-[22px] items-center rounded-full bg-[#f1f5f9] px-2 font-mono text-[11px] font-medium tabular-nums text-[#334155]"
                              title="Mesmo valor detectado em múltiplas partes do documento"
                            >
                              {repeatCount}x no doc
                            </span>
                          )}
                        </div>
                      </div>
                    </label>

                    <button
                      type="button"
                      onClick={() => {
                        onPageChange(occ.page_number);
                        onSelectOccurrence(occ.id);
                      }}
                      className="ml-auto h-[26px] shrink-0 rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
                    >
                      Ver Pág. {occ.page_number}
                    </button>
                  </div>

                  {/* Linha 3: Controle Segmentado .seg ([■ Tarjar] [*** Mascarar] [○ Manter]) */}
                  <div
                    role="group"
                    aria-label={`Modo de proteção para ${occ.entity_type}`}
                    className="mt-2.5 grid grid-cols-3 gap-[2px] rounded-[8px] bg-[#f1f5f9] p-[2px]"
                  >
                    <button
                      type="button"
                      aria-pressed={effectiveMode === "BLACKOUT"}
                      onClick={() => handleSelectModeButton(occ, "BLACKOUT")}
                      className={cn(
                        "h-[30px] rounded-[6px] text-[12px] font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1",
                        effectiveMode === "BLACKOUT"
                          ? "bg-[#0f172a] text-white shadow-sm"
                          : "bg-transparent text-[#334155] hover:bg-white/70 hover:text-[#0f172a]"
                      )}
                      data-testid={`mode-blackout-${occ.id}`}
                    >
                      ■ Tarjar
                    </button>

                    <button
                      type="button"
                      aria-pressed={effectiveMode === "PARTIAL_MASK"}
                      onClick={() => handleSelectModeButton(occ, "PARTIAL_MASK")}
                      title={`Mascarar parcialmente: ${maskedPreviewText}`}
                      className={cn(
                        "h-[30px] rounded-[6px] text-[12px] font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1",
                        effectiveMode === "PARTIAL_MASK"
                          ? "bg-white text-[#0f172a] shadow-[0_0_0_1.5px_#0f172a_inset]"
                          : "bg-transparent text-[#334155] hover:bg-white/70 hover:text-[#0f172a]"
                      )}
                      data-testid={`mode-mask-${occ.id}`}
                    >
                      *** Mascarar
                    </button>

                    <button
                      type="button"
                      aria-pressed={effectiveMode === "KEEP"}
                      onClick={() => handleSelectModeButton(occ, "KEEP")}
                      className={cn(
                        "h-[30px] rounded-[6px] text-[12px] font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1",
                        effectiveMode === "KEEP"
                          ? "bg-[#fef3c7] text-[#451a03] shadow-[0_0_0_1.5px_#d97706_inset]"
                          : "bg-transparent text-[#334155] hover:bg-white/70 hover:text-[#0f172a]"
                      )}
                      data-testid={`mode-keep-${occ.id}`}
                    >
                      ○ Manter
                    </button>
                  </div>

                  {repeatCount > 1 && (
                    <div className="mt-1.5 flex items-center justify-end text-[11px]">
                      <button
                        type="button"
                        onClick={() => onSetGroupSelection(occ.group_id, !isSelected)}
                        className="font-semibold text-[#0e5a43] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
                        data-testid={`group-toggle-btn-${occ.group_id}`}
                      >
                        {isSelected
                          ? `Desmarcar todas as ${repeatCount} repetições deste dado`
                          : `Marcar todas as ${repeatCount} repetições deste dado`}
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Paginação da Lista Lateral caso haja mais de 25 ocorrências no filtro */}
        {totalSlicePages > 1 && (
          <div className="flex items-center justify-between border-t border-[#e2e8f0] px-4 py-2.5 text-[12px]">
            <button
              type="button"
              disabled={effectiveSlicePage <= 1}
              onClick={() => setListSlicePage((p) => Math.max(1, p - 1))}
              className="h-[28px] rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 font-semibold text-[#0f172a] hover:bg-[#f1f5f9] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Bloco Anterior
            </button>
            <span className="font-mono tabular-nums text-[#475569]">
              Lista {effectiveSlicePage} de {totalSlicePages}
            </span>
            <button
              type="button"
              disabled={effectiveSlicePage >= totalSlicePages}
              onClick={() => setListSlicePage((p) => Math.min(totalSlicePages, p + 1))}
              className="h-[28px] rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 font-semibold text-[#0f172a] hover:bg-[#f1f5f9] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Próximo Bloco
            </button>
          </div>
        )}

        {/* Tarjas Manuais Adicionadas pelo Servidor */}
        {manualBoxes.length > 0 && (
          <div className="border-t border-[#e2e8f0] px-4 py-3">
            <div className="mb-2 flex items-center justify-between text-[13px] font-semibold text-[#0f172a]">
              <span className="flex items-center gap-1.5">
                <Layers className="h-3.5 w-3.5 text-[#475569]" aria-hidden="true" />
                Tarjas Manuais ({manualBoxes.length})
              </span>
            </div>
            <ul className="max-h-32 space-y-1.5 overflow-y-auto">
              {manualBoxes.map((mbox, idx) => (
                <li
                  key={mbox.id}
                  className="flex items-center justify-between rounded-[8px] border border-[#e2e8f0] bg-[#f8fafc] px-2.5 py-1.5 text-[12px]"
                >
                  <button
                    type="button"
                    onClick={() => onPageChange(mbox.page_number)}
                    className="font-medium text-[#0f172a] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
                  >
                    Tarja Manual #{idx + 1} • Pág. {mbox.page_number} (
                    {CATEGORY_METADATA[mbox.category]?.shortLabel || mbox.category})
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemoveManualBox(mbox.id)}
                    className="inline-flex h-[24px] items-center gap-1 rounded-[6px] border border-[#fecaca] bg-white px-2 text-[11px] font-semibold text-[#991b1b] transition-all duration-200 hover:bg-[#fef2f2] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
                    title="Remover esta tarja manual"
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                    <span>Excluir</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* Bloco 5 — CTA Irreversível */}
      <section
        className="border-t border-[#e2e8f0] bg-white px-4 py-[14px]"
        aria-label="Aplicação do tarjamento"
      >
        <button
          type="button"
          disabled={isApplyingRedaction}
          onClick={onOpenConfirmRedactionModal}
          className="flex h-[46px] w-full items-center justify-center gap-2 rounded-[8px] border border-[#7f1d1d] bg-[#7f1d1d] px-4 text-[14px] font-semibold tabular-nums text-white transition-all duration-200 hover:bg-[#691717] disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
          data-testid="open-confirm-redaction-modal-btn"
        >
          <Shield className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {isApplyingRedaction
              ? "Executando Tarjamento Físico..."
              : `Aplicar Tarjamento Definitivo (${totalRedactionsToApply} tarjas)`}
          </span>
        </button>

        <div className="mt-2.5 text-center text-[12px] text-[#475569]">
          <span className="inline-flex items-center justify-center gap-1 font-medium text-[#334155]">
            <Keyboard className="h-3.5 w-3.5 text-[#0e5a43]" aria-hidden="true" />
            Atalhos:
          </span>{" "}
          <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[#334155]">
            ←
          </kbd>{" "}
          <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[#334155]">
            →
          </kbd>{" "}
          páginas •{" "}
          <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[#334155]">
            M
          </kbd>{" "}
          desenho manual
        </div>
      </section>
    </aside>
  );
}
