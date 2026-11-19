/**
 * Teste End-to-End Automatizado da Jornada Completa do Servidor Público da UFAM
 * (`frontend/e2e/redaction_workflow.spec.ts`).
 *
 * Critérios de Aceitação validados:
 * 1. Acessar a tela inicial e anexar o PDF sintético;
 * 2. Verificar que o visualizador Thin-Client (WebP + SVG) abre assim que a Página 1 fica pronta (`First-Page-First`) e exibe o painel de Log Verbose por Etapa (`ProcessingVerboseLog`);
 * 3. Verificar o Banner de Perfil do Documento, a Barra de Ação Rápida Global (1 Clique) e o Seletor de 3 Estados (`Tarjar`, `Mascarar`, `Manter`), incluindo a prévia `<text>` de mascaramento parcial no SVG;
 * 4. Desenhar manualmente uma tarja adicional sobre o `<svg>` da página a 60 FPS;
 * 5. Confirmar o aviso legal da LGPD (Art. 5º, XI) e aplicar o tarjamento/mascaramento definitivo;
 * 6. Verificar o checklist de validação cirúrgica de segurança na tela e as etapas 5, 6 e 7 no Log Verbose;
 * 7. Baixar o PDF final tarjado e o Relatório de Processamento em PDF (Zero-PII);
 * 8. Clicar em "Voltar e Editar Modos", alternar o modo e re-gerar um novo PDF tarjado no mesmo documento.
 */

import { expect, test } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

test.describe("Jornada Institucional UFAM — Identificação e Tarjamento Definitivo (E2E)", () => {
  test("Fluxo completo de 7 etapas + alternância dinâmica: Upload -> First-Page-First -> Log Verbose -> Tarja Manual 60FPS -> Validação -> Download PDF/Relatório -> Voltar e Re-gerar", async ({
    page,
  }) => {
    const samplePdfPath = path.resolve(__dirname, "fixtures", "ufam_sample_e2e.pdf");
    expect(fs.existsSync(samplePdfPath)).toBeTruthy();

    // 1. Acessa a tela inicial e verifica o Header Institucional UFAM e o Stepper de 7 etapas
    await page.goto("/");
    await expect(
      page.getByRole("heading", {
        name: /Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM/i,
      })
    ).toBeVisible();
    await expect(page.getByText("1. Anexar")).toBeVisible();
    await expect(page.getByText("7. Exportar")).toBeVisible();

    // Anexa o PDF sintético da UFAM
    const fileInput = page.getByTestId("pdf-file-input");
    await fileInput.setInputFiles(samplePdfPath);

    // 2. Verifica que o visualizador abre assim que a Página 1 fica pronta
    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 1 de", {
      timeout: 15000,
    });
    const webpImage = page.getByTestId("page-webp-image");
    await expect(webpImage).toBeVisible();
    await expect(async () => {
      const box = await webpImage.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.height).toBeGreaterThan(200);
    }).toPass({ timeout: 10000 });

    const svgOverlay = page.getByTestId("page-overlay-svg");
    await expect(svgOverlay).toBeVisible();

    // Verifica que o Painel de Log Verbose por Etapa está visível com as etapas iniciais
    const verbosePanel = page.getByTestId("verbose-processing-panel");
    await expect(verbosePanel).toBeVisible();
    await expect(verbosePanel).toContainText("1. Ingestão Segura");
    await expect(verbosePanel).toContainText("2. Extração Posicional");
    await expect(verbosePanel).toContainText("3. Detecção PII");

    // 3. Verifica o Banner de Perfil Semântico e os 3 Botões Rápidos Globais
    const profileBanner = page.getByTestId("document-profile-banner");
    await expect(profileBanner).toBeVisible();
    await expect(profileBanner).toContainText("Perfil Detectado:");

    // Testa o botão global "Mascarar CPFs (***.XXX.XXX-**)" e verifica a prévia `<text>` no SVG
    const maskCpfsBtn = page.getByTestId("quick-action-mask-cpfs-btn");
    await expect(maskCpfsBtn).toBeVisible();
    await maskCpfsBtn.click();

    const svgMaskTexts = page.locator('[data-testid^="svg-occurrence-mask-text-"]');
    await expect(svgMaskTexts.first()).toBeVisible();

    // Testa o botão "Tarjar Tudo (Preto)" e depois "Usar Recomendação IA"
    await page.getByTestId("quick-action-blackout-all-btn").click();
    await page.getByTestId("quick-action-ai-rec-btn").click();

    // Revisa ocorrências e alterna o seletor individual de 3 estados (`Tarjar` / `Mascarar` / `Manter`)
    const occurrencesList = page.getByTestId("occurrences-list");
    await expect(occurrencesList).toBeVisible();

    const firstModeMaskBtn = page.locator('[data-testid^="mode-mask-"]').first();
    await expect(firstModeMaskBtn).toBeVisible();
    await firstModeMaskBtn.click();

    // Alterna para visualização de Todo o Documento para inspecionar repetições de `group_id`
    await page.getByTestId("scope-all-pages-btn").click();

    const firstGroupToggleBtn = page.locator('[data-testid^="group-toggle-btn-"]').first();
    if ((await firstGroupToggleBtn.count()) > 0) {
      await firstGroupToggleBtn.click();
      // Re-marca o grupo novamente para garantir que seja protegido
      await firstGroupToggleBtn.click();
    }

    // 4. Desenha manualmente uma tarja adicional sobre o `<svg>` da página
    const toggleManualDrawBtn = page.getByTestId("toggle-manual-draw-btn");
    await toggleManualDrawBtn.click();
    await expect(toggleManualDrawBtn).toContainText("ATIVO");

    await svgOverlay.scrollIntoViewIfNeeded();
    const svgBox = await svgOverlay.boundingBox();
    expect(svgBox).not.toBeNull();
    expect(svgBox!.height).toBeGreaterThan(200);

    if (svgBox) {
      const startX = svgBox.x + svgBox.width * 0.15;
      const startY = svgBox.y + svgBox.height * 0.35;
      const endX = svgBox.x + svgBox.width * 0.65;
      const endY = svgBox.y + svgBox.height * 0.45;

      await page.mouse.move(startX, startY);
      await page.mouse.down();
      await page.mouse.move(endX, endY, { steps: 5 });
      await page.mouse.up();
    }

    // Verifica que a nova tarja manual `<rect>` foi renderizada no SVG
    const manualRects = page.locator('[data-testid^="svg-manual-rect-"]');
    await expect(manualRects.first()).toBeVisible();

    // 5. Abre o modal de confirmação, aceita o aviso legal da LGPD (Art. 5º, XI) e aplica o tarjamento definitivo
    const openModalBtn = page.getByTestId("open-confirm-redaction-modal-btn");
    await openModalBtn.scrollIntoViewIfNeeded();
    await openModalBtn.click();
    const modal = page.getByTestId("export-and-report-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Art. 5º, XI");

    const confirmBtn = page.getByTestId("confirm-apply-redaction-btn");
    await confirmBtn.click();

    // 6. Verifica o checklist de validação cirúrgica de segurança na tela
    const validationPanel = page.getByTestId("validation-and-export-panel");
    await expect(validationPanel).toBeVisible({ timeout: 20000 });
    await expect(validationPanel).toContainText("PDF_REDACT_TEXT_REMOVE");
    await expect(validationPanel).toContainText("PDF_REDACT_IMAGE_PIXELS");
    await expect(validationPanel).toContainText("pikepdf");

    // 7. Baixa o PDF final tarjado e o Relatório de Processamento em PDF (Zero-PII)
    const downloadPdfPromise = page.waitForEvent("download");
    await page.getByTestId("download-redacted-pdf-link").click();
    const downloadedPdf = await downloadPdfPromise;
    expect(downloadedPdf.suggestedFilename()).toContain(".pdf");

    const downloadReportPromise = page.waitForEvent("download");
    await page.getByTestId("download-report-pdf-link").click();
    const downloadedReport = await downloadReportPromise;
    expect(downloadedReport.suggestedFilename()).toContain(".pdf");

    // 8. Clica em "Voltar e Editar Modos", alterna para Tarjar Tudo e re-aplica gerando nova revisão
    await page.getByTestId("back-to-edit-modes-btn").click();
    await expect(modal).toBeHidden();
    await page.getByTestId("quick-action-blackout-all-btn").click();

    await openModalBtn.scrollIntoViewIfNeeded();
    await openModalBtn.click();
    await expect(confirmBtn).toBeVisible();
    await confirmBtn.click();
    await expect(validationPanel).toBeVisible({ timeout: 20000 });

    const redownloadHref = await page.getByTestId("download-redacted-pdf-link").getAttribute("href");
    expect(redownloadHref).toContain("rev=");

    // Fecha o modal e verifica que as etapas 5, 6 e 7 aparecem no Log Verbose
    await page.getByTestId("close-export-modal-btn").click();
    await expect(verbosePanel).toContainText("5. True Redaction");
    await expect(verbosePanel).toContainText("6. Validação Cirúrgica");
    await expect(verbosePanel).toContainText("7. Relatório Zero-PII");
  });
});
