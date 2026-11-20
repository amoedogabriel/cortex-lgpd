"use client";

import React, { useId } from "react";
import { FileCheck2 } from "lucide-react";
import { Badge } from "@/components/ui";

export interface SeiBoilerplateRuleToggleProps {
  checked: boolean;
  onChange: (nextChecked: boolean) => void;
  disabled?: boolean;
}

export function SeiBoilerplateRuleToggle({
  checked,
  onChange,
  disabled = false,
}: SeiBoilerplateRuleToggleProps) {
  const checkboxId = useId();
  const descriptionId = `${checkboxId}-desc`;

  return (
    <div
      className="mt-3 rounded-xl border border-line-subtle bg-surface-muted px-4 py-3"
      data-testid="sei-boilerplate-rule-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <label
          htmlFor={checkboxId}
          className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5 text-left"
        >
          <input
            id={checkboxId}
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
            aria-describedby={descriptionId}
            className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded border-line-strong text-brand-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-focus disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="toggle-ignore-sei-blocks-checkbox"
          />
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-1.5 text-[13px] font-semibold text-ink-primary">
              <FileCheck2
                className="h-4 w-4 shrink-0 text-brand-primary"
                aria-hidden="true"
              />
              <span>
                Ignorar cabeçalho, rodapé padrão do SEI e assinatura final de
                despacho
              </span>
            </span>
            <span
              id={descriptionId}
              className="mt-1 block text-xs leading-relaxed text-ink-muted"
            >
              Preserva timbre MEC/UFAM, rodapé institucional SEI, QR Code de
              conferência e bloco de assinatura de despacho. CPFs, dados
              bancários e dados sensíveis no corpo continuam protegidos.
            </span>
          </span>
        </label>

        <Badge variant={checked ? "brand" : "neutral"} className="shrink-0">
          {checked ? "Ativo por padrão" : "Desativado"}
        </Badge>
      </div>
    </div>
  );
}
