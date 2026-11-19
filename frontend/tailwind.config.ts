import type { Config } from "tailwindcss";

/**
 * Configuração Tailwind CSS otimizada para Hardware Antigo (GPU Integrada Legada 10-15 anos).
 * - Utiliza cores sólidas institucionais UFAM de alto contraste.
 * - Desabilita utilitários pesados de composição em GPU (`backdropBlur`, `dropShadow`).
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
        ufam: {
          green: "#0F4C3A",
          greenDark: "#0A3528",
          greenLight: "#E6F2EE",
          gold: "#8A6D1B",
          goldLight: "#FBF7E8",
          slate: "#1E293B",
          border: "#CBD5E1",
        },
        category: {
          commonBg: "#EFF6FF",
          commonBorder: "#1D4ED8",
          commonText: "#1E3A8A",
          sensitiveBg: "#FEF2F2",
          sensitiveBorder: "#B91C1C",
          sensitiveText: "#7F1D1D",
          financialBg: "#FFFBEB",
          financialBorder: "#B45309",
          financialText: "#78350F",
          minorBg: "#F5F3FF",
          minorBorder: "#6D28D9",
          minorText: "#4C1D95",
        },
      },
      boxShadow: {
        none: "none",
        sm: "0 1px 2px 0 rgba(15, 23, 42, 0.08)",
        DEFAULT: "0 1px 3px 0 rgba(15, 23, 42, 0.12)",
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
