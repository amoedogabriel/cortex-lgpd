import type { Config } from "tailwindcss";

/**
 * Design System Tailwind CSS — Cortex LGPD (UFAM)
 * Otimizado para Hardware Legado (GPU Integrada de 10–15 anos):
 * - Tokens semânticos unificados de superfície, texto, borda, marca UFAM e categorias LGPD.
 * - Taxonomia de cores estrita:
 *   1. Comum = Azul | 2. Sensível = Vermelho | 3. Financeiro = Violeta | 4. Menor/Dependente = Âmbar.
 * - Proibido usar `backdropBlur`, `backdropFilter`, `dropShadow` ou sombras pesadas com grande blur.
 */
const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/hooks/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          page: "var(--surface-page)",
          card: "var(--surface-card)",
          subtle: "var(--surface-subtle)",
          muted: "var(--surface-muted)",
          canvas: "var(--surface-canvas)",
        },
        ink: {
          primary: "var(--ink-primary)",
          secondary: "var(--ink-secondary)",
          muted: "var(--ink-muted)",
        },
        line: {
          subtle: "var(--border-subtle)",
          DEFAULT: "var(--border-default)",
          strong: "var(--border-strong)",
        },
        brand: {
          header: "var(--brand-header)",
          primary: "var(--brand-primary)",
          hover: "var(--brand-primary-hover)",
          focus: "var(--brand-focus)",
          subtle: "var(--brand-subtle)",
          border: "var(--brand-border)",
          ink: "var(--brand-ink)",
        },
        ufam: {
          green: "#0E5A43",
          greenDark: "#0C4736",
          greenLight: "#ECFDF5",
          gold: "#8A6D1B",
          goldLight: "#FFFBEB",
          slate: "#0F172A",
          border: "#CBD5E1",
        },
        category: {
          commonBg: "#EFF6FF",
          commonBorder: "#BFDBFE",
          commonAccent: "#2563EB",
          commonText: "#1D4ED8",
          sensitiveBg: "#FEF2F2",
          sensitiveBorder: "#FECACA",
          sensitiveAccent: "#DC2626",
          sensitiveText: "#B91C1C",
          financialBg: "#F5F3FF",
          financialBorder: "#DDD6FE",
          financialAccent: "#7C3AED",
          financialText: "#6D28D9",
          minorBg: "#FFFBEB",
          minorBorder: "#FDE68A",
          minorAccent: "#D97706",
          minorText: "#B45309",
        },
      },
      boxShadow: {
        none: "none",
        xs: "0 1px 1px 0 rgba(15, 23, 42, 0.04)",
        sm: "0 1px 2px 0 rgba(15, 23, 42, 0.06)",
        DEFAULT: "0 1px 3px 0 rgba(15, 23, 42, 0.08)",
        md: "0 4px 8px -2px rgba(15, 23, 42, 0.12)",
      },
    },
  },
  corePlugins: {
    backdropBlur: false,
    backdropFilter: false,
    dropShadow: false,
  },
  plugins: [],
};

export default config;
