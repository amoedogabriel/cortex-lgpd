/**
 * Testes de Caracterização Visual e Comportamental (Etapa 2)
 * (`frontend/e2e/ui_characterization.spec.ts`).
 *
 * Objetivo: Garantir que toda a lógica de negócios, estado, atalhos de teclado,
 * contratos DOM/ARIA, coordenadas SVG e textos legais permaneçam 100% idênticos
 * antes e depois da refatoração exclusivamente visual e de acessibilidade.
 *
 * Utiliza mocks de rede via `page.route` para rodar de forma determinística
 * com `@playwright/test` contra o servidor Next.js, sem alterar o comportamento real.
 */

import { expect, test, type Page } from "@playwright/test";

const MOCK_PAGE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1130" viewBox="0 0 800 1130">
  <rect width="800" height="1130" fill="#ffffff" stroke="#e2e8f0" stroke-width="2" />
  <rect x="60" y="80" width="680" height="24" fill="#f1f5f9" />
  <rect x="60" y="140" width="420" height="16" fill="#e2e8f0" />
</svg>`;

const SAMPLE_PDF_BUFFER = Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n");

function buildMockOccurrences(jobId: string) {
  return [
    {
      id: "occ_1",
      page_number: 1,
      entity_type: "CPF",
      category: "PERSONAL_COMMON",
      x0_norm: 0.12,
      y0_norm: 0.2,
      x1_norm: 0.32,
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
      x1_norm: 0.28,
      y1_norm: 0.38,
      confidence_score: 0.94,
      confidence_level: "HIGH",
      group_id: "grp_cid_1",
      detector_source: "NLP_SPACY",
      selected: true,
      action_mode: "BLACKOUT",
      recommended_mode: "BLACKOUT",
      masked_preview: "CID ***",
      preview_text: "CID F32.1",
    },
    {
      id: "occ_3",
      page_number: 2,
      entity_type: "CPF",
      category: "PERSONAL_COMMON",
      x0_norm: 0.18,
      y0_norm: 0.25,
      x1_norm: 0.38,
      y1_norm: 0.28,
      confidence_score: 0.98,
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
      id: "occ_4",
      page_number: 2,
      entity_type: "CONTA_BANCARIA",
      category: "FINANCIAL_BANKING",
      x0_norm: 0.2,
      y0_norm: 0.45,
      x1_norm: 0.44,
      y1_norm: 0.48,
      confidence_score: 0.91,
      confidence_level: "MEDIUM",
      group_id: `grp_bank_${jobId}`,
      detector_source: "REGEX_VALIDATED",
      selected: true,
      action_mode: "BLACKOUT",
      recommended_mode: "BLACKOUT",
      masked_preview: "CC ****-9",
      preview_text: "12345-9",
    },
  ];
}

function buildAuditLog(jobId: string, totalRedactionsApplied: number) {
  return {
    job_id: jobId,
    original_document_sha256: `sha256_orig_${jobId}_abcdef0123456789`,
    redacted_document_sha256: `sha256_red_${jobId}_9876543210fedcba`,
    count_personal_common: 2,
    count_personal_sensitive: 1,
    count_financial_banking: 1,
    count_minor_or_dependent: 0,
    total_redactions_applied: totalRedactionsApplied,
    category_counts: {
      PERSONAL_COMMON: 2,
      PERSONAL_SENSITIVE: 1,
      FINANCIAL_BANKING: 1,
      MINOR_OR_DEPENDENT: 0,
    },
    affected_pages: [1, 2],
    total_pages: 2,
    security_validation_passed: true,
    created_at: new Date().toISOString(),
  };
}

async function setupMockBackend(page: Page, options?: { captureRedactBodies?: unknown[] }) {
  let uploadCounter = 0;
  const completedJobs = new Map<string, number>();

  await page.route("**/api/v1/documents/upload*", async (route) => {
    uploadCounter += 1;
    const jobId = `job_mock_${uploadCounter}`;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: jobId,
        status: "AWAITING_REVIEW",
        document_profile: uploadCounter === 2 ? "PUBLIC_TRANSPARENCY" : "RESTRICTED_PROCESS",
        original_file_sha256: `sha256_orig_${jobId}_abcdef0123456789`,
        file_size_bytes: 20480,
        total_pages: 2,
        processed_pages: 2,
        verbose_logs: [
          {
            id: "v1",
            sequence: 1,
            step_number: 4,
            stage: "ETAPA_4_AGUARDANDO_REVISAO",
            stage_label: "Aguardando revisão",
            level: "INFO",
            message: "Ok",
            timestamp: new Date().toISOString(),
          },
        ],
        stream_url: `/api/v1/documents/${jobId}/stream`,
        occurrences_url: `/api/v1/documents/${jobId}/occurrences`,
        first_page_image_url: `/api/v1/documents/${jobId}/pages/1/image`,
      }),
    });
  });

  await page.route("**/api/v1/documents/*/occurrences", async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/");
    const jobId = parts[parts.length - 2] || "job_mock_1";
    const occs = buildMockOccurrences(jobId);
    const completedCount = completedJobs.get(jobId);
    const isCompleted = completedCount !== undefined;

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: jobId,
        status: isCompleted ? "COMPLETED" : "AWAITING_REVIEW",
        document_profile: jobId.endsWith("_2") ? "PUBLIC_TRANSPARENCY" : "RESTRICTED_PROCESS",
        original_file_sha256: `sha256_orig_${jobId}_abcdef0123456789`,
        redacted_file_sha256: isCompleted ? `sha256_red_${jobId}_9876543210fedcba` : null,
        total_pages: 2,
        processed_pages: 2,
        pages: {
          "1": {
            page_number: 1,
            page_type: "TEXTUAL",
            width_px: 800,
            height_px: 1130,
            image_url: `/api/v1/documents/${jobId}/pages/1/image`,
            ocr_executed: false,
            processing_time_ms: 42,
            occurrences: occs.filter((o) => o.page_number === 1),
          },
          "2": {
            page_number: 2,
            page_type: "TEXTUAL",
            width_px: 800,
            height_px: 1130,
            image_url: `/api/v1/documents/${jobId}/pages/2/image`,
            ocr_executed: false,
            processing_time_ms: 38,
            occurrences: occs.filter((o) => o.page_number === 2),
          },
        },
        occurrences: occs,
        verbose_logs: [
          {
            id: "v1",
            sequence: 1,
            step_number: isCompleted ? 7 : 4,
            stage: isCompleted ? "ETAPA_7_EXPORTACAO_AUDITORIA" : "ETAPA_4_AGUARDANDO_REVISAO",
            stage_label: isCompleted ? "Concluído" : "Aguardando revisão",
            level: "INFO",
            message: "Ok",
            timestamp: new Date().toISOString(),
          },
        ],
        audit_log: isCompleted ? buildAuditLog(jobId, completedCount) : null,
      }),
    });
  });

  await page.route("**/api/v1/documents/*/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: {
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
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
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/");
    const jobId = parts[parts.length - 2] || "job_mock_1";
    const reqJson = route.request().postDataJSON();
    if (options?.captureRedactBodies) {
      options.captureRedactBodies.push(reqJson);
    }
    const items = Array.isArray(reqJson?.items) ? reqJson.items : [];
    const activeAuto = items.filter((i: { action_mode?: string }) => i.action_mode !== "KEEP").length;
    const manualCount = Array.isArray(reqJson?.manual_boxes) ? reqJson.manual_boxes.length : 0;
    const totalApplied = activeAuto + manualCount;
    completedJobs.set(jobId, totalApplied);

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: jobId,
        status: "COMPLETED",
        document_profile: "RESTRICTED_PROCESS",
        original_file_sha256: `sha256_orig_${jobId}_abcdef0123456789`,
        redacted_file_sha256: `sha256_red_${jobId}_9876543210fedcba`,
        audit_log: buildAuditLog(jobId, totalApplied),
        download_pdf_url: `/api/v1/documents/${jobId}/download/pdf`,
        download_report_url: `/api/v1/documents/${jobId}/download/report`,
      }),
    });
  });

  await page.route("**/api/v1/documents/*/download/*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/pdf",
      headers: {
        "Content-Disposition": 'attachment; filename="documento_tarjado.pdf"',
      },
      body: SAMPLE_PDF_BUFFER,
    });
  });
}

test.describe("Caracterização de UI e Contrato Funcional (Pré/Pós-Refatoração Visual)", () => {
  test("1. Renderização inicial, stepper 7 etapas (aria-current), textos normativos e validação de arquivo inválido", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && !msg.text().includes("404 (Not Found)")) {
        consoleErrors.push(msg.text());
      }
    });
    page.on("pageerror", (err) => pageErrors.push(err.message));

    await page.goto("/");

    // Cabeçalho institucional e stepper de 7 etapas
    await expect(
      page.getByRole("heading", {
        name: /Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM/i,
      })
    ).toBeVisible();
    await expect(page.getByText("Processamento Local Seguro • Zero-PII")).toBeVisible();

    const stepNav = page.getByRole("navigation", { name: /Etapas do fluxo de tarjamento/i });
    await expect(stepNav).toBeVisible();
    for (const label of [
      "1. Anexar",
      "2. Analisar",
      "3. Revisar",
      "4. Selecionar",
      "5. Aplicar",
      "6. Validar",
      "7. Exportar",
    ]) {
      await expect(stepNav).toContainText(label);
    }

    const activeStep = stepNav.locator('[aria-current="step"]');
    await expect(activeStep).toHaveCount(1);
    await expect(activeStep).toContainText("1. Anexar");

    // Textos normativos LGPD / LAI e 4 categorias
    await expect(page.getByText(/Lei nº 13\.709\/2018 \(LGPD\)/i)).toBeVisible();
    await expect(page.getByText(/Lei nº 12\.527\/2011 \(LAI\)/i)).toBeVisible();
    await expect(page.getByText(/Art\. 5º, XI, LGPD/i)).toBeVisible();
    await expect(page.getByText("1. Dado Pessoal Comum")).toBeVisible();
    await expect(page.getByText("2. Dado Pessoal Sensível")).toBeVisible();
    await expect(page.getByText("3. Financeiro e Bancário")).toBeVisible();
    await expect(page.getByText("4. Menores e Dependentes")).toBeVisible();

    // Validação local rejeita arquivo não-PDF com role="alert"
    const fileInput = page.getByTestId("pdf-file-input");
    await fileInput.setInputFiles({
      name: "notas.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("conteudo texto"),
    });

    const alertBox = page.getByRole("alert").filter({ hasText: "Arquivo rejeitado" });
    await expect(alertBox).toBeVisible();
    await expect(alertBox).toContainText('"notas.txt": formato inválido (aceita exclusivamente .pdf).');

    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
  });

  test("2. Fila multi-documento, cards individuais de progresso, alternância, hash SHA-256 e limpeza de fila", async ({
    page,
  }) => {
    await setupMockBackend(page);
    await page.goto("/");

    const fileInput = page.getByTestId("pdf-file-input");
    await fileInput.setInputFiles([
      {
        name: "processo_alpha.pdf",
        mimeType: "application/pdf",
        buffer: SAMPLE_PDF_BUFFER,
      },
      {
        name: "edital_beta.pdf",
        mimeType: "application/pdf",
        buffer: SAMPLE_PDF_BUFFER,
      },
    ]);

    await expect(page.getByText("Documentos na Sessão (2)")).toBeVisible({ timeout: 10000 });
    const alphaCard = page.getByRole("article", { name: /Progresso do documento processo_alpha\.pdf/i });
    const betaCard = page.getByRole("article", { name: /Progresso do documento edital_beta\.pdf/i });
    await expect(alphaCard).toBeVisible();
    await expect(betaCard).toBeVisible();
    await expect(alphaCard.getByRole("progressbar")).toBeVisible();
    await expect(alphaCard).toContainText("Em Revisão");
    await expect(betaCard).toContainText("Revisar");

    // Stepper ativo avança para 4. Selecionar e workspace exibe hash SHA-256
    const stepNav = page.getByRole("navigation", { name: /Etapas do fluxo de tarjamento/i });
    await expect(stepNav.locator('[aria-current="step"]')).toContainText("4. Selecionar");
    await expect(page.getByText(/SHA-256 Orig: sha256_orig_job_/i)).toBeVisible();

    // Alterna documento ativo para o segundo arquivo (edital_beta.pdf -> PUBLIC_TRANSPARENCY)
    await betaCard.getByRole("button", { name: /^Revisar$/i }).click();
    await expect(betaCard).toContainText("Em Revisão");
    await expect(page.getByTestId("document-profile-banner")).toContainText("Edital / Lista Pública");

    // Toggle "Anexar Mais PDFs" no header
    const addMoreBtn = page.getByRole("button", { name: /Anexar Mais PDFs/i });
    await addMoreBtn.click();
    await expect(page.getByTestId("pdf-file-input")).toBeAttached();
    await page.getByRole("button", { name: /Ocultar Anexação/i }).click();

    // Remover um documento e depois limpar fila
    await alphaCard.getByRole("button", { name: /Remover processo_alpha\.pdf/i }).click();
    await expect(page.getByText("Documentos na Sessão (1)")).toBeVisible();

    await page.getByRole("button", { name: /Limpar Fila/i }).click();
    await expect(page.getByText(/Documentos na Sessão/i)).toBeHidden();
    await expect(page.getByText("1. Dado Pessoal Comum")).toBeVisible();
  });

  test("3. Visualizador WebP+SVG: coordenadas, zoom, paginação, atalhos de teclado e tarja manual 60 FPS", async ({
    page,
  }) => {
    await setupMockBackend(page);
    await page.goto("/");

    await page.getByTestId("pdf-file-input").setInputFiles({
      name: "contrato_servidor.pdf",
      mimeType: "application/pdf",
      buffer: SAMPLE_PDF_BUFFER,
    });

    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 1 de 2", {
      timeout: 10000,
    });

    // Verifica preservação exata das coordenadas SVG normalizadas (* 1000)
    const occ1Rect = page.getByTestId("svg-occurrence-rect-occ_1");
    await expect(occ1Rect).toBeVisible();
    expect(Number(await occ1Rect.getAttribute("x"))).toBeCloseTo(120, 1);
    expect(Number(await occ1Rect.getAttribute("y"))).toBeCloseTo(200, 1);
    expect(Number(await occ1Rect.getAttribute("width"))).toBeCloseTo(200, 1);
    expect(Number(await occ1Rect.getAttribute("height"))).toBeCloseTo(30, 1);

    const occ2Rect = page.getByTestId("svg-occurrence-rect-occ_2");
    await expect(occ2Rect).toBeVisible();
    expect(Number(await occ2Rect.getAttribute("x"))).toBeCloseTo(150, 1);
    expect(Number(await occ2Rect.getAttribute("y"))).toBeCloseTo(350, 1);
    expect(Number(await occ2Rect.getAttribute("width"))).toBeCloseTo(130, 1);
    expect(Number(await occ2Rect.getAttribute("height"))).toBeCloseTo(30, 1);

    // Preservação da legenda e guia visual Preenchido / Tracejado
    await expect(page.getByRole("list", { name: /Legenda de categorias/i })).toBeVisible();
    await expect(page.getByText(/Preenchido:/i)).toBeVisible();
    await expect(page.getByText(/Tracejado:/i)).toBeVisible();

    // Controles de zoom (100%, 125%, 150%)
    await page.getByTestId("zoom-btn-125").click();
    await page.getByTestId("zoom-btn-150").click();
    await page.getByTestId("zoom-btn-100").click();

    // Paginação por botão e atalhos ArrowRight / ArrowLeft
    await expect(page.getByTestId("prev-page-btn")).toBeDisabled();
    await page.getByTestId("next-page-btn").click();
    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 2 de 2");
    await expect(page.getByTestId("next-page-btn")).toBeDisabled();

    await page.keyboard.press("ArrowLeft");
    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 1 de 2");
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 2 de 2");
    await page.keyboard.press("ArrowLeft");
    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 1 de 2");

    // Modo Prévia Final (#000000)
    const previewBtn = page.getByTestId("toggle-final-preview-btn");
    await previewBtn.click();
    await expect(previewBtn).toContainText("ATIVO");
    expect(await occ1Rect.getAttribute("fill")).toBe("#000000");
    await previewBtn.click();

    // Atalho M ativa e desativa desenho manual
    const toggleManualBtn = page.getByTestId("toggle-manual-draw-btn");
    await page.keyboard.press("m");
    await expect(toggleManualBtn).toContainText("ATIVO");
    const catSelect = page.getByTestId("manual-draw-category-select");
    await expect(catSelect).toBeVisible();
    await catSelect.selectOption("PERSONAL_SENSITIVE");

    // Remove foco do <select> para que atalho `M` funcione depois do desenho
    await page.getByTestId("current-page-indicator").click();

    // Desenha tarja manual via drag no SVG (com scrollIntoViewIfNeeded para viewport)
    const svgOverlay = page.getByTestId("page-overlay-svg");
    await svgOverlay.scrollIntoViewIfNeeded();
    const svgBox = await svgOverlay.boundingBox();
    expect(svgBox).not.toBeNull();
    if (svgBox) {
      await page.mouse.move(svgBox.x + svgBox.width * 0.2, svgBox.y + svgBox.height * 0.5);
      await page.mouse.down();
      await page.mouse.move(svgBox.x + svgBox.width * 0.6, svgBox.y + svgBox.height * 0.58, {
        steps: 5,
      });
      await page.mouse.up();
    }

    const manualRect = page.locator('[data-testid^="svg-manual-rect-"]').first();
    await expect(manualRect).toBeVisible();
    await manualRect.click();
    await expect(page.getByTestId("selected-manual-box-toolbar")).toBeVisible();
    await page.getByTestId("delete-manual-box-btn").click();
    await expect(manualRect).toHaveCount(0);

    // Tecla M desativa modo desenho manual
    await page.keyboard.press("M");
    await expect(toggleManualBtn).toContainText("Desenhar Tarja Manual (M)");
  });

  test("4. Painel lateral: 3 estados, group_id (ON/OFF), filtros, Ver Pág., Modal 1 (Voltar/Confirmar) e Modal 2 (Exportação)", async ({
    page,
  }) => {
    const capturedRedactBodies: unknown[] = [];
    await setupMockBackend(page, { captureRedactBodies: capturedRedactBodies });
    await page.goto("/");

    await page.getByTestId("pdf-file-input").setInputFiles({
      name: "folha_funcional.pdf",
      mimeType: "application/pdf",
      buffer: SAMPLE_PDF_BUFFER,
    });

    await expect(page.getByTestId("occurrences-list")).toBeVisible({ timeout: 10000 });
    const applyModalBtn = page.getByTestId("open-confirm-redaction-modal-btn");
    await expect(applyModalBtn).toContainText("(4 tarjas)");
    await expect(page.getByTestId("sidebar-sse-progress-banner")).toContainText("AWAITING_REVIEW");

    // Mascarar ocorrência occ_1 -> exibe preview no SVG e sincroniza group_id (occ_3 também vira PARTIAL_MASK)
    await page.getByTestId("mode-mask-occ_1").click();
    await expect(page.getByTestId("svg-occurrence-mask-text-occ_1")).toContainText("***.729.160-**");

    // Tarjar occ_1 novamente via controle individual -> oculta texto de máscara mantendo 4 tarjas
    await page.getByTestId("mode-blackout-occ_1").click();
    await expect(page.getByTestId("svg-occurrence-mask-text-occ_1")).toHaveCount(0);

    // Manter occ_1 -> com sync group_id ativo, occ_1 e occ_3 viram KEEP -> total cai de 4 para 2
    await page.getByTestId("mode-keep-occ_1").click();
    await expect(applyModalBtn).toContainText("(2 tarjas)");

    // Desmarca sync group_id -> alterar occ_1 afeta apenas occ_1 (total vai para 3, não 4)
    const groupSyncCheckbox = page.getByTestId("toggle-group-sync-checkbox");
    await expect(groupSyncCheckbox).toBeChecked();
    await groupSyncCheckbox.uncheck();
    await page.getByTestId("mode-mask-occ_1").click();
    await expect(applyModalBtn).toContainText("(3 tarjas)");
    await groupSyncCheckbox.check();

    // Reabilita com "Tarjar Tudo (Preto)", "Mascarar CPFs" e "Usar Recomendação IA"
    await page.getByTestId("quick-action-blackout-all-btn").click();
    await expect(applyModalBtn).toContainText("(4 tarjas)");
    await page.getByTestId("quick-action-mask-cpfs-btn").click();
    await expect(page.getByTestId("svg-occurrence-mask-text-occ_1")).toBeVisible();
    await page.getByTestId("quick-action-ai-rec-btn").click();
    await expect(applyModalBtn).toContainText("(4 tarjas)");
    await page.getByTestId("quick-action-mask-cpfs-btn").click();

    // Escopo "Todo o Doc" exibe ocorrências das páginas 1 e 2, filtro por categoria e botão Ver Pág. 2
    await page.getByTestId("scope-all-pages-btn").click();
    await expect(page.getByTestId("occurrence-item-occ_4")).toBeVisible();
    await expect(page.getByTestId("group-toggle-btn-grp_cpf_1").first()).toBeVisible();
    await page.getByTestId("scope-current-page-btn").click();
    await expect(page.getByTestId("occurrence-item-occ_4")).toHaveCount(0);
    await page.getByTestId("scope-all-pages-btn").click();

    // Filtrar lista clicando no chip de categoria e limpar filtro
    await page.getByRole("button", { name: /Financeiro \(1\/1\)/i }).click();
    await expect(page.getByTestId("occurrence-item-occ_4")).toBeVisible();
    await expect(page.getByTestId("occurrence-item-occ_1")).toHaveCount(0);
    await page.getByRole("button", { name: /Limpar Filtro/i }).click();
    await expect(page.getByTestId("occurrence-item-occ_1")).toBeVisible();

    // "Ver Pág. 2" dentro da ocorrência da página 2 navega o visualizador
    await page
      .getByTestId("occurrence-item-occ_4")
      .getByRole("button", { name: /Ver Pág\. 2/i })
      .click();
    await expect(page.getByTestId("current-page-indicator")).toContainText("Página 2 de 2");

    // Seleção em lote por categoria e documento inteiro
    await page.getByTestId("batch-category-btn-PERSONAL_SENSITIVE").click();
    await expect(applyModalBtn).toContainText("(3 tarjas)");
    await page.getByTestId("batch-category-btn-PERSONAL_SENSITIVE").click();
    await expect(applyModalBtn).toContainText("(4 tarjas)");
    await page.getByTestId("deselect-all-doc-btn").click();
    await expect(applyModalBtn).toContainText("(0 tarjas)");
    await page.getByTestId("select-all-doc-btn").click();
    await expect(applyModalBtn).toContainText("(4 tarjas)");
    await page.getByTestId("quick-action-mask-cpfs-btn").click();

    // Modal 1 (Termo de Ciência): "Voltar à Revisão" fecha sem disparar /redact
    await applyModalBtn.click();
    const modal = page.getByTestId("export-and-report-modal");
    await expect(modal).toBeVisible();
    await expect(modal).toHaveAttribute("aria-modal", "true");
    await expect(modal).toContainText("Art. 5º, XI");
    await modal.getByRole("button", { name: /Voltar à Revisão/i }).click();
    await expect(modal).toBeHidden();
    expect(capturedRedactBodies.length).toBe(0);

    // Reabre Modal 1, testa trava do checkbox e confirma execução do POST /redact
    await applyModalBtn.click();
    const checkbox = page.getByTestId("modal-disclaimer-checkbox");
    const confirmBtn = page.getByTestId("confirm-apply-redaction-btn");
    await expect(checkbox).toBeChecked();
    await expect(confirmBtn).toBeEnabled();

    await checkbox.uncheck();
    await expect(confirmBtn).toBeDisabled();
    await checkbox.check();
    await expect(confirmBtn).toBeEnabled();

    await confirmBtn.click();

    // Modal 2 (Validação Cirúrgica e Exportação)
    const validationPanel = page.getByTestId("validation-and-export-panel");
    await expect(validationPanel).toBeVisible({ timeout: 10000 });
    await expect(validationPanel).toContainText("PDF_REDACT_TEXT_REMOVE");
    await expect(validationPanel).toContainText("PDF_REDACT_IMAGE_PIXELS");
    await expect(validationPanel).toContainText("pikepdf");
    await expect(validationPanel).toContainText("APROVADO (100%)");
    await expect(validationPanel).toContainText("sha256_orig_");
    await expect(validationPanel).toContainText("sha256_red_");

    const redactedLink = page.getByTestId("download-redacted-pdf-link");
    const reportLink = page.getByTestId("download-report-pdf-link");
    await expect(redactedLink).toBeVisible();
    await expect(reportLink).toBeVisible();
    expect(await redactedLink.getAttribute("href")).toContain("/download/pdf");
    expect(await reportLink.getAttribute("href")).toContain("/download/report");

    // Stepper ativo avança para 7. Exportar
    const stepNav = page.getByRole("navigation", { name: /Etapas do fluxo de tarjamento/i });
    await expect(stepNav.locator('[aria-current="step"]')).toContainText("7. Exportar");

    expect(capturedRedactBodies.length).toBe(1);
    const payload = capturedRedactBodies[0] as {
      anonymization_disclaimer_acknowledged: boolean;
      items: Array<{ id: string; action_mode: string }>;
    };
    expect(payload.anonymization_disclaimer_acknowledged).toBe(true);
    expect(payload.items.find((i) => i.id === "occ_1")?.action_mode).toBe("PARTIAL_MASK");

    // "Voltar e Editar Modos" fecha modal e permite nova edição
    await page.getByTestId("back-to-edit-modes-btn").click();
    await expect(modal).toBeHidden();
  });
});
