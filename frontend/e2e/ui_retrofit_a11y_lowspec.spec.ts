/**
 * Suíte Determinística de Qualidade Visual, Acessibilidade (WCAG 2.2 AA),
 * Conformidade Low-Spec (GPU Legada) e Contrato `verbose-processing-panel`
 * (`frontend/e2e/ui_retrofit_a11y_lowspec.spec.ts`).
 */

import { expect, test, type Page } from "@playwright/test";

const MOCK_PAGE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1130" viewBox="0 0 800 1130">
  <rect width="800" height="1130" fill="#ffffff" stroke="#e2e8f0" stroke-width="2" />
  <rect x="60" y="80" width="680" height="24" fill="#f1f5f9" />
  <rect x="60" y="140" width="420" height="16" fill="#e2e8f0" />
</svg>`;

const SAMPLE_PDF_BUFFER = Buffer.from(
  "%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
);

function buildVerboseLogs(isCompleted: boolean) {
  const now = new Date().toISOString();
  const base = [
    {
      id: "v1",
      sequence: 1,
      step_number: 1,
      stage: "ETAPA_1_INGESTAO_SEGURA",
      stage_label: "1. Ingestão Segura & Validação Estrutural",
      level: "INFO",
      message: "PDF validado localmente.",
      duration_ms: 12,
      timestamp: now,
    },
    {
      id: "v2",
      sequence: 2,
      step_number: 2,
      stage: "ETAPA_2_EXTRACAO_OCR",
      stage_label: "2. Extração Posicional & WebP",
      level: "INFO",
      message: "Páginas renderizadas em 150 DPI.",
      duration_ms: 58,
      timestamp: now,
    },
    {
      id: "v3",
      sequence: 3,
      step_number: 3,
      stage: "ETAPA_3_DETECCAO_LGPD",
      stage_label: "3. Detecção PII (Regex + Dígito + spaCy NLP)",
      level: "INFO",
      message: "Ocorrências classificadas.",
      duration_ms: 91,
      timestamp: now,
    },
  ];
  if (!isCompleted) return base;
  return [
    ...base,
    {
      id: "v5",
      sequence: 5,
      step_number: 5,
      stage: "ETAPA_5_REDACAO_FISICA",
      stage_label: "5. True Redaction (PyMuPDF) & Sanitização (pikepdf)",
      level: "INFO",
      message: "Tarjamento físico aplicado.",
      duration_ms: 132,
      timestamp: now,
    },
    {
      id: "v6",
      sequence: 6,
      step_number: 6,
      stage: "ETAPA_6_VALIDACAO_CIRURGICA",
      stage_label: "6. Validação Cirúrgica Pós-Tarjamento",
      level: "INFO",
      message: "Zero texto residual detectado.",
      duration_ms: 41,
      timestamp: now,
    },
    {
      id: "v7",
      sequence: 7,
      step_number: 7,
      stage: "ETAPA_7_EXPORTACAO_AUDITORIA",
      stage_label: "7. Relatório Zero-PII & Expurgo de Sessão",
      level: "INFO",
      message: "Relatório gerado.",
      duration_ms: 28,
      timestamp: now,
    },
  ];
}

async function setupDeterministicMock(page: Page) {
  let completed = false;
  const occs = [
    {
      id: "occ_1",
      page_number: 1,
      entity_type: "CPF",
      category: "PERSONAL_COMMON",
      x0_norm: 0.12,
      y0_norm: 0.2,
      x1_norm: 0.34,
      y1_norm: 0.23,
      confidence_score: 0.99,
      confidence_level: "HIGH",
      group_id: "grp_cpf_1",
      detector_source: "REGEX_VALIDATED",
      selected: true,
      action_mode: "BLACKOUT",
      recommended_mode: "BLACKOUT",
      masked_preview: "***.729.160-**",
      preview_text: "014.729.160-88",
    },
    {
      id: "occ_2",
      page_number: 1,
      entity_type: "CID",
      category: "PERSONAL_SENSITIVE",
      x0_norm: 0.15,
      y0_norm: 0.35,
      x1_norm: 0.3,
      y1_norm: 0.38,
      confidence_score: 0.95,
      confidence_level: "HIGH",
      group_id: "grp_cid_1",
      detector_source: "NLP_SPACY",
      selected: true,
      action_mode: "BLACKOUT",
      recommended_mode: "BLACKOUT",
      masked_preview: "CID ***",
      preview_text: "CID F32.1",
    },
  ];

  await page.route("**/api/v1/documents/upload*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job_a11y_1",
        status: "AWAITING_REVIEW",
        document_profile: "RESTRICTED_PROCESS",
        original_file_sha256: "sha256_orig_job_a11y_1_abcdef0123456789",
        file_size_bytes: 16384,
        total_pages: 1,
        processed_pages: 1,
        verbose_logs: buildVerboseLogs(false),
        stream_url: "/api/v1/documents/job_a11y_1/stream",
        occurrences_url: "/api/v1/documents/job_a11y_1/occurrences",
        first_page_image_url: "/api/v1/documents/job_a11y_1/pages/1/image",
      }),
    });
  });

  await page.route("**/api/v1/documents/*/occurrences", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job_a11y_1",
        status: completed ? "COMPLETED" : "AWAITING_REVIEW",
        document_profile: "RESTRICTED_PROCESS",
        original_file_sha256: "sha256_orig_job_a11y_1_abcdef0123456789",
        redacted_file_sha256: completed
          ? "sha256_red_job_a11y_1_9876543210fedcba"
          : null,
        total_pages: 1,
        processed_pages: 1,
        pages: {
          "1": {
            page_number: 1,
            page_type: "TEXTUAL",
            width_px: 800,
            height_px: 1130,
            image_url: "/api/v1/documents/job_a11y_1/pages/1/image",
            ocr_executed: false,
            processing_time_ms: 35,
            occurrences: occs,
          },
        },
        occurrences: occs,
        verbose_logs: buildVerboseLogs(completed),
        audit_log: completed
          ? {
              job_id: "job_a11y_1",
              original_document_sha256: "sha256_orig_job_a11y_1_abcdef0123456789",
              redacted_document_sha256: "sha256_red_job_a11y_1_9876543210fedcba",
              count_personal_common: 1,
              count_personal_sensitive: 1,
              count_financial_banking: 0,
              count_minor_or_dependent: 0,
              total_redactions_applied: 2,
              category_counts: {
                PERSONAL_COMMON: 1,
                PERSONAL_SENSITIVE: 1,
                FINANCIAL_BANKING: 0,
                MINOR_OR_DEPENDENT: 0,
              },
              affected_pages: [1],
              total_pages: 1,
              security_validation_passed: true,
              created_at: new Date().toISOString(),
            }
          : null,
      }),
    });
  });

  await page.route("**/api/v1/documents/*/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
      body: ": keepalive\n\n",
    });
  });

  await page.route("**/api/v1/documents/*/pages/*/image", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: MOCK_PAGE_SVG,
    });
  });

  await page.route("**/api/v1/documents/*/redact", async (route) => {
    completed = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job_a11y_1",
        status: "COMPLETED",
        document_profile: "RESTRICTED_PROCESS",
        original_file_sha256: "sha256_orig_job_a11y_1_abcdef0123456789",
        redacted_file_sha256: "sha256_red_job_a11y_1_9876543210fedcba",
        verbose_logs: buildVerboseLogs(true),
        audit_log: {
          job_id: "job_a11y_1",
          original_document_sha256: "sha256_orig_job_a11y_1_abcdef0123456789",
          redacted_document_sha256: "sha256_red_job_a11y_1_9876543210fedcba",
          count_personal_common: 1,
          count_personal_sensitive: 1,
          count_financial_banking: 0,
          count_minor_or_dependent: 0,
          total_redactions_applied: 2,
          category_counts: {
            PERSONAL_COMMON: 1,
            PERSONAL_SENSITIVE: 1,
            FINANCIAL_BANKING: 0,
            MINOR_OR_DEPENDENT: 0,
          },
          affected_pages: [1],
          total_pages: 1,
          security_validation_passed: true,
          created_at: new Date().toISOString(),
        },
        download_pdf_url: "/api/v1/documents/job_a11y_1/download/pdf",
        download_report_url: "/api/v1/documents/job_a11y_1/download/report",
      }),
    });
  });
}

async function assertLowSpecGpuCompliance(page: Page) {
  const violations = await page.evaluate(() => {
    const issues: string[] = [];
    const elements = document.querySelectorAll<HTMLElement>("*");
    for (const el of elements) {
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;

      const backdrop = style.backdropFilter || "";
      if (backdrop && backdrop !== "none") {
        issues.push(`backdrop-filter=${backdrop} on ${el.tagName}`);
      }

      const filter = style.filter || "";
      if (filter.includes("drop-shadow") || filter.includes("blur(")) {
        issues.push(`filter=${filter} on ${el.tagName}`);
      }

      const shadow = style.boxShadow || "";
      if (shadow && shadow !== "none") {
        // Verifica blur radius (3º valor px em `x y blur ...`)
        const matches = shadow.match(
          /(-?\d+(?:\.\d+)?)px\s+(-?\d+(?:\.\d+)?)px\s+(\d+(?:\.\d+)?)px/g
        );
        if (matches) {
          for (const m of matches) {
            const parts = m.split(/\s+/);
            const blurVal = parseFloat(parts[2]);
            if (blurVal > 12) {
              issues.push(`heavy box-shadow blur=${blurVal}px on ${el.tagName}`);
            }
          }
        }
      }
    }
    return issues;
  });

  expect(violations).toEqual([]);
}

test.describe("Retrofit Visual SaaS — Acessibilidade WCAG 2.2 AA, Low-Spec GPU e Auditoria Verbose", () => {
  test("1. Conformidade Low-Spec (zero backdrop-blur/drop-shadow/sombras pesadas), verbose-processing-panel e zero crases literais", async ({
    page,
  }) => {
    await setupDeterministicMock(page);
    await page.goto("/");

    // Upload view low-spec check
    await assertLowSpecGpuCompliance(page);

    // Envia PDF e entra no Workspace
    await page.getByTestId("pdf-file-input").setInputFiles({
      name: "processo_ufam.pdf",
      mimeType: "application/pdf",
      buffer: SAMPLE_PDF_BUFFER,
    });
    await expect(page.getByTestId("occurrences-list")).toBeVisible({
      timeout: 10000,
    });

    // Workspace low-spec check
    await assertLowSpecGpuCompliance(page);

    // Contrato verbose-processing-panel (visível sem precisar expandir)
    const verbosePanel = page.getByTestId("verbose-processing-panel");
    await expect(verbosePanel).toBeVisible();
    await expect(verbosePanel).toContainText("1. Ingestão Segura");
    await expect(verbosePanel).toContainText("2. Extração Posicional");
    await expect(verbosePanel).toContainText("3. Detecção PII");

    // Progressive disclosure de detalhes técnicos funciona
    const detailsBtn = verbosePanel.getByRole("button", {
      name: /Ver detalhes/i,
    });
    await detailsBtn.click();
    await expect(verbosePanel).toContainText("PDF validado localmente.");
    await verbosePanel.getByRole("button", { name: /Ocultar detalhes/i }).click();

    // Zero crases literais de Markdown na barra lateral
    const sidebarText = await page
      .getByLabel("Painel de Ocorrências e Produtividade")
      .innerText();
    expect(sidebarText).not.toContain("`");

    // Modal 1: low-spec check + zero crases literais
    await page.getByTestId("open-confirm-redaction-modal-btn").click();
    const modal = page.getByTestId("export-and-report-modal");
    await expect(modal).toBeVisible();
    await assertLowSpecGpuCompliance(page);
    expect(await modal.innerText()).not.toContain("`");

    // Modal 2: low-spec check + zero crases literais + etapas 5, 6 e 7 no verbose panel
    await page.getByTestId("confirm-apply-redaction-btn").click();
    await expect(page.getByTestId("validation-and-export-panel")).toBeVisible({
      timeout: 10000,
    });
    await assertLowSpecGpuCompliance(page);
    const modal2Text = await modal.innerText();
    expect(modal2Text).not.toContain("`");
    expect(modal2Text).toContain("PDF_REDACT_TEXT_REMOVE");
    expect(modal2Text).toContain("PDF_REDACT_IMAGE_PIXELS");
    expect(modal2Text).toContain("pikepdf");

    await page.getByTestId("close-export-modal-btn").click();
    await expect(verbosePanel).toContainText("5. True Redaction");
    await expect(verbosePanel).toContainText("6. Validação Cirúrgica");
    await expect(verbosePanel).toContainText("7. Relatório Zero-PII");
  });

  test("2. Acessibilidade WCAG 2.2 AA: skip-link, teclado no SVG e Escape + Focus Trap + restauração de foco no Modal", async ({
    page,
  }) => {
    await setupDeterministicMock(page);
    await page.goto("/");

    // Skip-link existe e aponta para #main-content
    const skipLink = page.getByRole("link", {
      name: /Pular para o conteúdo principal/i,
    });
    await expect(skipLink).toHaveAttribute("href", "#main-content");
    await expect(page.locator("#main-content")).toBeVisible();

    await page.getByTestId("pdf-file-input").setInputFiles({
      name: "acessibilidade.pdf",
      mimeType: "application/pdf",
      buffer: SAMPLE_PDF_BUFFER,
    });
    await expect(page.getByTestId("occurrences-list")).toBeVisible({
      timeout: 10000,
    });

    // Ocorrência SVG focável e operável via teclado (Enter alterna 2 -> 1 tarja)
    const applyBtn = page.getByTestId("open-confirm-redaction-modal-btn");
    await expect(applyBtn).toContainText("(2 tarjas)");
    const occ1Rect = page.getByTestId("svg-occurrence-rect-occ_1");
    await expect(occ1Rect).toHaveAttribute("role", "button");
    await expect(occ1Rect).toHaveAttribute("tabindex", "0");
    await occ1Rect.focus();
    await page.keyboard.press("Enter");
    await expect(applyBtn).toContainText("(1 tarjas)");
    await page.keyboard.press(" ");
    await expect(applyBtn).toContainText("(2 tarjas)");

    // Modal: foco preso no dialog, body scroll bloqueado, Escape fecha e restaura foco
    await applyBtn.focus();
    await applyBtn.click();
    const modal = page.getByTestId("export-and-report-modal");
    await expect(modal).toBeVisible();

    const bodyOverflowWhileOpen = await page.evaluate(
      () => document.body.style.overflow
    );
    expect(bodyOverflowWhileOpen).toBe("hidden");

    // Focus trap: Tab cicla apenas dentro do modal
    for (let i = 0; i < 8; i += 1) {
      await page.keyboard.press("Tab");
      const activeInsideModal = await page.evaluate(() => {
        const dialog = document.querySelector(
          '[data-testid="export-and-report-modal"]'
        );
        return Boolean(dialog && dialog.contains(document.activeElement));
      });
      expect(activeInsideModal).toBe(true);
    }

    // Escape fecha o modal e restaura foco ao botão que abriu o dialog
    await page.keyboard.press("Escape");
    await expect(modal).toBeHidden();

    const restoredTestId = await page.evaluate(() =>
      document.activeElement?.getAttribute("data-testid")
    );
    expect(restoredTestId).toBe("open-confirm-redaction-modal-btn");
  });

  test("3. Responsividade sem overflow horizontal nem sobreposição no header (Desktop 1440, Notebook 1280 e Mobile 390)", async ({
    page,
  }) => {
    const viewports = [
      { width: 1440, height: 900 },
      { width: 1280, height: 720 },
      { width: 390, height: 844 },
    ];

    await setupDeterministicMock(page);

    for (const vp of viewports) {
      await page.setViewportSize(vp);
      await page.goto("/");

      const uploadOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(uploadOverflow).toBeLessThanOrEqual(1);

      // Verifica que título h1 e badge Zero-PII não se sobrepõem (especialmente em 390px)
      const h1Box = await page.getByRole("heading", { level: 1 }).boundingBox();
      const badgeBox = await page
        .getByText("Processamento Local Seguro • Zero-PII")
        .boundingBox();
      expect(h1Box).not.toBeNull();
      expect(badgeBox).not.toBeNull();
      if (h1Box && badgeBox) {
        const overlapsHorizontally =
          h1Box.x < badgeBox.x + badgeBox.width &&
          h1Box.x + h1Box.width > badgeBox.x;
        const overlapsVertically =
          h1Box.y < badgeBox.y + badgeBox.height &&
          h1Box.y + h1Box.height > badgeBox.y;
        expect(overlapsHorizontally && overlapsVertically).toBe(false);
      }

      // Workspace overflow check
      await page.getByTestId("pdf-file-input").setInputFiles({
        name: `resp_${vp.width}.pdf`,
        mimeType: "application/pdf",
        buffer: SAMPLE_PDF_BUFFER,
      });
      await expect(page.getByTestId("occurrences-list")).toBeVisible({
        timeout: 10000,
      });
      const wsOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(wsOverflow).toBeLessThanOrEqual(1);
    }
  });

  test("4. Recuperação automática de queda SSE em 22/25 + UI de progresso moderna sem redundância", async ({
    page,
  }) => {
    const jobId = "job_sse_drop_25";
    let occurrencesPollCount = 0;
    let streamConnections = 0;

    const buildPagesMap = (count: number) => {
      const map: Record<string, Record<string, unknown>> = {};
      for (let p = 1; p <= count; p += 1) {
        map[String(p)] = {
          page_number: p,
          page_type: "SCANNED",
          width_px: 1240,
          height_px: 1754,
          image_url: `/api/v1/documents/${jobId}/pages/${p}/image.webp`,
          ocr_executed: true,
          processing_time_ms: 120,
          occurrences: [],
        };
      }
      return map;
    };

    const baseOccurrences = [
      {
        id: "occ_drop_1",
        page_number: 1,
        entity_type: "CPF",
        category: "PERSONAL_COMMON",
        x0_norm: 0.12,
        y0_norm: 0.14,
        x1_norm: 0.36,
        y1_norm: 0.18,
        confidence_score: 0.99,
        confidence_level: "HIGH",
        group_id: "grp_drop_cpf",
        detector_source: "REGEX",
        selected: true,
        action_mode: "BLACKOUT",
        recommended_mode: "BLACKOUT",
        masked_preview: "***.222.333-**",
        preview_text: "111.222.333-44",
      },
      {
        id: "occ_drop_2",
        page_number: 2,
        entity_type: "EMAIL",
        category: "PERSONAL_COMMON",
        x0_norm: 0.12,
        y0_norm: 0.24,
        x1_norm: 0.45,
        y1_norm: 0.28,
        confidence_score: 0.96,
        confidence_level: "HIGH",
        group_id: "grp_drop_email",
        detector_source: "REGEX",
        selected: true,
        action_mode: "BLACKOUT",
        recommended_mode: "BLACKOUT",
        masked_preview: "t***@ufam.edu.br",
        preview_text: "titular@ufam.edu.br",
      },
    ];

    const finalOccurrence = {
      id: "occ_drop_3",
      page_number: 25,
      entity_type: "RG",
      category: "PERSONAL_COMMON",
      x0_norm: 0.14,
      y0_norm: 0.30,
      x1_norm: 0.38,
      y1_norm: 0.34,
      confidence_score: 0.94,
      confidence_level: "HIGH",
      group_id: "grp_drop_rg",
      detector_source: "REGEX",
      selected: true,
      action_mode: "BLACKOUT",
      recommended_mode: "BLACKOUT",
      masked_preview: "***.456-*",
      preview_text: "1234567-0",
    };

    await page.route("**/api/v1/documents/upload**", async (route) => {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: jobId,
          status: "PARTIAL_REVIEW_READY",
          document_profile: "RESTRICTED_PROCESS",
          original_file_sha256: "f".repeat(64),
          total_pages: 25,
          processed_pages: 1,
          first_page_ready: true,
          stream_url: `/api/v1/documents/${jobId}/stream`,
          occurrences_url: `/api/v1/documents/${jobId}/occurrences`,
          verbose_logs: [
            {
              id: "drop_step_1",
              sequence: 1,
              step_number: 1,
              stage: "ETAPA_1_INGESTAO",
              stage_label: "1. Ingestão Segura",
              level: "SUCCESS",
              message: "PDF recebido com 25 páginas.",
              timestamp: new Date().toISOString(),
            },
          ],
        }),
      });
    });

    await page.route(`**/api/v1/documents/${jobId}/pages/*/image.webp`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: MOCK_PAGE_SVG,
      });
    });

    await page.route(`**/api/v1/documents/${jobId}/occurrences`, async (route) => {
      occurrencesPollCount += 1;
      // Primeiras 2 chamadas mantêm 22/25 para validar a UI de progresso em andamento;
      // a partir da 3ª chamada (reconexão / polling de segurança), o worker concluiu 25/25.
      const isCompletedNow = occurrencesPollCount >= 3;
      const processed = isCompletedNow ? 25 : 22;
      const occs = isCompletedNow
        ? [...baseOccurrences, finalOccurrence]
        : baseOccurrences;

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          job_id: jobId,
          status: isCompletedNow ? "AWAITING_REVIEW" : "PARTIAL_REVIEW_READY",
          document_profile: "RESTRICTED_PROCESS",
          original_file_sha256: "f".repeat(64),
          total_pages: 25,
          processed_pages: processed,
          pages: buildPagesMap(processed),
          occurrences: occs,
          verbose_logs: [
            {
              id: "drop_step_1",
              sequence: 1,
              step_number: 1,
              stage: "ETAPA_1_INGESTAO",
              stage_label: "1. Ingestão Segura",
              level: "SUCCESS",
              message: "PDF recebido com 25 páginas.",
              timestamp: new Date().toISOString(),
            },
            {
              id: "drop_step_3",
              sequence: 2,
              step_number: 3,
              stage: "ETAPA_3_DETECCAO_LGPD",
              stage_label: "3. Detecção PII",
              level: "INFO",
              message: isCompletedNow
                ? "Análise concluída em todas as 25 páginas."
                : "Processando OCR/IA da página 23.",
              details: { page_number: isCompletedNow ? 25 : 23 },
              timestamp: new Date().toISOString(),
            },
          ],
        }),
      });
    });

    await page.route(`**/api/v1/documents/${jobId}/stream`, async (route) => {
      streamConnections += 1;
      if (streamConnections === 1) {
        // Simula queda prematura do SSE exatamente em 22/25 (sem analysis_complete)
        const partialSse =
          `event: progress\n` +
          `data: ${JSON.stringify({
            event: "progress",
            job_id: jobId,
            timestamp: new Date().toISOString(),
            data: {
              processed_pages: 22,
              total_pages: 25,
              percentage: 88,
              status: "PARTIAL_REVIEW_READY",
            },
          })}\n\n`;
        await route.fulfill({
          status: 200,
          headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
          },
          body: partialSse,
        });
        return;
      }

      // Na reconexão SSE, backend envia analysis_complete 25/25
      const completeSse =
        `event: analysis_complete\n` +
        `data: ${JSON.stringify({
          event: "analysis_complete",
          job_id: jobId,
          timestamp: new Date().toISOString(),
          data: {
            status: "AWAITING_REVIEW",
            document_profile: "RESTRICTED_PROCESS",
            total_pages: 25,
            total_occurrences: 3,
          },
        })}\n\n`;
      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
        },
        body: completeSse,
      });
    });

    await page.goto("/");
    await page.getByTestId("pdf-file-input").setInputFiles({
      name: "processo_25_paginas.pdf",
      mimeType: "application/pdf",
      buffer: SAMPLE_PDF_BUFFER,
    });

    // 1. Verifica estado de progresso em 22/25 (antes da recuperação)
    const progressBanner = page.getByTestId("sidebar-sse-progress-banner");
    await expect(progressBanner).toBeVisible({ timeout: 10000 });
    await expect(progressBanner).toContainText("ANALISANDO DOCUMENTO");
    await expect(page.getByTestId("processing-progress-percent")).toHaveText("88%");
    await expect(page.getByTestId("processing-pages-summary")).toContainText(
      "22 de 25 páginas processadas"
    );
    await expect(page.getByTestId("processing-current-page")).toContainText(
      "Processando página 23..."
    );

    // CTA desabilitado de forma limpa (sem repetir contador redundante 22/25)
    const ctaBtn = page.getByTestId("open-confirm-redaction-modal-btn");
    await expect(ctaBtn).toBeDisabled();
    await expect(ctaBtn).toContainText("Aguardando conclusão da análise...");

    // Regra anti-redundância: card ativo compacto não duplica texto "Etapa 4/6"
    const activeCard = page.getByRole("article", {
      name: /Progresso do documento processo_25_paginas\.pdf/i,
    });
    await expect(activeCard).not.toContainText("Etapa 4/6");
    await expect(activeCard.getByRole("progressbar")).toBeVisible();

    // 2. Aguarda recuperação automática via reconexão SSE + snapshot polling (sem refresh manual!)
    await expect(progressBanner).toContainText("ANÁLISE CONCLUÍDA", {
      timeout: 10000,
    });
    await expect(progressBanner).toContainText("AWAITING_REVIEW");
    await expect(page.getByTestId("processing-progress-percent")).toHaveText("100%");
    await expect(page.getByTestId("processing-pages-summary")).toContainText(
      "25 de 25 páginas prontas para conferência final."
    );

    // CTA liberado automaticamente com o total consolidado de 3 tarjas
    await expect(ctaBtn).toBeEnabled();
    await expect(ctaBtn).toContainText("Aplicar Tarjamento Definitivo (3 tarjas)");

    // Conformidade Low-Spec GPU preservada
    await assertLowSpecGpuCompliance(page);
  });
});
