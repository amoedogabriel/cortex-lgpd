"use client";

import React from "react";
import { cn } from "@/lib/utils";
import type { PiiCategory } from "@/types/document";

export type BadgeVariant =
  | "neutral"
  | "brand"
  | "info"
  | "warning"
  | "danger"
  | "common"
  | "sensitive"
  | "financial"
  | "minor";

const BADGE_CLASSES: Record<BadgeVariant, string> = {
  neutral: "border-line-subtle bg-surface-subtle text-ink-secondary",
  brand: "border-brand-border bg-brand-subtle text-brand-ink",
  info: "border-category-commonBorder bg-category-commonBg text-category-commonText",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-red-200 bg-red-50 text-red-800",
  common: "border-category-commonBorder bg-category-commonBg text-category-commonText",
  sensitive: "border-category-sensitiveBorder bg-category-sensitiveBg text-category-sensitiveText",
  financial: "border-category-financialBorder bg-category-financialBg text-category-financialText",
  minor: "border-category-minorBorder bg-category-minorBg text-category-minorText",
};

export interface CategoryStyleMetadata {
  label: string;
  shortLabel: string;
  variant: BadgeVariant;
  badgeClass: string;
  dotClass: string;
  svgStroke: string;
  svgFillSelected: string;
}

/**
 * Paleta taxonômica canônica da UFAM (única fonte de verdade no frontend):
 * 1. Pessoal Comum = Azul
 * 2. Pessoal Sensível = Vermelho
 * 3. Financeiro / Bancário = Violeta
 * 4. Menor / Dependente = Âmbar
 */
export const PII_CATEGORY_STYLES: Record<PiiCategory, CategoryStyleMetadata> = {
  PERSONAL_COMMON: {
    label: "Dado Pessoal Comum",
    shortLabel: "Comum",
    variant: "common",
    badgeClass: BADGE_CLASSES.common,
    dotClass: "bg-category-commonAccent",
    svgStroke: "#2563eb",
    svgFillSelected: "rgba(37, 99, 235, 0.22)",
  },
  PERSONAL_SENSITIVE: {
    label: "Dado Pessoal Sensível (Art. 5º, II)",
    shortLabel: "Sensível",
    variant: "sensitive",
    badgeClass: BADGE_CLASSES.sensitive,
    dotClass: "bg-category-sensitiveAccent",
    svgStroke: "#dc2626",
    svgFillSelected: "rgba(220, 38, 38, 0.24)",
  },
  FINANCIAL_BANKING: {
    label: "Dado Financeiro / Bancário",
    shortLabel: "Financeiro",
    variant: "financial",
    badgeClass: BADGE_CLASSES.financial,
    dotClass: "bg-category-financialAccent",
    svgStroke: "#7c3aed",
    svgFillSelected: "rgba(124, 58, 237, 0.22)",
  },
  MINOR_OR_DEPENDENT: {
    label: "Menor / Dependente (Art. 14)",
    shortLabel: "Menor/Dep.",
    variant: "minor",
    badgeClass: BADGE_CLASSES.minor,
    dotClass: "bg-category-minorAccent",
    svgStroke: "#d97706",
    svgFillSelected: "rgba(217, 119, 6, 0.24)",
  },
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({
  variant = "neutral",
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold leading-tight",
        BADGE_CLASSES[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}
