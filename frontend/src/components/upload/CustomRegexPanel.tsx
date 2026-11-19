"use client";

import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Plus, Regex, Trash2 } from "lucide-react";
import { Badge, Button, PII_CATEGORY_STYLES } from "@/components/ui";
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

const CATEGORY_ORDER: PiiCategory[] = [
  "PERSONAL_COMMON",
  "PERSONAL_SENSITIVE",
  "FINANCIAL_BANKING",
  "MINOR_OR_DEPENDENT",
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
      className="mt-4 overflow-hidden rounded-xl border border-line-subtle bg-surface-muted"
      data-testid="custom-regex-panel"
    >
      <button
        type="button"
        aria-expanded={isExpanded}
        aria-controls="custom-regex-panel-body"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex w-full flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-left text-[13px] font-semibold text-ink-primary transition-colors duration-150 hover:bg-surface-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-focus"
        data-testid="custom-regex-toggle-btn"
      >
        <span className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown
              className="h-4 w-4 text-ink-muted"
              aria-hidden="true"
            />
          ) : (
            <ChevronRight
              className="h-4 w-4 text-ink-muted"
              aria-hidden="true"
            />
          )}
          <Regex className="h-4 w-4 text-brand-primary" aria-hidden="true" />
          <span>Regras Regex Personalizadas (Opcional)</span>
        </span>

        <span className="flex items-center gap-2">
          {rules.length > 0 ? (
            <Badge variant="brand" className="font-mono tabular-nums">
              {rules.length}{" "}
              {rules.length === 1 ? "regra ativa" : "regras ativas"}
            </Badge>
          ) : (
            <span className="text-[11px] font-normal text-ink-muted">
              Padrões extras aplicados no próximo envio
            </span>
          )}
        </span>
      </button>

      {isExpanded && (
        <div
          id="custom-regex-panel-body"
          className="space-y-3 border-t border-line-subtle bg-surface-card p-4"
        >
          <p className="text-xs leading-relaxed text-ink-muted">
            Cadastre expressões regulares específicas da sua unidade (ex.:
            códigos de protocolo, identificadores internos ou prefixos
            próprios). As regras configuradas aqui serão aplicadas em conjunto
            com o motor automático LGPD/NLP nos próximos PDFs enviados.
          </p>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-12">
            <div className="lg:col-span-3">
              <label
                htmlFor="custom-regex-label"
                className="mb-1 block text-[11px] font-semibold text-ink-secondary"
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
                className="h-9 w-full rounded-lg border border-line bg-surface-card px-2.5 text-xs text-ink-primary placeholder:text-ink-muted/70 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
                data-testid="custom-regex-label-input"
              />
            </div>

            <div className="lg:col-span-4">
              <label
                htmlFor="custom-regex-pattern"
                className="mb-1 block text-[11px] font-semibold text-ink-secondary"
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
                  "h-9 w-full rounded-lg border bg-surface-card px-2.5 font-mono text-xs text-ink-primary placeholder:text-ink-muted/70 focus:outline-none focus:ring-1",
                  patternError
                    ? "border-red-600 focus:border-red-600 focus:ring-red-600"
                    : "border-line focus:border-brand-primary focus:ring-brand-primary"
                )}
                data-testid="custom-regex-pattern-input"
              />
            </div>

            <div className="lg:col-span-3">
              <label
                htmlFor="custom-regex-category"
                className="mb-1 block text-[11px] font-semibold text-ink-secondary"
              >
                Categoria LGPD
              </label>
              <select
                id="custom-regex-category"
                disabled={disabled}
                value={category}
                onChange={(e) => setCategory(e.target.value as PiiCategory)}
                className="h-9 w-full rounded-lg border border-line bg-surface-card px-2 text-xs text-ink-primary focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
                data-testid="custom-regex-category-select"
              >
                {CATEGORY_ORDER.map((catKey) => (
                  <option key={catKey} value={catKey}>
                    {PII_CATEGORY_STYLES[catKey].label}
                  </option>
                ))}
              </select>
            </div>

            <div className="lg:col-span-2">
              <label
                htmlFor="custom-regex-mode"
                className="mb-1 block text-[11px] font-semibold text-ink-secondary"
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
                className="h-9 w-full rounded-lg border border-line bg-surface-card px-2 text-xs text-ink-primary focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
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
              className="text-[11px] font-medium text-red-700"
              data-testid="custom-regex-error"
            >
              {patternError}
            </p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-ink-secondary">
              <input
                type="checkbox"
                disabled={disabled}
                checked={caseSensitive}
                onChange={(e) => setCaseSensitive(e.target.checked)}
                className="h-4 w-4 rounded accent-brand-primary"
                data-testid="custom-regex-case-checkbox"
              />
              <span>Diferenciar maiúsculas e minúsculas</span>
            </label>

            <Button
              variant="primary"
              size="sm"
              disabled={!canAdd}
              onClick={handleAddRule}
              data-testid="custom-regex-add-btn"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>
                Adicionar Regra ({rules.length}/{MAX_RULES})
              </span>
            </Button>
          </div>

          {rules.length > 0 && (
            <ul
              className="divide-y divide-line-subtle rounded-lg border border-line-subtle bg-surface-muted"
              aria-label="Lista de regras regex personalizadas ativas"
            >
              {rules.map((rule) => {
                const catMeta =
                  PII_CATEGORY_STYLES[rule.category] ||
                  PII_CATEGORY_STYLES.PERSONAL_COMMON;
                return (
                  <li
                    key={rule.id}
                    className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs"
                    data-testid={`custom-regex-rule-${rule.id}`}
                  >
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="font-semibold text-ink-primary">
                        {rule.label}
                      </span>
                      <code className="rounded border border-line-subtle bg-surface-card px-1.5 py-0.5 font-mono text-[11px] text-ink-secondary">
                        /{rule.pattern}/{rule.case_sensitive ? "" : "i"}
                      </code>
                      <Badge variant={catMeta.variant}>{catMeta.label}</Badge>
                      <Badge variant="neutral">
                        {rule.action_mode === "PARTIAL_MASK"
                          ? "Mascarar"
                          : "Tarja Preta"}
                      </Badge>
                    </div>

                    <Button
                      variant="ghost"
                      size="icon"
                      disabled={disabled}
                      onClick={() => handleRemoveRule(rule.id)}
                      aria-label={`Remover regra ${rule.label}`}
                      className="text-ink-muted hover:bg-red-50 hover:text-red-700"
                      data-testid={`custom-regex-remove-${rule.id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
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
