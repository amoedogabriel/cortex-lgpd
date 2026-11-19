"use client";

/**
 * Painel Lateral de Ocorrências, Recomendação Inteligente e Escolha Manual 3-Estados
 * (`frontend/src/components/workspace/OccurrencesSidebar.tsx`).
 *
 * Requisitos atendidos:
 * 1. Banner de Perfil do Documento no topo indicando a classificação automática (`RESTRICTED_PROCESS` vs. `PUBLIC_TRANSPARENCY`) e a Recomendação IA.
 * 2. Barra de Ação Rápida Global (1 Clique): `Usar Recomendação IA`, `Mascarar CPFs (***.XXX.XXX-**)`, `Tarjar Tudo (Preto)`.
 * 3. Seletor Individual de 3 Estados por Ocorrência (`■ Tarjar` / `*** Mascarar` / `○ Manter`), sincronizado por `group_id` quando marcado.
 * 4. Barra de status SSE, filtros por categoria LGPD e paginação em fatias de 25 itens no DOM.
 * 5. Microcopy limpa (sem crases literais de Markdown expostas na interface).
 */

import React, { useMemo, useState } from "react";
import {
  CheckSquare,
  Filter,
  Layers,
  Shield,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import {
  Badge,
  Button,
  PII_CATEGORY_STYLES,
  SegmentedControl,
} from "@/components/ui";
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
  onSetOccurrenceActionMode?: (
    occId: string,
    mode: RedactionActionMode
  ) => void;
  onApplyAiRecommendation?: () => void;
  onSetAllBlackout?: () => void;
  onMaskAllCpfs?: () => void;
  onSetAllDocumentSelection: (selectAll: boolean) => void;
  onSetCategorySelection: (
    category: PiiCategory,
    shouldSelect: boolean
  ) => void;
  onSetGroupSelection: (groupId: string, shouldSelect: boolean) => void;
  onRemoveManualBox: (manualId: string) => void;
  onOpenConfirmRedactionModal: () => void;
}

const CATEGORY_ORDER: PiiCategory[] = [
  "PERSONAL_COMMON",
  "PERSONAL_SENSITIVE",
  "FINANCIAL_BANKING",
  "MINOR_OR_DEPENDENT",
];

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
  const [categoryFilter, setCategoryFilter] = useState<PiiCategory | "ALL">(
    "ALL"
  );
  const [internalApplyToGroup, setInternalApplyToGroup] =
    useState<boolean>(true);
  const [listSlicePage, setListSlicePage] = useState<number>(1);

  const applyToGroupByDefault =
    controlledApplyToGroup !== undefined
      ? controlledApplyToGroup
      : internalApplyToGroup;

  const handleGroupSyncCheckboxChange = (checked: boolean) => {
    setInternalApplyToGroup(checked);
    onApplyToGroupChange?.(checked);
  };

  // Mapa de quantas vezes cada group_id se repete no documento inteiro
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
    return filteredOccurrences.slice(
      startIdx,
      startIdx + MAX_DOM_ITEMS_PER_SLICE
    );
  }, [filteredOccurrences, effectiveSlicePage]);

  const { totalSelectedAuto, totalBlackoutCount, totalMaskedCount } =
    useMemo(() => {
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
    const effectiveMode = getEffectiveOccurrenceMode(
      occ,
      deselectedIds,
      actionModesById
    );
    const currentlySelected = effectiveMode !== "KEEP";
    const repeatCount = groupCountsMap[occ.group_id] || 1;
    if (applyToGroupByDefault && repeatCount > 1) {
      onSetGroupSelection(occ.group_id, !currentlySelected);
    } else {
      onToggleOccurrence(occ.id);
    }
  };

  const handleSelectModeButton = (
    occ: DetectedOccurrence,
    mode: RedactionActionMode
  ) => {
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
  const currentPageCount = useMemo(
    () => allOccurrences.filter((o) => o.page_number === currentPage).length,
    [allOccurrences, currentPage]
  );

  return (
    <aside
      className="overflow-hidden rounded-xl border border-line-subtle bg-surface-card shadow-xs lg:sticky lg:top-4"
      aria-label="Painel de Ocorrências e Produtividade"
    >
      {/* Bloco 1 — Recomendação IA e Ações Rápidas Globais */}
      <section
        className="border-b border-line-subtle border-l-4 border-l-brand-primary bg-brand-subtle/50 p-4"
        data-testid="document-profile-banner"
      >
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-brand-border bg-surface-card text-brand-ink">
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <span className="block text-[11px] font-semibold uppercase tracking-wide text-brand-ink">
              Recomendação Inteligente
            </span>
            <p className="mt-0.5 text-[13px] font-semibold leading-snug text-ink-primary">
              {isPublicTransparency
                ? "Perfil Detectado: Edital / Lista Pública — Recomendação IA: Mascaramento Parcial de CPF (***.XXX.XXX-**)"
                : "Perfil Detectado: Despacho / Processo Restrito — Recomendação IA: Tarjamento Total (Preto)"}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
              {isPublicTransparency
                ? "Documento de transparência ativa: CPFs e RGs podem ser mascarados parcialmente (***.XXX.XXX-**) para conciliar publicidade e LGPD."
                : "Processo administrativo interno/funcional: recomenda-se Tarja Preta Total (100%) em todos os dados pessoais, financeiros e sensíveis."}
            </p>
          </div>
        </div>

        {/* Grade Equilibrada de Ações em Lote */}
        <div className="mt-3 space-y-2">
          <Button
            variant="primary"
            size="sm"
            className="w-full"
            onClick={() => {
              if (onApplyAiRecommendation) {
                onApplyAiRecommendation();
              } else {
                onSetAllDocumentSelection(true);
              }
            }}
            data-testid="quick-action-ai-rec-btn"
          >
            Usar Recomendação IA
          </Button>

          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <Button
              variant="secondary"
              size="xs"
              className="h-8 px-2"
              onClick={() => {
                onMaskAllCpfs?.();
              }}
              data-testid="quick-action-mask-cpfs-btn"
            >
              Mascarar CPFs (***.XXX.XXX-**)
            </Button>

            <Button
              variant="danger-outline"
              size="xs"
              className="h-8 px-2"
              onClick={() => {
                if (onSetAllBlackout) {
                  onSetAllBlackout();
                } else {
                  onSetAllDocumentSelection(true);
                }
              }}
              data-testid="quick-action-blackout-all-btn"
            >
              Tarjar Tudo (Preto)
            </Button>
          </div>
        </div>
      </section>

      {/* Bloco 2 — Progresso das Páginas (SSE) */}
      <section
        aria-live="polite"
        aria-busy={isStreamingPages}
        className="border-b border-line-subtle px-4 py-3"
        data-testid="sidebar-sse-progress-banner"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-xs font-semibold tabular-nums text-ink-primary">
            Páginas prontas: {processedPages} de {Math.max(1, totalPages)} (
            {progressPercentage}%)
          </span>
          <Badge variant="neutral" className="font-mono text-[10px]">
            {status}
          </Badge>
        </div>

        <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
          {isStreamingPages
            ? `Páginas prontas: ${processedPages} de ${totalPages} — Você já pode revisar e marcar as páginas disponíveis enquanto o restante é processado.`
            : `Todas as ${totalPages} páginas foram analisadas e estão prontas para conferência final.`}
        </p>

        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-canvas">
          <div
            className="h-full rounded-full bg-brand-primary transition-[width] duration-150"
            style={{ width: `${Math.min(100, progressPercentage)}%` }}
          />
        </div>
      </section>

      {/* Bloco 3 — Filtros por Categoria LGPD e Sincronização de Repetições */}
      <section
        className="border-b border-line-subtle px-4 py-3"
        aria-label="Filtros e ações em lote"
      >
        <div className="flex flex-wrap items-center justify-between gap-1.5">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-ink-primary">
            <Filter
              className="h-3.5 w-3.5 text-brand-primary"
              aria-hidden="true"
            />
            <span>Filtros Rápidos e Seleção em Lote</span>
          </div>
          <span
            aria-live="polite"
            className="font-mono text-[11px] font-medium tabular-nums text-ink-muted"
          >
            {totalSelectedAuto}/{allOccurrences.length} auto (
            {totalBlackoutCount} pretas, {totalMaskedCount} masc.) +{" "}
            {manualBoxes.length} manuais
          </span>
        </div>

        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <Button
            variant="secondary"
            size="xs"
            onClick={() => onSetAllDocumentSelection(true)}
            data-testid="select-all-doc-btn"
          >
            <CheckSquare
              className="h-3.5 w-3.5 text-brand-primary"
              aria-hidden="true"
            />
            <span>Marcar Todas (Doc)</span>
          </Button>

          <Button
            variant="secondary"
            size="xs"
            onClick={() => onSetAllDocumentSelection(false)}
            data-testid="deselect-all-doc-btn"
          >
            <Square
              className="h-3.5 w-3.5 text-ink-muted"
              aria-hidden="true"
            />
            <span>Desmarcar Todas</span>
          </Button>
        </div>

        {/* Grade 2x2 de Categorias LGPD (Simétrica e Escaneável) */}
        <fieldset className="mt-2.5">
          <legend className="block text-[11px] font-medium text-ink-muted">
            Selecionar / Filtrar por Categoria LGPD:
          </legend>
          <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {CATEGORY_ORDER.map((cat) => {
              const meta = PII_CATEGORY_STYLES[cat];
              const catItems = allOccurrences.filter((o) => o.category === cat);
              const catSelectedCount = catItems.filter(
                (o) =>
                  getEffectiveOccurrenceMode(
                    o,
                    deselectedIds,
                    actionModesById
                  ) !== "KEEP"
              ).length;
              const allCatSelected =
                catItems.length > 0 && catSelectedCount === catItems.length;

              return (
                <div
                  key={cat}
                  className={cn(
                    "flex items-center justify-between gap-1 rounded-lg border px-2 py-1 transition-colors duration-150",
                    meta.badgeClass,
                    catItems.length === 0 && "opacity-65",
                    categoryFilter === cat && "ring-2 ring-brand-focus/60"
                  )}
                >
                  <button
                    type="button"
                    aria-pressed={categoryFilter === cat}
                    onClick={() =>
                      setCategoryFilter((prev) => (prev === cat ? "ALL" : cat))
                    }
                    className="inline-flex min-w-0 items-center gap-1.5 truncate text-left text-[11px] font-semibold hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-1"
                    title={`Filtrar lista por ${meta.label}`}
                  >
                    <span
                      className={cn(
                        "h-2 w-2 shrink-0 rounded-full",
                        meta.dotClass
                      )}
                      aria-hidden="true"
                    />
                    <span className="truncate tabular-nums">
                      {meta.shortLabel} ({catSelectedCount}/{catItems.length})
                    </span>
                  </button>

                  <button
                    type="button"
                    disabled={catItems.length === 0}
                    onClick={() =>
                      onSetCategorySelection(cat, !allCatSelected)
                    }
                    className="shrink-0 rounded border border-current/25 bg-white/85 px-1.5 py-0.5 text-[10px] font-semibold transition-colors duration-150 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-1"
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

        {/* Checkbox Global de Agrupamento por Repetições (sem crases literais de Markdown) */}
        <label className="mt-2.5 flex cursor-pointer items-start gap-2 text-[11px] leading-relaxed text-ink-secondary">
          <input
            type="checkbox"
            checked={applyToGroupByDefault}
            onChange={(e) => handleGroupSyncCheckboxChange(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 rounded accent-brand-primary focus-visible:ring-2 focus-visible:ring-brand-focus"
            data-testid="toggle-group-sync-checkbox"
          />
          <span>
            <strong className="font-semibold text-ink-primary">
              Sincronizar repetições do mesmo dado no documento:
            </strong>{" "}
            ao alterar o modo (Tarjar, Mascarar ou Manter) de um CPF ou nome
            repetido, aplica automaticamente em todas as páginas.
          </span>
        </label>
      </section>

      {/* Bloco 4 — Abas de Escopo e Lista com Divisores */}
      <section aria-label="Lista de ocorrências detectadas">
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
          <SegmentedControl
            ariaLabel="Escopo da lista de ocorrências"
            value={scopeFilter}
            onChange={(nextScope) => {
              setScopeFilter(nextScope);
              setListSlicePage(1);
            }}
            options={[
              {
                value: "CURRENT_PAGE",
                label: `Página ${currentPage} (${currentPageCount})`,
                testId: "scope-current-page-btn",
                activeClassName: "bg-brand-primary text-white shadow-xs",
              },
              {
                value: "ALL_PAGES",
                label: `Todo o Doc (${allOccurrences.length})`,
                testId: "scope-all-pages-btn",
                activeClassName: "bg-brand-primary text-white shadow-xs",
              },
            ]}
            itemClassName="tabular-nums px-2.5"
          />

          {categoryFilter !== "ALL" && (
            <button
              type="button"
              onClick={() => setCategoryFilter("ALL")}
              className="inline-flex h-6 items-center rounded-full border border-amber-200 bg-amber-50 px-2.5 text-[11px] font-semibold text-amber-900 transition-colors duration-150 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-1"
            >
              Limpar Filtro ({PII_CATEGORY_STYLES[categoryFilter].shortLabel}) ✕
            </button>
          )}
        </div>

        {/* Lista Paginada (máx. 25 itens no DOM) */}
        <div
          className="max-h-[400px] overflow-y-auto"
          data-testid="occurrences-list"
        >
          {visibleOccurrencesSlice.length === 0 ? (
            <div className="border-t border-line-subtle px-4 py-6 text-center text-xs text-ink-muted">
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
                PII_CATEGORY_STYLES[occ.category] ||
                PII_CATEGORY_STYLES.PERSONAL_COMMON;
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
                    "border-t border-line-subtle px-4 py-3 transition-colors duration-150",
                    isFocused && "bg-brand-subtle/60",
                    !isSelected && "opacity-75"
                  )}
                >
                  {/* Linha 1: Checkbox + Entidade + Valor + Botão Ver Pág. */}
                  <div className="flex items-start gap-2">
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => handleCheckboxToggle(occ)}
                        className="mt-0.5 h-4 w-4 shrink-0 rounded accent-brand-primary"
                        data-testid={`occ-checkbox-${occ.id}`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-[13px] font-bold text-ink-primary">
                            {occ.entity_type}
                          </span>
                          {occ.preview_text && (
                            <span className="font-mono text-xs tabular-nums text-ink-secondary">
                              {occ.preview_text}
                            </span>
                          )}
                          {effectiveMode === "PARTIAL_MASK" && (
                            <span className="rounded border border-category-commonBorder bg-category-commonBg px-1.5 py-0.5 font-mono text-[11px] font-medium text-category-commonText">
                              → {maskedPreviewText}
                            </span>
                          )}
                        </div>

                        {/* Linha 2: Categoria + Página + Confiança + Repetições */}
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <Badge variant={catMeta.variant}>
                            {catMeta.shortLabel}
                          </Badge>
                          <span className="font-mono text-[11px] tabular-nums text-ink-muted">
                            Pág. {occ.page_number} · Conf:{" "}
                            {Math.round(occ.confidence_score * 100)}%
                          </span>
                          {repeatCount > 1 && (
                            <Badge
                              variant="neutral"
                              className="font-mono tabular-nums"
                              title="Mesmo valor detectado em múltiplas partes do documento"
                            >
                              {repeatCount}x no doc
                            </Badge>
                          )}
                        </div>
                      </div>
                    </label>

                    <Button
                      variant="secondary"
                      size="xs"
                      onClick={() => {
                        onPageChange(occ.page_number);
                        onSelectOccurrence(occ.id);
                      }}
                      className="ml-auto shrink-0"
                    >
                      Ver Pág. {occ.page_number}
                    </Button>
                  </div>

                  {/* Linha 3: Seletor Segmentado 3-Estados (Tarjar / Mascarar / Manter) */}
                  <SegmentedControl
                    ariaLabel={`Modo de proteção para ${occ.entity_type}`}
                    value={effectiveMode}
                    onChange={(mode) => handleSelectModeButton(occ, mode)}
                    className="mt-2.5 w-full"
                    options={[
                      {
                        value: "BLACKOUT",
                        label: "■ Tarjar",
                        testId: `mode-blackout-${occ.id}`,
                        activeClassName:
                          "bg-ink-primary text-white shadow-xs",
                      },
                      {
                        value: "PARTIAL_MASK",
                        label: "*** Mascarar",
                        title: `Mascarar parcialmente: ${maskedPreviewText}`,
                        testId: `mode-mask-${occ.id}`,
                        activeClassName:
                          "bg-surface-card text-ink-primary ring-1 ring-inset ring-ink-primary",
                      },
                      {
                        value: "KEEP",
                        label: "○ Manter",
                        testId: `mode-keep-${occ.id}`,
                        activeClassName:
                          "bg-amber-100 text-amber-950 ring-1 ring-inset ring-amber-500",
                      },
                    ]}
                  />

                  {repeatCount > 1 && (
                    <div className="mt-1.5 flex items-center justify-end text-[11px]">
                      <button
                        type="button"
                        onClick={() =>
                          onSetGroupSelection(occ.group_id, !isSelected)
                        }
                        className="font-semibold text-brand-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-1"
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
          <div className="flex items-center justify-between border-t border-line-subtle px-4 py-2 text-xs">
            <Button
              variant="secondary"
              size="xs"
              disabled={effectiveSlicePage <= 1}
              onClick={() => setListSlicePage((p) => Math.max(1, p - 1))}
            >
              Bloco Anterior
            </Button>
            <span className="font-mono text-[11px] tabular-nums text-ink-muted">
              Lista {effectiveSlicePage} de {totalSlicePages}
            </span>
            <Button
              variant="secondary"
              size="xs"
              disabled={effectiveSlicePage >= totalSlicePages}
              onClick={() =>
                setListSlicePage((p) => Math.min(totalSlicePages, p + 1))
              }
            >
              Próximo Bloco
            </Button>
          </div>
        )}

        {/* Tarjas Manuais Adicionadas pelo Servidor */}
        {manualBoxes.length > 0 && (
          <div className="border-t border-line-subtle px-4 py-3">
            <div className="mb-2 flex items-center justify-between text-xs font-semibold text-ink-primary">
              <span className="flex items-center gap-1.5">
                <Layers
                  className="h-3.5 w-3.5 text-ink-muted"
                  aria-hidden="true"
                />
                Tarjas Manuais ({manualBoxes.length})
              </span>
            </div>
            <ul className="max-h-32 space-y-1.5 overflow-y-auto">
              {manualBoxes.map((mbox, idx) => (
                <li
                  key={mbox.id}
                  className="flex items-center justify-between rounded-lg border border-line-subtle bg-surface-muted px-2.5 py-1.5 text-xs"
                >
                  <button
                    type="button"
                    onClick={() => onPageChange(mbox.page_number)}
                    className="font-medium text-ink-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus focus-visible:ring-offset-1"
                  >
                    Tarja Manual #{idx + 1} • Pág. {mbox.page_number} (
                    {PII_CATEGORY_STYLES[mbox.category]?.shortLabel ||
                      mbox.category}
                    )
                  </button>
                  <Button
                    variant="danger-outline"
                    size="xs"
                    onClick={() => onRemoveManualBox(mbox.id)}
                    className="h-6 px-2 text-[11px]"
                    title="Remover esta tarja manual"
                  >
                    <Trash2 className="h-3 w-3" aria-hidden="true" />
                    <span>Excluir</span>
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* Bloco 5 — CTA Irreversível */}
      <section
        className="border-t border-line-subtle bg-surface-card px-4 py-3.5"
        aria-label="Aplicação do tarjamento"
      >
        <Button
          variant="danger"
          size="lg"
          disabled={isApplyingRedaction}
          onClick={onOpenConfirmRedactionModal}
          className="w-full tabular-nums"
          data-testid="open-confirm-redaction-modal-btn"
        >
          <Shield className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {isApplyingRedaction
              ? "Executando Tarjamento Físico..."
              : `Aplicar Tarjamento Definitivo (${totalRedactionsToApply} tarjas)`}
          </span>
        </Button>
      </section>
    </aside>
  );
}
