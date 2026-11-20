"use client";

import React from "react";
import { Download, FileCheck, ShieldCheck } from "lucide-react";
import { AppFooter } from "@/components/layout/AppFooter";
import { AppHeader } from "@/components/layout/AppHeader";
import { Badge, Button, buttonVariants, Card } from "@/components/ui";
import { CustomRegexPanel } from "@/components/upload/CustomRegexPanel";
import { DocumentProgressCard } from "@/components/upload/DocumentProgressCard";
import { DropzoneUpload } from "@/components/upload/DropzoneUpload";
import { SeiBoilerplateRuleToggle } from "@/components/upload/SeiBoilerplateRuleToggle";
import {
  ExportAndReportModal,
  LightweightPageViewer,
  OccurrencesSidebar,
  ProcessingVerbosePanel,
} from "@/components/workspace";
import { useDocumentQueue } from "@/hooks/useDocumentQueue";

export default function HomePage() {
  const {
    documents,
    activeDocId,
    activeDoc,
    activeVerboseLogs,
    activeProcessingPage,
    activeStepNumber,
    showAddFilesDropzone,
    setShowAddFilesDropzone,
    customRegexRules,
    setCustomRegexRules,
    ignoreSeiStandardBlocks,
    setIgnoreSeiStandardBlocks,
    syncGroupByDefault,
    setSyncGroupByDefault,
    activeOccurrenceId,
    setActiveOccurrenceId,
    isManualDrawMode,
    setIsManualDrawMode,
    manualDrawCategory,
    setManualDrawCategory,
    isExportModalOpen,
    setIsExportModalOpen,
    isApplyingRedaction,
    redactionError,
    hasAnyUploadInFlight,
    deselectedSet,
    currentPageOccurrences,
    currentPageManualBoxes,
    selectedAutoCount,
    currentPageImageUrl,
    nextPageImageUrl,
    setActiveDocPage,
    handleFilesSelected,
    handleSelectForReview,
    handleRetryDocument,
    handleRemoveDocument,
    handleResetAll,
    handleSetOccurrenceActionMode,
    handleToggleOccurrence,
    handleApplyAiRecommendation,
    handleSetAllBlackout,
    handleMaskAllCpfs,
    handleSetAllDocumentSelection,
    handleSetCategorySelection,
    handleSetGroupSelection,
    handleAddManualBox,
    handleUpdateManualBox,
    handleRemoveManualBox,
    handleOpenConfirmRedactionModal,
    handleBackToEditModes,
    handleConfirmAndApplyRedaction,
  } = useDocumentQueue();

  return (
    <div className="flex min-h-screen flex-col bg-surface-page text-ink-primary">
      <a href="#main-content" className="skip-link">
        Pular para o conteúdo principal
      </a>

      <AppHeader
        activeStepNumber={activeStepNumber}
        hasActiveDoc={Boolean(activeDoc)}
        documentsCount={documents.length}
        showAddFilesDropzone={showAddFilesDropzone}
        onToggleAddFilesDropzone={() =>
          setShowAddFilesDropzone((prev) => !prev)
        }
        onResetAll={handleResetAll}
      />

      {activeDoc && activeDoc.isReadyForReview ? (
        <main
          id="main-content"
          className="mx-auto grid w-full max-w-[1400px] flex-1 grid-cols-1 items-start gap-5 px-4 pb-6 pt-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_396px] lg:px-8"
        >
          {/* Coluna Esquerda: Fila Compacta + Visualizador Thin-Client + Auditoria Discreta */}
          <div className="flex min-w-0 flex-col gap-4">
            {showAddFilesDropzone && (
              <section aria-label="Envio de documentos PDF">
                <Card padded>
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-2 border-b border-line-subtle pb-3.5">
                    <div>
                      <h2 className="text-[15px] font-semibold tracking-tight text-ink-primary">
                        Anexar Documento(s) PDF para Identificação e Tarjamento
                        Definitivo
                      </h2>
                      <p className="mt-0.5 text-xs text-ink-muted">
                        Arquitetura Thin-Client (WebP + SVG) com suporte a
                        múltiplos arquivos e liberação imediata da Página 1 (
                        <em>First-Page-First</em>).
                      </p>
                    </div>
                    <Badge variant="neutral">
                      Otimizado para Estações Administrativas UFAM
                    </Badge>
                  </div>

                  <DropzoneUpload
                    onFilesSelected={handleFilesSelected}
                    isUploading={hasAnyUploadInFlight}
                    uploadError={null}
                    maxSizeMb={50}
                    compact
                  />
                  <SeiBoilerplateRuleToggle
                    checked={ignoreSeiStandardBlocks}
                    onChange={setIgnoreSeiStandardBlocks}
                    disabled={hasAnyUploadInFlight}
                  />
                  <CustomRegexPanel
                    rules={customRegexRules}
                    onChange={setCustomRegexRules}
                    disabled={hasAnyUploadInFlight}
                  />
                </Card>
              </section>
            )}

            {documents.length > 0 && (
              <section
                aria-label="Progresso individual dos documentos"
                className="flex flex-col gap-2.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                    Documentos na Sessão ({documents.length})
                  </h2>
                  {documents.length > 1 && (
                    <span className="text-xs text-ink-muted">
                      Clique em{" "}
                      <strong className="font-semibold text-ink-primary">
                        Revisar
                      </strong>{" "}
                      em qualquer documento pronto para alternar a visualização.
                    </span>
                  )}
                </div>

                <div className="flex flex-col gap-2.5">
                  {documents.map((docItem) => (
                    <DocumentProgressCard
                      key={docItem.clientId}
                      item={docItem}
                      isActive={docItem.clientId === activeDocId}
                      compact
                      onSelectForReview={handleSelectForReview}
                      onRetry={handleRetryDocument}
                      onRemove={handleRemoveDocument}
                    />
                  ))}
                </div>

                {activeDoc.isRedactionComplete && (
                  <div className="flex flex-wrap items-center gap-2 rounded-xl border border-brand-border bg-brand-subtle px-4 py-2.5">
                    <Button
                      variant="subtle"
                      size="sm"
                      onClick={() => setIsExportModalOpen(true)}
                    >
                      <ShieldCheck
                        className="h-4 w-4 text-brand-primary"
                        aria-hidden="true"
                      />
                      <span>Ver Checklist de Segurança</span>
                    </Button>

                    {activeDoc.downloadPdfUrl && (
                      <a
                        href={activeDoc.downloadPdfUrl}
                        download
                        className={buttonVariants({
                          variant: "primary",
                          size: "sm",
                        })}
                      >
                        <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>Baixar PDF Tarjado</span>
                      </a>
                    )}

                    {activeDoc.downloadReportUrl && (
                      <a
                        href={activeDoc.downloadReportUrl}
                        download
                        className={buttonVariants({
                          variant: "secondary",
                          size: "sm",
                        })}
                      >
                        <FileCheck
                          className="h-3.5 w-3.5 text-brand-primary"
                          aria-hidden="true"
                        />
                        <span>Relatório (Zero-PII)</span>
                      </a>
                    )}
                  </div>
                )}
              </section>
            )}

            <section
              aria-label="Workspace Split-Screen de Revisão"
              className="min-w-0"
            >
              <LightweightPageViewer
                currentPage={activeDoc.currentPage}
                totalPages={activeDoc.totalPages}
                pageImageUrl={currentPageImageUrl}
                nextPageImageUrl={nextPageImageUrl}
                pageOccurrences={currentPageOccurrences}
                pageManualBoxes={currentPageManualBoxes}
                deselectedIds={deselectedSet}
                actionModesById={activeDoc.actionModesById}
                activeOccurrenceId={activeOccurrenceId}
                isManualDrawMode={isManualDrawMode}
                manualDrawCategory={manualDrawCategory}
                onPageChange={setActiveDocPage}
                onToggleOccurrence={handleToggleOccurrence}
                onSelectOccurrence={setActiveOccurrenceId}
                onToggleManualDrawMode={() =>
                  setIsManualDrawMode((prev) => !prev)
                }
                onManualDrawCategoryChange={setManualDrawCategory}
                onAddManualBox={handleAddManualBox}
                onUpdateManualBox={handleUpdateManualBox}
                onRemoveManualBox={handleRemoveManualBox}
              />
            </section>

            <ProcessingVerbosePanel logs={activeVerboseLogs} />
          </div>

          {/* Coluna Direita: Painel Lateral de Decisão HITL */}
          <OccurrencesSidebar
            status={activeDoc.jobStatus}
            documentProfile={activeDoc.documentProfile}
            processedPages={activeDoc.processedPages}
            totalPages={activeDoc.totalPages}
            progressPercentage={activeDoc.progressPercent}
            currentProcessingPage={activeProcessingPage}
            errorMessage={activeDoc.errorMessage}
            currentPage={activeDoc.currentPage}
            allOccurrences={activeDoc.occurrences}
            manualBoxes={activeDoc.manualBoxes}
            deselectedIds={deselectedSet}
            actionModesById={activeDoc.actionModesById}
            activeOccurrenceId={activeOccurrenceId}
            isApplyingRedaction={isApplyingRedaction}
            applyToGroupByDefault={syncGroupByDefault}
            onApplyToGroupChange={setSyncGroupByDefault}
            onPageChange={setActiveDocPage}
            onSelectOccurrence={setActiveOccurrenceId}
            onToggleOccurrence={handleToggleOccurrence}
            onSetOccurrenceActionMode={handleSetOccurrenceActionMode}
            onApplyAiRecommendation={handleApplyAiRecommendation}
            onSetAllBlackout={handleSetAllBlackout}
            onMaskAllCpfs={handleMaskAllCpfs}
            onSetAllDocumentSelection={handleSetAllDocumentSelection}
            onSetCategorySelection={handleSetCategorySelection}
            onSetGroupSelection={handleSetGroupSelection}
            onRemoveManualBox={handleRemoveManualBox}
            onOpenConfirmRedactionModal={handleOpenConfirmRedactionModal}
            onRetryDocument={() => handleRetryDocument(activeDoc.clientId)}
          />

          <ExportAndReportModal
            isOpen={isExportModalOpen}
            isApplyingRedaction={isApplyingRedaction}
            isRedactionComplete={activeDoc.isRedactionComplete}
            selectedAutoCount={selectedAutoCount}
            manualBoxesCount={activeDoc.manualBoxes.length}
            auditLog={activeDoc.auditLog}
            downloadPdfUrl={activeDoc.downloadPdfUrl}
            downloadReportUrl={activeDoc.downloadReportUrl}
            errorMessage={redactionError}
            onClose={() => setIsExportModalOpen(false)}
            onBackToEditModes={handleBackToEditModes}
            onConfirmAndApplyRedaction={handleConfirmAndApplyRedaction}
          />
        </main>
      ) : (
        <main
          id="main-content"
          className="mx-auto w-full max-w-[1400px] flex-1 space-y-5 px-4 pb-6 pt-5 sm:px-6 lg:px-8"
        >
          {documents.length > 0 && (
            <section
              aria-label="Progresso individual dos documentos"
              className="space-y-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                  Documentos na Sessão ({documents.length})
                </h2>
                <span className="text-xs text-ink-muted">
                  Clique em{" "}
                  <strong className="font-semibold text-ink-primary">
                    Revisar
                  </strong>{" "}
                  em qualquer documento pronto para alternar a visualização.
                </span>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {documents.map((docItem) => (
                  <DocumentProgressCard
                    key={docItem.clientId}
                    item={docItem}
                    isActive={docItem.clientId === activeDocId}
                    onSelectForReview={handleSelectForReview}
                    onRetry={handleRetryDocument}
                    onRemove={handleRemoveDocument}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Área de Upload Inicial */}
          <section aria-label="Envio de documentos PDF">
            <Card padded className="p-5 sm:p-6">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-line-subtle pb-4">
                <div>
                  <h2 className="text-base font-semibold tracking-tight text-ink-primary">
                    Anexar Documento(s) PDF para Identificação e Tarjamento
                    Definitivo
                  </h2>
                  <p className="mt-0.5 text-xs text-ink-muted">
                    Arquitetura Thin-Client (WebP + SVG) com suporte a múltiplos
                    arquivos e liberação imediata da Página 1 (
                    <em>First-Page-First</em>).
                  </p>
                </div>
                <Badge variant="neutral">
                  Otimizado para Estações Administrativas UFAM
                </Badge>
              </div>

              <DropzoneUpload
                onFilesSelected={handleFilesSelected}
                isUploading={hasAnyUploadInFlight}
                uploadError={null}
                maxSizeMb={50}
                compact={false}
              />
              <SeiBoilerplateRuleToggle
                checked={ignoreSeiStandardBlocks}
                onChange={setIgnoreSeiStandardBlocks}
                disabled={hasAnyUploadInFlight}
              />
              <CustomRegexPanel
                rules={customRegexRules}
                onChange={setCustomRegexRules}
                disabled={hasAnyUploadInFlight}
              />
            </Card>
          </section>
        </main>
      )}

      <AppFooter />
    </div>
  );
}
