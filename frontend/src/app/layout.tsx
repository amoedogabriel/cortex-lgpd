import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM",
  description:
    "Plataforma institucional da Universidade Federal do Amazonas (UFAM) para identificação assistida e tarjamento definitivo (true redaction) de dados pessoais e sensíveis em documentos PDF conforme LGPD (Lei nº 13.709/2018) e LAI (Lei nº 12.527/2011).",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <body className="min-h-screen bg-surface-page text-ink-primary antialiased">
        {children}
      </body>
    </html>
  );
}
