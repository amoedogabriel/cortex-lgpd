"use client";

/**
 * Visualizador Single-Page WebP + SVG (`frontend/src/components/workspace/LightweightPageViewer.tsx`).
 *
 * Requisitos atendidos:
 * 1. DOM Virtualizado de Página Única: monta exclusivamente a página ativa (`currentPage`) no DOM (< 80 MB de RAM).
 * 2. Preload silencioso em background da página seguinte (`new Image().src = nextPageImageUrl`) quando pronta.
 * 3. Controles simples de zoom (`100%`, `125%`, `150%`).
 * 4. Sobreposição vetorial `<svg viewBox="0 0 1000 1000" preserveAspectRatio="none">` com cores semânticas estritas:
 *    - `PERSONAL_COMMON`: Azul (`stroke="#2563eb"`)
 *    - `PERSONAL_SENSITIVE`: Vermelho (`stroke="#dc2626"`)
 *    - `FINANCIAL_BANKING`: Violeta (`stroke="#7c3aed"`)
 *    - `MINOR_OR_DEPENDENT`: Âmbar (`stroke="#d97706"`)
 *    - `MANUAL`: Cinza escuro (`stroke="#1f2937"`)
 * 5. Visualização Diferenciada dos 3 Modos (`BLACKOUT`, `PARTIAL_MASK`, `KEEP`):
 *    - `BLACKOUT`: retângulo sólido escuro / colorido da categoria (ou `#000000` no Modo Prévia Final);
 *    - `PARTIAL_MASK`: retângulo de fundo branco/âmbar claro (`#fffbeb` / `#ffffff`) com borda azul (`#1d4ed8`)
 *      e texto `<text>` centralizado em fonte ampliada e negrito (`fontWeight="700"`) mostrando `***.729.160-**`;
 *    - `KEEP`: contorno tracejado (`strokeDasharray="6,4"`) com fundo transparente.
 * 6. Desenho e redimensionamento de tarjas manuais a 60 FPS via manipulação direta de DOM com `useRef`
 *    (`draftRectRef.current.setAttribute(...)`), sem chamar `useState` durante o `onMouseMove`.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Crosshair,
  Eye,
  EyeOff,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  DetectedOccurrence,
  ManualRedactionBox,
  PiiCategory,
  RedactionActionMode,
} from "@/types/document";

export type ZoomLevel = 100 | 125 | 150;

export interface LightweightPageViewerProps {
  currentPage: number;
  totalPages: number;
  pageImageUrl: string | null;
  nextPageImageUrl: string | null;
  pageOccurrences: DetectedOccurrence[];
  pageManualBoxes: ManualRedactionBox[];
  deselectedIds: Set<string>;
  actionModesById?: Record<string, RedactionActionMode>;
  activeOccurrenceId: string | null;
  isManualDrawMode: boolean;
  manualDrawCategory: PiiCategory;
  onPageChange: (nextPage: number) => void;
  onToggleOccurrence: (occId: string) => void;
  onSelectOccurrence: (occId: string | null) => void;
  onToggleManualDrawMode: () => void;
  onManualDrawCategoryChange: (cat: PiiCategory) => void;
  onAddManualBox: (box: Omit<ManualRedactionBox, "id">) => void;
  onUpdateManualBox: (id: string, updated: Partial<ManualRedactionBox>) => void;
  onRemoveManualBox: (id: string) => void;
}

interface SemanticSvgPalette {
  stroke: string;
  fillSelected: string;
  label: string;
}

export const SEMANTIC_SVG_COLORS: Record<PiiCategory | "MANUAL", SemanticSvgPalette> = {
  PERSONAL_COMMON: {
    stroke: "#2563eb",
    fillSelected: "rgba(37, 99, 235, 0.22)",
    label: "Dado Pessoal Comum",
  },
  PERSONAL_SENSITIVE: {
    stroke: "#dc2626",
    fillSelected: "rgba(220, 38, 38, 0.24)",
    label: "Dado Pessoal Sensível (Art. 5º, II)",
  },
  FINANCIAL_BANKING: {
    stroke: "#7c3aed",
    fillSelected: "rgba(124, 58, 237, 0.22)",
    label: "Financeiro / Bancário",
  },
  MINOR_OR_DEPENDENT: {
    stroke: "#d97706",
    fillSelected: "rgba(217, 119, 6, 0.24)",
    label: "Menor / Dependente (Art. 14)",
  },
  MANUAL: {
    stroke: "#1f2937",
    fillSelected: "rgba(31, 41, 55, 0.4)",
    label: "Tarja Manual do Servidor",
  },
};

interface DragInteractionRefState {
  mode: "NONE" | "DRAW_NEW" | "RESIZE_MANUAL";
  startXSvg: number;
  startYSvg: number;
  currentXSvg: number;
  currentYSvg: number;
  targetManualId: string | null;
  origX0Svg: number;
  origY0Svg: number;
}

function clampSvgCoord(v: number): number {
  return Math.max(0, Math.min(1000, v));
}

function resolveOccurrenceEffectiveMode(
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

export function LightweightPageViewer({
  currentPage,
  totalPages,
  pageImageUrl,
  nextPageImageUrl,
  pageOccurrences,
  pageManualBoxes,
  deselectedIds,
  actionModesById,
  activeOccurrenceId,
  isManualDrawMode,
  manualDrawCategory,
  onPageChange,
  onToggleOccurrence,
  onSelectOccurrence,
  onToggleManualDrawMode,
  onManualDrawCategoryChange,
  onAddManualBox,
  onUpdateManualBox,
  onRemoveManualBox,
}: LightweightPageViewerProps) {
  const [zoomLevel, setZoomLevel] = useState<ZoomLevel>(100);
  const [isFinalPreviewMode, setIsFinalPreviewMode] = useState<boolean>(false);
  const [selectedManualBoxId, setSelectedManualBoxId] = useState<string | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const draftRectRef = useRef<SVGRectElement | null>(null);
  const dragStateRef = useRef<DragInteractionRefState>({
    mode: "NONE",
    startXSvg: 0,
    startYSvg: 0,
    currentXSvg: 0,
    currentYSvg: 0,
    targetManualId: null,
    origX0Svg: 0,
    origY0Svg: 0,
  });

  // 1. Preload silencioso em background exclusivamente da próxima página (`currentPage + 1`)
  useEffect(() => {
    if (!nextPageImageUrl) return;
    const preloader = new Image();
    preloader.decoding = "async";
    preloader.src = nextPageImageUrl;
  }, [nextPageImageUrl]);

  // Limpa seleção manual ao trocar de página
  useEffect(() => {
    setSelectedManualBoxId(null);
  }, [currentPage]);

  // Converte coordenadas de ponteiro do navegador para o sistema [0..1000] do SVG sem causar reflow pesado
  const clientPointToSvgCoords = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } => {
      const svgEl = svgRef.current;
      if (!svgEl) return { x: 0, y: 0 };
      const rect = svgEl.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return { x: 0, y: 0 };
      const relX = ((clientX - rect.left) / rect.width) * 1000;
      const relY = ((clientY - rect.top) / rect.height) * 1000;
      return {
        x: clampSvgCoord(relX),
        y: clampSvgCoord(relY),
      };
    },
    []
  );

  // Inicia desenho ou redimensionamento de tarja manual (Zero useState durante movimento)
  const handleSvgMouseDown = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (!isManualDrawMode) return;
      if (e.button !== 0) return;

      e.preventDefault();
      const pt = clientPointToSvgCoords(e.clientX, e.clientY);

      dragStateRef.current = {
        mode: "DRAW_NEW",
        startXSvg: pt.x,
        startYSvg: pt.y,
        currentXSvg: pt.x,
        currentYSvg: pt.y,
        targetManualId: null,
        origX0Svg: pt.x,
        origY0Svg: pt.y,
      };

      const draftEl = draftRectRef.current;
      if (draftEl) {
        draftEl.setAttribute("x", pt.x.toFixed(1));
        draftEl.setAttribute("y", pt.y.toFixed(1));
        draftEl.setAttribute("width", "0");
        draftEl.setAttribute("height", "0");
        draftEl.setAttribute("visibility", "visible");
      }
    },
    [isManualDrawMode, clientPointToSvgCoords]
  );

  const handleStartResizeManualBox = useCallback(
    (e: React.MouseEvent<SVGRectElement>, mbox: ManualRedactionBox) => {
      e.stopPropagation();
      e.preventDefault();
      const pt = clientPointToSvgCoords(e.clientX, e.clientY);

      const x0Svg = mbox.x0_norm * 1000;
      const y0Svg = mbox.y0_norm * 1000;

      dragStateRef.current = {
        mode: "RESIZE_MANUAL",
        startXSvg: x0Svg,
        startYSvg: y0Svg,
        currentXSvg: pt.x,
        currentYSvg: pt.y,
        targetManualId: mbox.id,
        origX0Svg: x0Svg,
        origY0Svg: y0Svg,
      };

      const draftEl = draftRectRef.current;
      if (draftEl) {
        draftEl.setAttribute("x", x0Svg.toFixed(1));
        draftEl.setAttribute("y", y0Svg.toFixed(1));
        draftEl.setAttribute("width", Math.max(4, pt.x - x0Svg).toFixed(1));
        draftEl.setAttribute("height", Math.max(4, pt.y - y0Svg).toFixed(1));
        draftEl.setAttribute("visibility", "visible");
      }
    },
    [clientPointToSvgCoords]
  );

  // REGRA CRÍTICA DE 60 FPS: Jamais invocar `useState` dentro de `onMouseMove`
  const handleSvgMouseMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      const st = dragStateRef.current;
      if (st.mode === "NONE") return;

      const pt = clientPointToSvgCoords(e.clientX, e.clientY);
      st.currentXSvg = pt.x;
      st.currentYSvg = pt.y;

      const x0 = Math.min(st.startXSvg, pt.x);
      const y0 = Math.min(st.startYSvg, pt.y);
      const w = Math.abs(pt.x - st.startXSvg);
      const h = Math.abs(pt.y - st.startYSvg);

      const draftEl = draftRectRef.current;
      if (draftEl) {
        draftEl.setAttribute("x", x0.toFixed(1));
        draftEl.setAttribute("y", y0.toFixed(1));
        draftEl.setAttribute("width", w.toFixed(1));
        draftEl.setAttribute("height", h.toFixed(1));
      }
    },
    [clientPointToSvgCoords]
  );

  // Persiste no estado do React SOMENTE UMA VEZ no `onMouseUp`
  const finishDragInteraction = useCallback(() => {
    const st = dragStateRef.current;
    if (st.mode === "NONE") return;

    const draftEl = draftRectRef.current;
    if (draftEl) {
      draftEl.setAttribute("visibility", "hidden");
      draftEl.setAttribute("width", "0");
      draftEl.setAttribute("height", "0");
    }

    const rawX0 = Math.min(st.startXSvg, st.currentXSvg);
    const rawY0 = Math.min(st.startYSvg, st.currentYSvg);
    const rawX1 = Math.max(st.startXSvg, st.currentXSvg);
    const rawY1 = Math.max(st.startYSvg, st.currentYSvg);

    const widthSvg = rawX1 - rawX0;
    const heightSvg = rawY1 - rawY0;

    const modeCompleted = st.mode;
    const targetId = st.targetManualId;
    dragStateRef.current.mode = "NONE";
    dragStateRef.current.targetManualId = null;

    // Ignora cliques acidentais minúsculos (< 6 unidades em 0..1000)
    if (widthSvg < 6 || heightSvg < 6) {
      return;
    }

    const x0_norm = Number((rawX0 / 1000).toFixed(6));
    const y0_norm = Number((rawY0 / 1000).toFixed(6));
    const x1_norm = Number((rawX1 / 1000).toFixed(6));
    const y1_norm = Number((rawY1 / 1000).toFixed(6));

    if (modeCompleted === "DRAW_NEW") {
      onAddManualBox({
        page_number: currentPage,
        x0_norm,
        y0_norm,
        x1_norm,
        y1_norm,
        category: manualDrawCategory,
        label: "Tarja Manual",
      });
    } else if (modeCompleted === "RESIZE_MANUAL" && targetId) {
      onUpdateManualBox(targetId, {
        x0_norm,
        y0_norm,
        x1_norm,
        y1_norm,
      });
    }
  }, [currentPage, manualDrawCategory, onAddManualBox, onUpdateManualBox]);

  const activeSelectedManualBox = useMemo<ManualRedactionBox | null>(() => {
    if (!selectedManualBoxId) return null;
    return pageManualBoxes.find((b) => b.id === selectedManualBoxId) || null;
  }, [pageManualBoxes, selectedManualBoxId]);

  const containerMaxWidthStyle = useMemo<React.CSSProperties>(() => {
    if (zoomLevel === 125) return { width: "125%", maxWidth: "none" };
    if (zoomLevel === 150) return { width: "150%", maxWidth: "none" };
    return { width: "100%", maxWidth: "820px" };
  }, [zoomLevel]);

  return (
    <section
      className="overflow-hidden rounded-[12px] border border-[#e2e8f0] bg-white shadow-[0_1px_2px_rgba(15,23,42,0.05)]"
      aria-label="Visualizador de página e sobreposição de tarjas"
    >
      {/* Toolbar Principal em Linha Única */}
      <div className="flex flex-wrap items-center gap-2 border-b border-[#e2e8f0] px-4 py-3">
        {/* Paginação Single-Page */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            disabled={currentPage <= 1}
            onClick={() => onPageChange(Math.max(1, currentPage - 1))}
            title="Página Anterior (Atalho: Seta Esquerda)"
            className="inline-flex h-[34px] items-center gap-1 rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="prev-page-btn"
          >
            <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Anterior</span>
          </button>

          <span
            aria-live="polite"
            className="px-2 font-mono text-[13px] font-semibold tabular-nums text-[#0f172a]"
            data-testid="current-page-indicator"
          >
            Página {currentPage} de {Math.max(1, totalPages)}
          </span>

          <button
            type="button"
            disabled={currentPage >= totalPages}
            onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
            title="Próxima Página (Atalho: Seta Direita)"
            className="inline-flex h-[34px] items-center gap-1 rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#0f172a] transition-all duration-200 hover:bg-[#f1f5f9] disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
            data-testid="next-page-btn"
          >
            <span>Próxima</span>
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>

        {/* Controles à Direita: Zoom Segmentado + Desenho Manual + Modo Prévia Final */}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/* Controle Segmentado de Zoom (100%, 125%, 150%) */}
          <div
            className="grid grid-cols-3 gap-[2px] rounded-[8px] bg-[#f1f5f9] p-[2px]"
            role="group"
            aria-label="Nível de zoom"
          >
            {([100, 125, 150] as ZoomLevel[]).map((level) => (
              <button
                key={level}
                type="button"
                aria-pressed={zoomLevel === level}
                onClick={() => setZoomLevel(level)}
                className={cn(
                  "h-[30px] w-14 rounded-[6px] font-mono text-[12px] font-semibold tabular-nums transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1",
                  zoomLevel === level
                    ? "bg-[#0f172a] text-white"
                    : "bg-transparent text-[#334155] hover:bg-white hover:text-[#0f172a]"
                )}
                data-testid={`zoom-btn-${level}`}
              >
                {level}%
              </button>
            ))}
          </div>

          <button
            type="button"
            aria-pressed={isManualDrawMode}
            onClick={onToggleManualDrawMode}
            title="Ativar/Desativar Desenho de Tarja Manual com o mouse (Atalho: tecla M)"
            className={cn(
              "inline-flex h-[34px] items-center gap-1.5 whitespace-nowrap rounded-[8px] border px-3 text-[12px] font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2",
              isManualDrawMode
                ? "border-[#0f172a] bg-[#0f172a] text-amber-300"
                : "border-[#cbd5e1] bg-white text-[#0f172a] hover:bg-[#f1f5f9]"
            )}
            data-testid="toggle-manual-draw-btn"
          >
            <Crosshair className="h-3.5 w-3.5" aria-hidden="true" />
            <span>
              {isManualDrawMode ? "Desenho Manual: ATIVO" : "Desenhar Tarja Manual"}
            </span>{" "}
            <kbd className="rounded-[5px] border border-[#cbd5e1] border-b-2 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold leading-none text-[#334155]">
              <span className="sr-only">(</span>M<span className="sr-only">)</span>
            </kbd>
          </button>

          {isManualDrawMode && (
            <select
              aria-label="Categoria LGPD para novas tarjas manuais"
              value={manualDrawCategory}
              onChange={(e) => onManualDrawCategoryChange(e.target.value as PiiCategory)}
              className="h-[34px] rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-medium text-[#0f172a] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2"
              data-testid="manual-draw-category-select"
            >
              <option value="PERSONAL_COMMON">Cat: Pessoal Comum</option>
              <option value="PERSONAL_SENSITIVE">Cat: Sensível (Art. 5º, II)</option>
              <option value="FINANCIAL_BANKING">Cat: Financeiro / Bancário</option>
              <option value="MINOR_OR_DEPENDENT">Cat: Menor / Dependente</option>
            </select>
          )}

          <button
            type="button"
            aria-pressed={isFinalPreviewMode}
            onClick={() => setIsFinalPreviewMode((prev) => !prev)}
            title="Alterna entre cores de revisão por categoria e prévia final de saída"
            className={cn(
              "inline-flex h-[34px] items-center gap-1.5 whitespace-nowrap rounded-[8px] border px-3 text-[12px] font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-2",
              isFinalPreviewMode
                ? "border-[#0f172a] bg-[#0f172a] text-white"
                : "border-[#cbd5e1] bg-white text-[#0f172a] hover:bg-[#f1f5f9]"
            )}
            data-testid="toggle-final-preview-btn"
          >
            {isFinalPreviewMode ? (
              <>
                <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Modo Prévia Final (#000000): ATIVO</span>
              </>
            ) : (
              <>
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Modo Prévia Final</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Barra de Legenda Compacta em Chips Pastéis */}
      <div className="flex flex-wrap items-center gap-2 border-b border-[#e2e8f0] bg-[#f8fafc] px-4 py-2.5">
        <ul className="flex flex-wrap items-center gap-2" aria-label="Legenda de categorias">
          <li className="inline-flex h-[28px] items-center gap-1.5 rounded-full border border-[#bfdbfe] bg-[#eff6ff] px-2.5 text-[12px] font-semibold text-[#1d4ed8]">
            <span className="inline-block h-2 w-2 rounded-full bg-[#2563eb]" aria-hidden="true" />
            Pessoal Comum
          </li>
          <li className="inline-flex h-[28px] items-center gap-1.5 rounded-full border border-[#fecaca] bg-[#fef2f2] px-2.5 text-[12px] font-semibold text-[#b91c1c]">
            <span className="inline-block h-2 w-2 rounded-full bg-[#dc2626]" aria-hidden="true" />
            Sensível (Art. 5º, II)
          </li>
          <li className="inline-flex h-[28px] items-center gap-1.5 rounded-full border border-[#ddd6fe] bg-[#f5f3ff] px-2.5 text-[12px] font-semibold text-[#6d28d9]">
            <span className="inline-block h-2 w-2 rounded-full bg-[#7c3aed]" aria-hidden="true" />
            Financeiro / Bancário
          </li>
          <li className="inline-flex h-[28px] items-center gap-1.5 rounded-full border border-[#fde68a] bg-[#fffbeb] px-2.5 text-[12px] font-semibold text-[#b45309]">
            <span className="inline-block h-2 w-2 rounded-full bg-[#d97706]" aria-hidden="true" />
            Menor / Dependente
          </li>
          <li className="inline-flex h-[28px] items-center gap-1.5 rounded-full border border-[#cbd5e1] bg-white px-2.5 text-[12px] font-semibold text-[#334155]">
            <span aria-hidden="true" className="font-mono text-[11px] font-bold text-[#0f172a]">
              ***.XXX-**
            </span>
            Mascaramento Parcial
          </li>
        </ul>

        <div className="ml-auto text-[11px] text-[#64748b]">
          <strong className="font-semibold text-[#334155]">Preenchido:</strong> Tarja Total •{" "}
          <strong className="font-semibold text-[#334155]">***.XXX:</strong> Máscara Parcial •{" "}
          <strong className="font-semibold text-[#334155]">Tracejado:</strong> Mantido
        </div>
      </div>

      {/* Barra Contextual quando uma Tarja Manual está selecionada para ajuste/exclusão */}
      {activeSelectedManualBox && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 border-b border-[#0f172a] bg-[#0f172a] px-4 py-2 text-[12px] text-white"
          data-testid="selected-manual-box-toolbar"
        >
          <span>
            <strong className="font-semibold text-amber-300">Tarja Manual Selecionada</strong> (Pág. {activeSelectedManualBox.page_number}) — Arraste o quadradinho no canto inferior direito da tarja para redimensionar ou clique em excluir.
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                onRemoveManualBox(activeSelectedManualBox.id);
                setSelectedManualBoxId(null);
              }}
              className="inline-flex items-center gap-1 rounded-lg border border-red-700 bg-red-800 px-2.5 py-1 font-semibold text-white transition-colors duration-200 hover:bg-red-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2"
              data-testid="delete-manual-box-btn"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Excluir Tarja Manual</span>
            </button>
            <button
              type="button"
              onClick={() => setSelectedManualBoxId(null)}
              className="rounded-lg border border-slate-600 bg-slate-800 px-2.5 py-1 text-slate-200 transition-colors duration-200 hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2"
            >
              Fechar
            </button>
          </div>
        </div>
      )}

      {/* Viewport de Página Única sobre Canvas de Alto Contraste (DOM Virtualizado: monta APENAS a página atual) */}
      <div className="overflow-auto bg-[#e2e8f0] p-6">
        <div
          className="relative mx-auto overflow-hidden rounded-sm border border-[#cbd5e1] bg-white shadow-[0_4px_12px_rgba(15,23,42,0.12)]"
          style={containerMaxWidthStyle}
        >
          {pageImageUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={`single-page-webp-${currentPage}`}
              src={pageImageUrl}
              alt={`Página ${currentPage} renderizada em WebP`}
              decoding="async"
              draggable={false}
              className="block h-auto w-full select-none"
              data-testid="page-webp-image"
            />
          ) : (
            <div className="flex h-96 items-center justify-center bg-[#f8fafc] text-[13px] font-medium text-[#475569]">
              Aguardando renderização da Página {currentPage}...
            </div>
          )}

          {/* Camada Vetorial Única <svg viewBox="0 0 1000 1000"> */}
          <svg
            ref={svgRef}
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
            onMouseDown={handleSvgMouseDown}
            onMouseMove={handleSvgMouseMove}
            onMouseUp={finishDragInteraction}
            onMouseLeave={finishDragInteraction}
            className={cn(
              "absolute inset-0 h-full w-full select-none",
              isManualDrawMode ? "cursor-crosshair" : "cursor-default"
            )}
            data-testid="page-overlay-svg"
          >
            {/* 1. Ocorrências Automáticas da Página Atual */}
            {pageOccurrences.map((occ) => {
              const effectiveMode = resolveOccurrenceEffectiveMode(
                occ,
                deselectedIds,
                actionModesById
              );
              const isSelectedForRedaction = effectiveMode !== "KEEP";
              const isPartialMask = effectiveMode === "PARTIAL_MASK";
              const isFocused = activeOccurrenceId === occ.id;
              const palette =
                SEMANTIC_SVG_COLORS[occ.category] || SEMANTIC_SVG_COLORS.PERSONAL_COMMON;

              const x = occ.x0_norm * 1000;
              const y = occ.y0_norm * 1000;
              const width = Math.max(4, (occ.x1_norm - occ.x0_norm) * 1000);
              const height = Math.max(4, (occ.y1_norm - occ.y0_norm) * 1000);

              let fillValue = "transparent";
              let strokeValue = palette.stroke;
              let dashArray: string | undefined = "6,4";

              if (isPartialMask) {
                dashArray = undefined;
                fillValue = isFinalPreviewMode ? "#ffffff" : "#fffbeb";
                strokeValue = "#1d4ed8";
              } else if (isSelectedForRedaction) {
                dashArray = undefined;
                if (isFinalPreviewMode) {
                  fillValue = "#000000";
                  strokeValue = "#000000";
                } else {
                  fillValue = palette.fillSelected;
                  strokeValue = palette.stroke;
                }
              }

              const isBlankContinuation =
                Boolean(occ.is_continuation) || occ.masked_preview === "";
              const maskSvgText = isBlankContinuation
                ? ""
                : occ.masked_preview || "***.XXX-**";

              const modeLabel =
                effectiveMode === "PARTIAL_MASK"
                  ? isBlankContinuation
                    ? "MASCARAMENTO PARCIAL (continuação em branco)"
                    : `MASCARAMENTO PARCIAL (${occ.masked_preview || "***.XXX.XXX-**"})`
                  : effectiveMode === "BLACKOUT"
                  ? "TARJA PRETA TOTAL (100%)"
                  : "MANTIDO VISÍVEL";

              return (
                <g key={occ.id}>
                  <rect
                    data-testid={`svg-occurrence-rect-${occ.id}`}
                    x={x}
                    y={y}
                    width={width}
                    height={height}
                    fill={fillValue}
                    stroke={strokeValue}
                    strokeWidth={isFocused ? 3 : isSelectedForRedaction ? 2 : 1.5}
                    strokeDasharray={dashArray}
                    className="cursor-pointer"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectOccurrence(occ.id);
                      onToggleOccurrence(occ.id);
                    }}
                  >
                    <title>
                      {`${occ.entity_type} (${palette.label}) — Confiança: ${Math.round(
                        occ.confidence_score * 100
                      )}% — Modo: ${modeLabel} (Clique para alternar)`}
                    </title>
                  </rect>

                  {isPartialMask && maskSvgText && (
                    <text
                      data-testid={`svg-occurrence-mask-text-${occ.id}`}
                      x={x + width / 2}
                      y={y + height * 0.8}
                      textAnchor="middle"
                      fill="#0f172a"
                      fontSize={Math.max(14, height * 0.92)}
                      fontFamily="monospace"
                      fontWeight="700"
                      pointerEvents="none"
                    >
                      {maskSvgText}
                    </text>
                  )}
                </g>
              );
            })}

            {/* 2. Tarjas Manuais Desenhadas pelo Servidor na Página Atual */}
            {pageManualBoxes.map((mbox) => {
              const isManualFocused = selectedManualBoxId === mbox.id;
              const manualPalette = SEMANTIC_SVG_COLORS.MANUAL;

              const x = mbox.x0_norm * 1000;
              const y = mbox.y0_norm * 1000;
              const width = Math.max(6, (mbox.x1_norm - mbox.x0_norm) * 1000);
              const height = Math.max(6, (mbox.y1_norm - mbox.y0_norm) * 1000);

              const fillValue = isFinalPreviewMode
                ? "#000000"
                : manualPalette.fillSelected;
              const strokeValue = isFinalPreviewMode
                ? "#000000"
                : manualPalette.stroke;

              return (
                <g key={mbox.id}>
                  <rect
                    data-testid={`svg-manual-rect-${mbox.id}`}
                    x={x}
                    y={y}
                    width={width}
                    height={height}
                    fill={fillValue}
                    stroke={strokeValue}
                    strokeWidth={isManualFocused ? 3 : 2}
                    className="cursor-pointer"
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedManualBoxId((prev) =>
                        prev === mbox.id ? null : mbox.id
                      );
                    }}
                  >
                    <title>
                      Tarja Manual ({mbox.category}) — Clique para ajustar ou excluir
                    </title>
                  </rect>

                  {/* Handle de Redimensionamento Direto a 60 FPS quando selecionada */}
                  {isManualFocused && !isFinalPreviewMode && (
                    <rect
                      x={Math.min(990, x + width - 10)}
                      y={Math.min(990, y + height - 10)}
                      width={12}
                      height={12}
                      fill="#f59e0b"
                      stroke="#1f2937"
                      strokeWidth={2}
                      className="cursor-se-resize"
                      onMouseDown={(e) => handleStartResizeManualBox(e, mbox)}
                    >
                      <title>Arraste para redimensionar esta tarja manual</title>
                    </rect>
                  )}
                </g>
              );
            })}

            {/* 3. Retângulo Rascunho (`draftRectRef`) manipulado diretamente via DOM nativa a 60 FPS */}
            <rect
              ref={draftRectRef}
              x={0}
              y={0}
              width={0}
              height={0}
              visibility="hidden"
              fill={SEMANTIC_SVG_COLORS.MANUAL.fillSelected}
              stroke={SEMANTIC_SVG_COLORS.MANUAL.stroke}
              strokeWidth={2}
              strokeDasharray="4,2"
              pointerEvents="none"
              data-testid="svg-draft-manual-rect"
            />
          </svg>
        </div>
      </div>
    </section>
  );
}
