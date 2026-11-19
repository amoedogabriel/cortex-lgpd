"use client";

import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Plus, Regex, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  CustomRegexRule,
  PiiCategory,
  RedactionActionMode,
} from "@/types/document";

export interface CustomRegexPanelProps {
  rules: CustomRegexRule[];
  onChange: (nextRules: CustomRegexRule[]) => void;
  disabled?: boolean;
}

const MAX_RULES = 20;
const MAX_PATTERN_LENGTH = 300;
const REDOS_NESTED_RE = /\((?:[^()\\]|\\.)*[*+?{][^()]*\)\s*(?:[*+?]|{\d)/;

const CATEGORY_OPTIONS: {
  value: PiiCategory;
  label: string;
  badgeClass: string;
}[] = [
  {
    value: "PERSONAL_COMMON",
    label: "Dado Pessoal Comum",
    badgeClass: "border-[#bfdbfe] bg-[#eff6ff] text-[#1d4ed8]",
  },
  {
    value: "PERSONAL_SENSITIVE",
    label: "Dado Sensível (Art. 5º, II)",
    badgeClass: "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]",
  },
  {
    value: "FINANCIAL_BANKING",
    label: "Financeiro / Bancário",
    badgeClass: "border-[#ddd6fe] bg-[#f5f3ff] text-[#6d28d9]",
  },
  {
    value: "MINOR_OR_DEPENDENT",
    label: "Menor / Dependente (Art. 14)",
    badgeClass: "border-[#fde68a] bg-[#fffbeb] text-[#b45309]",
  },
];

function validatePatternLocally(pattern: string): string | null {
  const trimmed = pattern.trim();
  if (!trimmed) return null;
  if (pattern.length > MAX_PATTERN_LENGTH) {
    return `O padrão excede o limite de ${MAX_PATTERN_LENGTH} caracteres.`;
  }
  if (REDOS_NESTED_RE.test(pattern)) {
    return "Padrão com quantificadores aninhados pode causar lentidão (ReDoS).";
  }
  try {
    const compiled = new RegExp(pattern);
    if (compiled.test("")) {
      return "O padrão casa texto vazio e geraria marcações inválidas. Torne-o mais específico.";
    }
  } catch (err) {
    return err instanceof Error
      ? `Sintaxe regex inválida: ${err.message}`
      : "Sintaxe regex inválida.";
  }
  return null;
}

export function CustomRegexPanel({
  rules,
  onChange,
  disabled = false,
}: CustomRegexPanelProps) {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [label, setLabel] = useState<string>("");
  const [pattern, setPattern] = useState<string>("");
  const [category, setCategory] = useState<PiiCategory>("PERSONAL_COMMON");
  const [actionMode, setActionMode] =
    useState<RedactionActionMode>("BLACKOUT");
  const [caseSensitive, setCaseSensitive] = useState<boolean>(false);

  const patternError = useMemo(() => validatePatternLocally(pattern), [pattern]);
  const canAdd =
    !disabled &&
    rules.length < MAX_RULES &&
    label.trim().length > 0 &&
    pattern.trim().length > 0 &&
    patternError === null;

  const handleAddRule = () => {
    if (!canAdd) return;
    const nextRule: CustomRegexRule = {
      id: `cr_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      label: label.trim().slice(0, 64),
      pattern,
      category,
      action_mode: actionMode,
      case_sensitive: caseSensitive,
    };
    onChange([...rules, nextRule]);
    setLabel("");
    setPattern("");
    setCaseSensitive(false);
  };

  const handleRemoveRule = (id: string) => {
    onChange(rules.filter((r) => r.id !== id));
  };

  return (
    <div
      className="mt-4 rounded-[10px] border border-[#e2e8f0] bg-[#f8fafc]"
      data-testid="custom-regex-panel"
    >
      <button
        type="button"
        aria-expanded={isExpanded}
        aria-controls="custom-regex-panel-body"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex w-full items-center justify-between gap-3 rounded-[10px] px-4 py-2.5 text-left text-[13px] font-semibold text-[#0f172a] transition-colors duration-200 hover:bg-[#f1f5f9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
        data-testid="custom-regex-toggle-btn"
      >
        <span className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown className="h-4 w-4 text-[#475569]" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-4 w-4 text-[#475569]" aria-hidden="true" />
          )}
          <Regex className="h-4 w-4 text-[#0e5a43]" aria-hidden="true" />
          <span>Regras Regex Personalizadas (Opcional)</span>
        </span>

        <span className="flex items-center gap-2">
          {rules.length > 0 ? (
            <span className="rounded-full border border-[#a7f3d0] bg-[#ecfdf5] px-2.5 py-0.5 font-mono text-[11px] font-semibold text-[#065f46]">
              {rules.length} {rules.length === 1 ? "regra ativa" : "regras ativas"}
            </span>
          ) : (
            <span className="text-[11px] font-normal text-[#64748b]">
              Padrões extras aplicados no próximo envio
            </span>
          )}
        </span>
      </button>

      {isExpanded && (
        <div
          id="custom-regex-panel-body"
          className="space-y-3 border-t border-[#e2e8f0] bg-white p-4 rounded-b-[10px]"
        >
          <p className="text-[12px] leading-relaxed text-[#475569]">
            Cadastre expressões regulares específicas da sua unidade (ex.: códigos de protocolo, identificadores internos ou prefixos próprios). As regras configuradas aqui serão aplicadas em conjunto com o motor automático LGPD/NLP nos próximos PDFs enviados.
          </p>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
            <div className="md:col-span-3">
              <label
                htmlFor="custom-regex-label"
                className="mb-1 block text-[11px] font-semibold text-[#334155]"
              >
                Nome da Regra
              </label>
              <input
                id="custom-regex-label"
                type="text"
                maxLength={64}
                disabled={disabled}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Ex.: CODIGO_PROEG"
                className="h-9 w-full rounded-[8px] border border-[#cbd5e1] bg-white px-2.5 text-[12px] text-[#0f172a] placeholder:text-[#94a3b8] focus:border-[#0e5a43] focus:outline-none focus:ring-1 focus:ring-[#0e5a43]"
                data-testid="custom-regex-label-input"
              />
            </div>

            <div className="md:col-span-4">
              <label
                htmlFor="custom-regex-pattern"
                className="mb-1 block text-[11px] font-semibold text-[#334155]"
              >
                Expressão Regular (Regex)
              </label>
              <input
                id="custom-regex-pattern"
                type="text"
                maxLength={MAX_PATTERN_LENGTH}
                disabled={disabled}
                value={pattern}
                onChange={(e) => setPattern(e.target.value)}
                placeholder="Ex.: PROEG-\d{4}-\d{3}"
                className={cn(
                  "h-9 w-full rounded-[8px] border bg-white px-2.5 font-mono text-[12px] text-[#0f172a] placeholder:text-[#94a3b8] focus:outline-none focus:ring-1",
                  patternError
                    ? "border-[#dc2626] focus:border-[#dc2626] focus:ring-[#dc2626]"
                    : "border-[#cbd5e1] focus:border-[#0e5a43] focus:ring-[#0e5a43]"
                )}
                data-testid="custom-regex-pattern-input"
              />
            </div>

            <div className="md:col-span-3">
              <label
                htmlFor="custom-regex-category"
                className="mb-1 block text-[11px] font-semibold text-[#334155]"
              >
                Categoria LGPD
              </label>
              <select
                id="custom-regex-category"
                disabled={disabled}
                value={category}
                onChange={(e) => setCategory(e.target.value as PiiCategory)}
                className="h-9 w-full rounded-[8px] border border-[#cbd5e1] bg-white px-2 text-[12px] text-[#0f172a] focus:border-[#0e5a43] focus:outline-none focus:ring-1 focus:ring-[#0e5a43]"
                data-testid="custom-regex-category-select"
              >
                {CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="md:col-span-2">
              <label
                htmlFor="custom-regex-mode"
                className="mb-1 block text-[11px] font-semibold text-[#334155]"
              >
                Ação Padrão
              </label>
              <select
                id="custom-regex-mode"
                disabled={disabled}
                value={actionMode}
                onChange={(e) =>
                  setActionMode(e.target.value as RedactionActionMode)
                }
                className="h-9 w-full rounded-[8px] border border-[#cbd5e1] bg-white px-2 text-[12px] text-[#0f172a] focus:border-[#0e5a43] focus:outline-none focus:ring-1 focus:ring-[#0e5a43]"
                data-testid="custom-regex-mode-select"
              >
                <option value="BLACKOUT">Tarja Preta</option>
                <option value="PARTIAL_MASK">Mascarar</option>
              </select>
            </div>
          </div>

          {patternError && (
            <p
              role="alert"
              className="text-[11px] font-medium text-[#b91c1c]"
              data-testid="custom-regex-error"
            >
              {patternError}
            </p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="inline-flex cursor-pointer items-center gap-2 text-[12px] text-[#334155]">
              <input
                type="checkbox"
                disabled={disabled}
                checked={caseSensitive}
                onChange={(e) => setCaseSensitive(e.target.checked)}
                className="h-4 w-4 rounded accent-[#0e5a43]"
                data-testid="custom-regex-case-checkbox"
              />
              <span>Diferenciar maiúsculas e minúsculas</span>
            </label>

            <button
              type="button"
              disabled={!canAdd}
              onClick={handleAddRule}
              className="inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-[#0e5a43] bg-[#0e5a43] px-3 text-[12px] font-semibold text-white transition-colors duration-200 hover:bg-[#0b4a37] disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f] focus-visible:ring-offset-1"
              data-testid="custom-regex-add-btn"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Adicionar Regra ({rules.length}/{MAX_RULES})</span>
            </button>
          </div>

          {rules.length > 0 && (
            <ul
              className="divide-y divide-[#e2e8f0] rounded-[8px] border border-[#e2e8f0] bg-[#f8fafc]"
              aria-label="Lista de regras regex personalizadas ativas"
            >
              {rules.map((rule) => {
                const catMeta =
                  CATEGORY_OPTIONS.find((c) => c.value === rule.category) ||
                  CATEGORY_OPTIONS[0];
                return (
                  <li
                    key={rule.id}
                    className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[12px]"
                    data-testid={`custom-regex-rule-${rule.id}`}
                  >
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="font-semibold text-[#0f172a]">
                        {rule.label}
                      </span>
                      <code className="rounded border border-[#e2e8f0] bg-white px-1.5 py-0.5 font-mono text-[11px] text-[#334155]">
                        /{rule.pattern}/{rule.case_sensitive ? "" : "i"}
                      </code>
                      <span
                        className={cn(
                          "inline-flex h-[20px] items-center rounded-full border px-2 text-[10px] font-semibold",
                          catMeta.badgeClass
                        )}
                      >
                        {catMeta.label}
                      </span>
                      <span className="rounded bg-[#f1f5f9] px-1.5 py-0.5 text-[10px] font-medium text-[#475569]">
                        {rule.action_mode === "PARTIAL_MASK"
                          ? "Mascarar"
                          : "Tarja Preta"}
                      </span>
                    </div>

                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => handleRemoveRule(rule.id)}
                      aria-label={`Remover regra ${rule.label}`}
                      className="inline-flex h-7 w-7 items-center justify-center rounded-[6px] text-[#64748b] transition-colors hover:bg-[#fef2f2] hover:text-[#b91c1c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#0e6b4f]"
                      data-testid={`custom-regex-remove-${rule.id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
