"use client";

import React from "react";
import { Kbd } from "@/components/ui";

export function AppFooter() {
  return (
    <footer className="mt-auto border-t border-line-subtle bg-surface-card text-ink-muted">
      <div className="mx-auto flex w-full max-w-[1400px] flex-col items-start justify-between gap-2.5 px-4 py-3 text-xs sm:flex-row sm:items-center sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center gap-3 text-xs text-ink-muted">
          <span className="inline-flex items-center gap-1">
            <Kbd>←</Kbd>
            <Kbd>→</Kbd>
            <span>Navegar páginas</span>
          </span>
          <span className="inline-flex items-center gap-1">
            <Kbd>M</Kbd>
            <span>Desenho manual</span>
          </span>
        </div>

        <p className="text-left text-xs text-ink-muted sm:text-right">
          Universidade Federal do Amazonas (UFAM) • Sistema de Identificação e Tarjamento de Dados Pessoais • Conformidade LGPD (Lei nº 13.709/2018) e LAI (Lei nº 12.527/2011)
        </p>
      </div>
    </footer>
  );
}
