"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Container,
  Box,
  Card,
  CardContent,
  Button,
  Chip,
  CircularProgress,
  Alert,
  AlertTitle,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Snackbar,
  Stack,
  Typography,
  Tabs,
  Tab,
  LinearProgress,
  Divider,
  IconButton,
  Link as MuiLink,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Checkbox,
  FormControlLabel,
} from "@mui/material";
import {
  CloudUpload as CloudUploadIcon,
  Error as ErrorIcon,
  ExpandMore as ExpandMoreIcon,
  History as HistoryIcon,
  Close as CloseIcon,
  CalendarToday as CalendarIcon,
} from "@mui/icons-material";
import NextLink from "next/link";
import ServiceWorkerRegistration from "../components/ServiceWorkerRegistration";
import TaxDisclaimer from "../components/TaxDisclaimer";
import PortfolioSummaryCards from "../components/PortfolioSummaryCards";
import PositionsTable from "../components/PositionsTable";
import HarvestingSuggestions from "../components/HarvestingSuggestions";
import WashSaleWarning from "../components/WashSaleWarning";
import WashSaleChainPanel, {
  washChainsVisible,
} from "../components/WashSaleChainPanel";
import {
  useAnalyzePortfolio,
  useTaxProfile,
  usePortfolioHistory,
  useBackendHealth,
  fetchAnalysisById,
  fetchWashSaleChains,
  WASH_SALE_CHAIN_REANALYZE_HINT,
  cleanupOrphanHistory,
  deleteAnalysis,
  persistGuestAnalysis,
  getAnalysisErrorMessage,
  getBackendUnreachableMessage,
} from "@/lib/api";
import FirstRunEmptyState from "../components/FirstRunEmptyState";
import AppShell from "../components/AppShell";
import Supplemental1099InsightsPanel from "../components/Supplemental1099InsightsPanel";
import {
  PACKET_SCOPE_COPY,
  SUPPLEMENTAL_1099_AFTER_FIRST_RUN_COPY,
  SUPPLEMENTAL_1099_AFTER_FIRST_RUN_TITLE,
  SUPPLEMENTAL_1099_CONTEXT_COPY,
  SUPPLEMENTAL_1099_UNKNOWN_YEAR_HELPER,
  SUPPLEMENTAL_1099_UNKNOWN_YEAR_RESTORED_HELPER,
  SUPPLEMENTAL_1099_UPLOAD_TITLE,
  csvWashSaleDisallowedTotal,
  isSameYear1099Compare,
  isUnknown1099Year,
  redactUnpaidLotMatch,
} from "@/lib/supplemental1099";
import YearClosePacketPanel, {
  isYearClosePacketPaid,
  PACKET_CHECKOUT_OTHER_TAB_COPY,
  rememberYearClosePacketPaid,
  yearClosePacketStorageId,
} from "../components/YearClosePacketPanel";
import LatestRunBar from "../components/LatestRunBar";
import {
  buildHarvestTeasers,
  formatUsd,
  harvestTeaserTotals,
  purchaseFillUnknownCopy,
} from "@/lib/harvestPreview";
import { useAuth } from "@/app/context/auth";
import { isEmailConfirmed } from "@/lib/supabase";
import { beginGuestPersist, endGuestPersist } from "@/lib/guest-persist-lock";
import { useQueryClient } from "@tanstack/react-query";
import type {
  PortfolioAnalysis,
  AnalysisHistoryItem,
  Position,
} from "@/lib/types";
import { consumeAutomaticSampleRerun } from "@/lib/automaticSampleRerun";
import {
  clearPacketResumeLocal,
  clearPacketResumeSessionMarkers,
  clearYearClosePacketClientState,
  consumePacketResumeLocalHandoff,
  PACKET_RESUME_REUPLOAD_COPY,
  packetCheckoutStartedRecently,
  readPacketResumeExpected,
  resumeAnalysisMatches,
} from "@/lib/packetResumeLocal";
import { SAMPLE_FETCH_TIMEOUT_MS } from "@/lib/constants";
import {
  DASHBOARD_RESUME_CHECKOUT_PATH,
  LOAD_SAMPLE_KEY,
  OPEN_RUNS_KEY,
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_SAMPLE_KEY,
  RESUME_CHECKOUT_PARAM,
  UPLOAD_INTENT_KEY,
  nextParam,
  safeNextPath,
  signInHref,
} from "@/lib/safeNextPath";

export const dynamic = "force-dynamic";

const GUEST_UNSAVED_KEY = "oth-guest-unsaved";
const GUEST_UNSAVED_FILENAME_KEY = "oth-guest-unsaved-filename";

function rearmResumeCheckout(): void {
  try {
    sessionStorage.setItem(PACKET_RESUME_CHECKOUT_KEY, "1");
    sessionStorage.setItem(PACKET_RESUME_SAMPLE_KEY, "1");
    const url = new URL(window.location.href);
    url.searchParams.set(RESUME_CHECKOUT_PARAM, "1");
    const next = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState(window.history.state, "", next);
  } catch {
    // ignore quota / private-mode failures
  }
}
const SAMPLE_CSV_URL = "/sample-robinhood-transactions.csv";
const SAMPLE_CSV_FILENAME = "sample-robinhood-transactions.csv";
const SAMPLE_1099_URL = "/sample-robinhood-1099-2026.pdf";
const SAMPLE_1099_FILENAME = "sample-robinhood-1099-2026.pdf";
const SAMPLE_FETCH_TIMEOUT_MESSAGE = "Could not load the 2026 sample.";
const ORIGIN_RESUME_WAIT_MS = 12_000;
const ORIGIN_RESUME_RETRY_MS = 3_000;
/** Signed-out second tab during sign-in. Shorter than a sample re-analyze. */
export const SIGNED_OUT_RESUME_TIMEOUT_MS = 8_000;

let freshUploadAnalysisId: string | null = null;

/** Tests only. A full reload already starts with no fresh-upload id. */
export function resetAnalysisOriginForTests(): void {
  freshUploadAnalysisId = null;
}

export function rememberFreshUploadOrigin(analysisId: string | undefined): void {
  freshUploadAnalysisId = analysisId || null;
}

function sourceForStoredAnalysis(
  saved: PortfolioAnalysis,
): "fresh-upload" | "restored-session" {
  if (
    freshUploadAnalysisId &&
    saved.analysis_id &&
    saved.analysis_id === freshUploadAnalysisId
  ) {
    return "fresh-upload";
  }
  return "restored-session";
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

async function fetchSampleCsvAnd1099(
  externalSignal?: AbortSignal,
): Promise<{
  csvFile: File;
  form1099File: File;
}> {
  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    SAMPLE_FETCH_TIMEOUT_MS,
  );
  const onExternalAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener("abort", onExternalAbort);
    }
  }
  try {
    const [csvResponse, pdfResponse] = await Promise.all([
      fetch(SAMPLE_CSV_URL, { signal: controller.signal }),
      fetch(SAMPLE_1099_URL, { signal: controller.signal }),
    ]);
    if (!csvResponse.ok) {
      throw new Error("Could not load the sample CSV.");
    }
    if (!pdfResponse.ok) {
      throw new Error("Could not load the sample 1099.");
    }
    const [csvBlob, pdfBlob] = await Promise.all([
      csvResponse.blob(),
      pdfResponse.blob(),
    ]);
    return {
      csvFile: new File([csvBlob], SAMPLE_CSV_FILENAME, { type: "text/csv" }),
      form1099File: new File([pdfBlob], SAMPLE_1099_FILENAME, {
        type: "application/pdf",
      }),
    };
  } catch (error) {
    if (isAbortError(error)) {
      throw new Error(SAMPLE_FETCH_TIMEOUT_MESSAGE);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", onExternalAbort);
  }
}

type AnalysisSource = "fresh-upload" | "saved-history" | "restored-session";

function formatBookDate(iso?: string | null): string | null {
  if (!iso) {
    return null;
  }
  const parsed = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function savedTradeBook(history?: AnalysisHistoryItem[] | null): {
  id: string;
  filename: string;
  first: string | null;
  last: string | null;
  count: number;
} | null {
  const item = history?.[0];
  const count = item?.summary?.activity_transaction_count ?? 0;
  if (!item || count <= 0) {
    return null;
  }
  return {
    id: item.id,
    filename: item.filename,
    first: formatBookDate(item.summary.activity_first_date),
    last: formatBookDate(item.summary.activity_last_date),
    count,
  };
}

function uploadIntroCopy({
  tradeBook,
  replaceBook,
  signedIn,
}: {
  tradeBook: ReturnType<typeof savedTradeBook>;
  replaceBook: boolean;
  signedIn: boolean;
}): string {
  if (tradeBook && !replaceBook) {
    const range =
      tradeBook.first && tradeBook.last
        ? ` (${tradeBook.first} – ${tradeBook.last})`
        : "";
    return `We'll add new activity to your book from ${tradeBook.filename}${range}. Overlap is fine — no need to re-export everything.`;
  }
  if (signedIn) {
    return "First upload: go back to the oldest shares you still hold. Later this year you can send only new activity plus a little overlap.";
  }
  return "Upload your Robinhood CSV export (and optional 1099) to reconcile 1099-B and wash-sale gaps toward Schedule D. Sign in, then upload a full export so later files only need new activity.";
}

type DeleteTarget = {
  id: string;
  filename: string;
};

function buildPositionId(position: Position, index: number): string {
  return (
    position.position_id ??
    [
      position.symbol,
      position.asset_type,
      position.earliest_purchase_date ?? "unknown",
      index,
    ].join(":")
  );
}

function normalizeAnalysis(analysis: PortfolioAnalysis): PortfolioAnalysis {
  return {
    ...analysis,
    positions: analysis.positions.map((position, index) => ({
      ...position,
      position_id: buildPositionId(position, index),
    })),
  };
}

function getDisplayedAnalysis(
  loadedAnalysis: PortfolioAnalysis | null,
  analysis: PortfolioAnalysis | undefined,
): PortfolioAnalysis | null {
  if (loadedAnalysis) {
    return normalizeAnalysis(loadedAnalysis);
  }

  if (analysis) {
    return normalizeAnalysis(analysis);
  }

  return null;
}

// Helper function: restore analysis from session storage
function restoreAnalysisFromStorage(): PortfolioAnalysis | null {
  try {
    const saved = sessionStorage.getItem("optionstaxhub-analysis");
    return saved
      ? normalizeAnalysis(JSON.parse(saved) as PortfolioAnalysis)
      : null;
  } catch {
    return null;
  }
}

// Helper function: save analysis to session storage
function saveAnalysisToStorage(analysis: PortfolioAnalysis): void {
  try {
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify(redactUnpaidLotMatch(analysis)),
    );
  } catch {
    // Storage full — ignore
  }
}

function getAnalysisSourceDisplay(source: AnalysisSource): {
  label: string;
  color: "success" | "info" | "default";
} {
  switch (source) {
    case "fresh-upload":
      return { label: "Fresh upload", color: "success" };
    case "saved-history":
      return { label: "Saved analysis", color: "info" };
    case "restored-session":
      return { label: "Restored from browser", color: "default" };
  }
}

function isSamplePriceWarning(warning: string): boolean {
  return /live prices were unavailable|fallback pric|csv-provided price|csv-provided option|prices as of your csv/i.test(
    warning,
  );
}

function warningsForDisplay(analysis: PortfolioAnalysis): string[] {
  const warnings = analysis.warnings ?? [];
  if (!analysis.sample_run) {
    return warnings;
  }
  return warnings.filter((warning) => !isSamplePriceWarning(warning));
}

function getConfidenceSummary(analysis: PortfolioAnalysis): {
  severity: "success" | "info" | "warning";
  label: string;
  summary: string;
  details: string[];
} {
  const warnings = warningsForDisplay(analysis);
  const details: string[] = [];

  if (
    warnings.some((warning) =>
      /had no open lots at all|matched the required asset type|exceeded the available open lot quantity/i.test(
        warning,
      ),
    )
  ) {
    details.push(
      "Some sells could not be matched to complete tax lots, so related gains or losses were excluded.",
    );
  }

  if (
    warnings.some((warning) =>
      /Corporate action activity|stock split|share position may need manual verification/i.test(
        warning,
      ),
    )
  ) {
    details.push(
      "Unsupported brokerage events may still affect share counts or current position accuracy.",
    );
  }

  if (
    warnings.some((warning) =>
      /(^Row )|(CSV row\(s\) could not be parsed)/i.test(warning),
    )
  ) {
    details.push(
      "Some CSV rows could not be parsed and were excluded from the analysis.",
    );
  }

  const csvPriceDetail =
    !analysis.sample_run && analysis.price_basis && !analysis.price_basis.live
      ? analysis.price_basis.label || "Prices came from your CSV."
      : "";
  if (csvPriceDetail) {
    details.push(csvPriceDetail);
  } else if (
    !analysis.sample_run &&
    warnings.some((warning) =>
      /live prices were unavailable|prices as of your csv/i.test(warning),
    )
  ) {
    details.push("Prices came from your CSV.");
  }

  if (details.length === 0) {
    return {
      severity: "success",
      label: "High confidence",
      summary:
        "No major data quality issues were detected in this result. You can use the harvest queue as a strong first pass for this tax year.",
      details: [],
    };
  }

  if (
    details.length === 1 &&
    /prices as of your csv|prices came from your csv|purchase price from your csv/i.test(
      details[0],
    )
  ) {
    const purchaseFill = /purchase price from your csv/i.test(details[0]);
    return {
      severity: "info",
      label: "Moderate confidence",
      summary: purchaseFill
        ? "This result is usable. The prices on this page are purchase fills from your CSV."
        : "This result is usable. The prices on this page are marked with your CSV date.",
      details,
    };
  }

  return {
    severity: "warning",
    label: "Partial confidence",
    summary:
      "Use these results as a starting point, but verify the affected holdings before acting on the analysis.",
    details,
  };
}

function getRecommendedNextSteps(
  analysis: PortfolioAnalysis,
  packetUnlocked = true,
): string[] {
  const steps: string[] = [];

  if (!packetUnlocked) {
    const teasers = buildHarvestTeasers(
      analysis.suggestions,
      analysis.positions,
    );
    const totals = harvestTeaserTotals(teasers);
    if (totals.count > 0) {
      steps.push(
        `${totals.count} lot${totals.count === 1 ? "" : "s"} show ${formatUsd(
          totals.totalLoss,
        )} in unrealized losses. The $49 PDF adds sell instructions, replacements, wash chains, and harvest notes.`,
      );
    } else if (totals.unknownLossCount === 0) {
      steps.push(
        "This page shows the free compare. The $49 PDF adds harvest notes, wash chains, and lot-level 1099 reconciliation.",
      );
    }
    if (totals.unknownLossCount > 0) {
      steps.push(purchaseFillUnknownCopy(totals.unknownLossCount));
    }
    return steps;
  }
  const lossSuggestions = analysis.suggestions.filter(
    (suggestion) => suggestion.action !== "REVIEW" && !suggestion.price_note,
  );
  const unknownLossCount = analysis.suggestions.length - lossSuggestions.length;
  if (lossSuggestions.length > 0) {
    steps.push(
      `Start with the ${lossSuggestions.length} ${lossSuggestions.length === 1 ? "lot" : "lots"} in the harvest queue.`,
    );
  } else if (unknownLossCount === 0) {
    steps.push(
      "The harvest queue is empty, so review the lot ledger and realized gains for this tax year first.",
    );
  }
  if (unknownLossCount > 0) {
    steps.push(purchaseFillUnknownCopy(unknownLossCount));
  }

  if (analysis.wash_sale_flags.length > 0) {
    steps.push(
      "Review the wash-sale radar before relying on losses from affected symbols.",
    );
  }

  if (warningsForDisplay(analysis).length > 0) {
    steps.push(
      "After that, check the data quality notes for any positions that may need manual verification.",
    );
  }

  return steps;
}

function getSkippedSuggestionSymbols(analysis: PortfolioAnalysis): string[] {
  const symbols = new Set<string>();
  const skippedSuggestionPattern =
    /^Skipped automated harvesting suggestions for (\S+) stock lots/i;

  for (const warning of analysis.warnings ?? []) {
    const match = skippedSuggestionPattern.exec(warning);
    if (match?.[1]) {
      symbols.add(match[1]);
    }
  }

  return Array.from(symbols).sort((left, right) => left.localeCompare(right));
}

function getSkippedSuggestionSymbolsForAnalysis(
  analysis: PortfolioAnalysis | null,
): string[] {
  if (!analysis) {
    return [];
  }

  return getSkippedSuggestionSymbols(analysis);
}

function SuggestionsPanel({
  suggestions,
  skippedSuggestionSymbols,
  lotsWithLosses,
  locked,
  positions,
  priceBasis,
}: Readonly<{
  suggestions: PortfolioAnalysis["suggestions"];
  skippedSuggestionSymbols: string[];
  lotsWithLosses: number;
  locked: boolean;
  positions: PortfolioAnalysis["positions"];
  priceBasis?: PortfolioAnalysis["price_basis"];
}>) {
  return (
    <Stack spacing={2}>
      {skippedSuggestionSymbols.length > 0 && (
        <Alert severity="warning" variant="outlined">
          <AlertTitle>Manual review needed</AlertTitle>
          The harvest queue skipped{" "}
          {skippedSuggestionSymbols.join(", ")} because a split or corporate
          action changed the reported share count. Review those symbols manually
          before acting.
        </Alert>
      )}
      <HarvestingSuggestions
        suggestions={suggestions}
        lotsWithLosses={lotsWithLosses}
        locked={locked}
        positions={positions}
        priceBasis={priceBasis}
      />
    </Stack>
  );
}

function shouldRecommendSupplemental1099(
  analysis: PortfolioAnalysis | null,
  selectedSupplemental1099File: File | null,
): boolean {
  return Boolean(
    analysis && !analysis.supplemental_1099 && !selectedSupplemental1099File,
  );
}

function getSupplemental1099Warnings(warnings: string[] | undefined): string[] {
  return (warnings ?? []).filter((warning) => /1099|supplemental/i.test(warning));
}

function getSupplemental1099HelperText({
  isApplied,
  isRestoredAppliedSummary,
  hasSelectedFile,
  hasUploadedCsv,
  sameYearCompare,
  unknownYear,
}: {
  isApplied: boolean;
  isRestoredAppliedSummary: boolean;
  hasSelectedFile: boolean;
  hasUploadedCsv: boolean;
  sameYearCompare: boolean;
  unknownYear: boolean;
}): string {
  if (isRestoredAppliedSummary) {
    if (sameYearCompare) {
      return `This restored result already includes 1099 vs this export (totals stay free). ${PACKET_SCOPE_COPY} Upload the PDF again only if you want to refresh it.`;
    }
    if (unknownYear) {
      return SUPPLEMENTAL_1099_UNKNOWN_YEAR_RESTORED_HELPER;
    }
    return "This restored result already includes a previous-year broker 1099 as a supplement. Upload the PDF again only if you want to refresh it.";
  }

  if (isApplied) {
    if (sameYearCompare) {
      return `Included as 1099 vs this export (totals stay free). ${PACKET_SCOPE_COPY}`;
    }
    if (unknownYear) {
      return SUPPLEMENTAL_1099_UNKNOWN_YEAR_HELPER;
    }
    return "Included as a previous-year 1099 supplement — not a same-year lot match.";
  }

  if (hasSelectedFile && hasUploadedCsv) {
    return `This PDF is being used with your latest CSV. Totals compare stays free. ${PACKET_SCOPE_COPY} Replace it to automatically refresh the result.`;
  }

  if (hasSelectedFile) {
    return `This PDF is ready and will be included the next time you analyze a CSV. Totals compare stays free. ${PACKET_SCOPE_COPY}`;
  }

  return SUPPLEMENTAL_1099_CONTEXT_COPY;
}

function getSupplemental1099Status({
  isApplied,
  isRestoredAppliedSummary,
  hasSelectedFile,
  hasUploadedCsv,
}: {
  isApplied: boolean;
  isRestoredAppliedSummary: boolean;
  hasSelectedFile: boolean;
  hasUploadedCsv: boolean;
}): {
  label: string;
  color: "success" | "info" | "default";
  variant: "filled" | "outlined";
} {
  if (isRestoredAppliedSummary) {
    return {
      label: "Included in restored result",
      color: "info",
      variant: "outlined",
    };
  }

  if (isApplied) {
    return {
      label: "Included in current analysis",
      color: "success",
      variant: "filled",
    };
  }

  if (hasSelectedFile && hasUploadedCsv) {
    return {
      label: "Auto-applied to latest CSV",
      color: "info",
      variant: "outlined",
    };
  }

  if (hasSelectedFile) {
    return {
      label: "Ready for next analysis",
      color: "info",
      variant: "outlined",
    };
  }

  return {
    label: "Optional",
    color: "default",
    variant: "outlined",
  };
}

function Supplemental1099UploadPanel({
  selectedFileName,
  appliedSummary,
  analysisTaxYear,
  analysisSource,
  hasUploadedCsv,
  canRemoveSupplement,
  onClearFile,
  isPending,
}: Readonly<{
  selectedFileName: string | null;
  appliedSummary: PortfolioAnalysis["supplemental_1099"] | null | undefined;
  analysisTaxYear?: number | null;
  analysisSource: AnalysisSource | null;
  hasUploadedCsv: boolean;
  canRemoveSupplement: boolean;
  onChooseFile?: () => void;
  onClearFile: () => void;
  isPending: boolean;
}>) {
  const displayFileName =
    selectedFileName ?? appliedSummary?.source_filename ?? null;
  const isApplied = Boolean(appliedSummary);
  const hasSelectedFile = Boolean(displayFileName);
  const isRestoredAppliedSummary =
    Boolean(appliedSummary) &&
    !selectedFileName &&
    (analysisSource === "restored-session" ||
      analysisSource === "saved-history");
  const sameYearCompare = isSameYear1099Compare(
    appliedSummary?.tax_year,
    analysisTaxYear,
  );
  const unknownYear =
    Boolean(appliedSummary) && isUnknown1099Year(appliedSummary?.tax_year);
  const helperText = getSupplemental1099HelperText({
    isApplied,
    isRestoredAppliedSummary,
    hasSelectedFile,
    hasUploadedCsv,
    sameYearCompare,
    unknownYear,
  });
  const status = getSupplemental1099Status({
    isApplied,
    isRestoredAppliedSummary,
    hasSelectedFile,
    hasUploadedCsv,
  });

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: isApplied ? "success.light" : "divider",
        borderRadius: 3,
        p: 2,
        background: isApplied
          ? "rgba(122, 158, 132, 0.12)"
          : "rgba(236, 234, 228, 0.03)",
      }}
    >
      <Stack spacing={1.5}>
        <Stack
          direction={{ xs: "column", md: "row" }}
          spacing={1.25}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", md: "center" }}
        >
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
              {SUPPLEMENTAL_1099_UPLOAD_TITLE}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {helperText}
            </Typography>
          </Box>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Chip
              size="small"
              color={status.color}
              variant={status.variant}
              label={status.label}
            />
            {displayFileName && (
              <Chip
                label={displayFileName}
                size="small"
                color="info"
                variant="outlined"
              />
            )}
          </Stack>
        </Stack>

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button
            component="label"
            htmlFor="desk-1099-input"
            variant={displayFileName ? "outlined" : "contained"}
            size="small"
            disabled={isPending}
          >
            {displayFileName ? "Replace 1099 PDF" : "Add 1099 PDF"}
          </Button>
          {canRemoveSupplement && (
            <Button
              size="small"
              color="inherit"
              onClick={onClearFile}
              disabled={isPending}
            >
              Remove 1099 PDF
            </Button>
          )}
        </Stack>
      </Stack>
    </Box>
  );
}

function ResultsSection({
  displayedAnalysis,
  analysisSource,
  sourceDisplay,
  confidenceSummary,
  recommendedNextSteps,
  activeTab,
  setActiveTab,
  skippedSuggestionSymbols,
  shouldPrompt1099Supplement,
  guest,
  packetUnlocked,
  washChainHint,
}: Readonly<{
  displayedAnalysis: PortfolioAnalysis;
  analysisSource: AnalysisSource | null;
  sourceDisplay: ReturnType<typeof getAnalysisSourceDisplay> | null;
  confidenceSummary: ReturnType<typeof getConfidenceSummary> | null;
  recommendedNextSteps: string[];
  activeTab: number;
  setActiveTab: (value: number) => void;
  skippedSuggestionSymbols: string[];
  shouldPrompt1099Supplement: boolean;
  guest?: boolean;
  packetUnlocked: boolean;
  washChainHint?: string | null;
}>) {
  const harvestTeasers = buildHarvestTeasers(
    displayedAnalysis.suggestions,
    displayedAnalysis.positions,
  );
  const harvestTotals = harvestTeaserTotals(harvestTeasers);
  const realSuggestionCount = displayedAnalysis.suggestions.filter(
    (suggestion) => suggestion.action !== "REVIEW" && !suggestion.price_note,
  ).length;
  const suggestionTabCount = packetUnlocked
    ? realSuggestionCount
    : harvestTotals.count;
  const washSaleChains = displayedAnalysis.wash_sale_chains ?? [];
  const showChainHint =
    Boolean(washChainHint) &&
    !washChainsVisible(displayedAnalysis, packetUnlocked);

  return (
    <>
      {guest && (
        <Alert severity="info" variant="outlined">
          <AlertTitle>This run lives on this device</AlertTitle>
          Sign in to keep harvest, wash sales, and the packet for next
          December.{" "}
          <Button
            component={NextLink}
            href={signInHref("/dashboard")}
            size="small"
            sx={{ textTransform: "none", minWidth: "auto", p: 0 }}
          >
            Sign in
          </Button>
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        {!!displayedAnalysis.tax_profile?.tax_year && (
          <Chip
            icon={<CalendarIcon />}
            label={`Tax Year: ${displayedAnalysis.tax_profile.tax_year}`}
            size="small"
            color="primary"
            variant="outlined"
          />
        )}
        {sourceDisplay && (
          <Chip
            label={sourceDisplay.label}
            size="small"
            color={sourceDisplay.color}
            variant="outlined"
          />
        )}
        {confidenceSummary && (
          <Chip
            label={confidenceSummary.label}
            size="small"
            color={confidenceSummary.severity}
            variant="filled"
          />
        )}
      </Box>

      <Box data-testid="tax-section-kpis">
        <PortfolioSummaryCards
          summary={displayedAnalysis.summary}
          priceBasis={displayedAnalysis.price_basis}
          preview={{
            locked: !packetUnlocked,
            candidateCount: harvestTotals.count,
            openLossTotal: harvestTotals.totalLoss,
            potentialTaxSavings: harvestTotals.totalSavings,
            unknownLossCount: harvestTotals.unknownLossCount,
          }}
        />
      </Box>

      <Box data-testid="tax-section-1099">
        {displayedAnalysis?.supplemental_1099 && (
          <Supplemental1099InsightsPanel
            summary={displayedAnalysis.supplemental_1099}
            analysisTaxYear={displayedAnalysis.tax_profile?.tax_year ?? null}
            realizedSummary={displayedAnalysis.summary?.realized_summary ?? null}
            csvWashSaleDisallowed={csvWashSaleDisallowedTotal(
              displayedAnalysis.wash_sale_flags,
            )}
            csvWashSaleFlags={displayedAnalysis.wash_sale_flags}
            lotMatchReport={displayedAnalysis.lot_match_report ?? null}
            locked={!packetUnlocked}
          />
        )}

        {shouldPrompt1099Supplement && (
          <Alert severity="info" variant="outlined">
            <AlertTitle>{SUPPLEMENTAL_1099_AFTER_FIRST_RUN_TITLE}</AlertTitle>
            {SUPPLEMENTAL_1099_AFTER_FIRST_RUN_COPY}
          </Alert>
        )}
      </Box>

      <Box data-testid="tax-section-wash">
        {displayedAnalysis.wash_sale_flags.length > 0 && (
          <WashSaleWarning
            flags={displayedAnalysis.wash_sale_flags}
            taxYear={displayedAnalysis.tax_profile?.tax_year}
            locked={!packetUnlocked}
          />
        )}

        {washChainsVisible(displayedAnalysis, packetUnlocked) && (
          <WashSaleChainPanel chains={washSaleChains} />
        )}

        {showChainHint && (
          <Alert
            severity="info"
            variant="outlined"
            data-testid="wash-sale-chain-hint"
          >
            {washChainHint}
          </Alert>
        )}
      </Box>

      <Card
        data-testid="tax-section-harvest"
        sx={{ overflow: "hidden", maxWidth: "100%" }}
      >
        <Tabs
          value={activeTab}
          onChange={(_, v) => setActiveTab(v)}
          sx={{ borderBottom: 1, borderColor: "divider", px: 2 }}
        >
          <Tab
            label={`Harvest queue (${suggestionTabCount})`}
            id="tab-suggestions"
          />
          <Tab
            label={`Lot ledger (${displayedAnalysis.positions.length})`}
            id="tab-positions"
          />
        </Tabs>

        <CardContent>
          {activeTab === 0 && (
            <SuggestionsPanel
              suggestions={displayedAnalysis.suggestions}
              skippedSuggestionSymbols={skippedSuggestionSymbols}
              lotsWithLosses={
                displayedAnalysis.summary?.lots_with_losses ?? 0
              }
              locked={!packetUnlocked}
              positions={displayedAnalysis.positions}
              priceBasis={displayedAnalysis.price_basis}
            />
          )}
          {activeTab === 1 && (
            <PositionsTable
              positions={displayedAnalysis.positions}
              washSaleFlags={displayedAnalysis.wash_sale_flags}
              lotDetailsUnlocked={packetUnlocked}
              priceBasis={displayedAnalysis.price_basis}
            />
          )}
        </CardContent>
      </Card>

      {recommendedNextSteps.length > 0 && (
        <Alert severity="info" variant="outlined">
          <AlertTitle>Recommended next steps</AlertTitle>
          <Box component="ol" sx={{ pl: 2.5, my: 0 }}>
            {recommendedNextSteps.map((step) => (
              <Typography component="li" variant="body2" key={step}>
                {step}
              </Typography>
            ))}
          </Box>
        </Alert>
      )}

      {confidenceSummary && (
        <Alert severity={confidenceSummary.severity} variant="outlined">
          <AlertTitle>{confidenceSummary.label}</AlertTitle>
          <Typography
            variant="body2"
            sx={{ mb: confidenceSummary.details.length ? 0.5 : 0 }}
          >
            {confidenceSummary.summary}
          </Typography>
          {analysisSource === "restored-session" && (
            <Typography variant="caption" sx={{ display: "block", mb: 0.5 }}>
              This result was restored from browser storage and may not reflect
              your newest upload. Re-upload the CSV for a fully fresh run.
            </Typography>
          )}
          {analysisSource === "saved-history" && (
            <Typography variant="caption" sx={{ display: "block", mb: 0.5 }}>
              This result was loaded from saved history. Re-upload the CSV if
              you want prices from that file.
            </Typography>
          )}
          {confidenceSummary.details.length > 0 && (
            <Box component="ul" sx={{ pl: 2, my: 0 }}>
              {confidenceSummary.details.map((detail) => (
                <Typography component="li" variant="caption" key={detail}>
                  {detail}
                </Typography>
              ))}
            </Box>
          )}
        </Alert>
      )}

      <TaxDisclaimer />
    </>
  );
}

async function deleteHistoryEntry({
  userId,
  deleteTarget,
  invalidateHistory,
  clearDeleteTarget,
}: {
  userId: string | undefined;
  deleteTarget: DeleteTarget | null;
  invalidateHistory: () => void;
  clearDeleteTarget: () => void;
}): Promise<void> {
  if (!userId || !deleteTarget) {
    return;
  }

  try {
    await deleteAnalysis(deleteTarget.id);
    invalidateHistory();
  } catch (err) {
    console.error("Failed to delete analysis:", err);
  } finally {
    clearDeleteTarget();
  }
}

async function loadHistoryAnalysis({
  userId,
  itemId,
  filename,
  closeHistory,
  setHistoryLoading,
  setLoadedAnalysis,
  setAnalysisSource,
  setActiveTab,
  setSnackbar,
}: {
  userId: string | undefined;
  itemId: string;
  filename: string;
  closeHistory: () => void;
  setHistoryLoading: (loading: boolean) => void;
  setLoadedAnalysis: (analysis: PortfolioAnalysis | null) => void;
  setAnalysisSource: (source: AnalysisSource | null) => void;
  setActiveTab: (tab: number) => void;
  setSnackbar: (value: {
    message: string;
    severity: "success" | "error" | "info";
  }) => void;
}): Promise<void> {
  if (!userId) {
    return;
  }

  closeHistory();
  setHistoryLoading(true);
  try {
    const record = await fetchAnalysisById(itemId);
    if (record?.result) {
      setLoadedAnalysis(normalizeAnalysis(record.result));
      setAnalysisSource("saved-history");
      setActiveTab(0);
      setSnackbar({
        message: `Loaded saved analysis: ${filename}`,
        severity: "success",
      });
      return;
    }

    setSnackbar({
      message:
        "No detailed data stored for this analysis. Re-upload the original CSV to see full results.",
      severity: "info",
    });
  } catch (err) {
    console.error("Failed to load analysis:", err);
    setSnackbar({
      message: "Failed to load saved analysis. Please try again.",
      severity: "error",
    });
  } finally {
    setHistoryLoading(false);
  }
}

function clearCurrentAnalysisView({
  setLoadedAnalysis,
  setAnalysisSource,
}: {
  setLoadedAnalysis: (analysis: PortfolioAnalysis | null) => void;
  setAnalysisSource: (source: AnalysisSource | null) => void;
}) {
  setLoadedAnalysis(null);
  setAnalysisSource(null);
  sessionStorage.removeItem("optionstaxhub-analysis");
}

function rerunPortfolioAnalysisWithSupplement({
  csvFile,
  supplementalFile,
  clearAnalysis,
  runPortfolioAnalysis,
}: {
  csvFile: File | null;
  supplementalFile?: File | null;
  clearAnalysis: () => void;
  runPortfolioAnalysis: (csvFile: File, supplementalFile?: File | null) => void;
}) {
  if (!csvFile) {
    return;
  }

  clearAnalysis();
  runPortfolioAnalysis(csvFile, supplementalFile);
}

function handleCsvInputChange({
  event,
  clearAnalysis,
  runPortfolioAnalysis,
}: {
  event: React.ChangeEvent<HTMLInputElement>;
  clearAnalysis: () => void;
  runPortfolioAnalysis: (csvFile: File) => void;
}) {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }

  clearAnalysis();
  runPortfolioAnalysis(file);
  event.target.value = "";
}

// Helper component: render history list or empty/error state
function HistoryContent({
  history,
  historyError,
  historyLoading,
  onItemClick,
  onDeleteClick,
}: Readonly<{
  history: AnalysisHistoryItem[] | null | undefined;
  historyError: Error | null;
  historyLoading: boolean;
  onItemClick: (id: string, filename: string) => void;
  onDeleteClick: (id: string, filename: string) => void;
}>) {
  if (historyError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        <AlertTitle>Failed to Load History</AlertTitle>
        {historyError instanceof Error
          ? historyError.message
          : "Unable to load your past uploads. Please try refreshing the page."}
      </Alert>
    );
  }

  if (!history || history.length === 0) {
    return (
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ p: 2, textAlign: "center" }}
      >
        No saved runs yet. Sample analysis is not auto-saved. Sign in, then
        save a run after you drop a CSV.
      </Typography>
    );
  }

  return (
    <List dense sx={{ overflow: "auto", maxHeight: "calc(100vh - 80px)" }}>
      {history.map((item) => (
        <ListItem
          key={item.id}
          disablePadding
          secondaryAction={
            <IconButton
              edge="end"
              size="small"
              aria-label="delete"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteClick(item.id, item.filename);
              }}
              sx={{
                opacity: 0.5,
                "&:hover": { opacity: 1, color: "error.main" },
              }}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          }
        >
          <ListItemButton
            onClick={() => onItemClick(item.id, item.filename)}
            disabled={historyLoading}
            sx={{ pr: 5 }}
          >
            <ListItemText
              primary={item.filename}
              secondary={
                <>
                  {new Date(item.uploaded_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                  {" · "}
                  {item.positions_count} positions
                  {" · "}
                  {new Intl.NumberFormat("en-US", {
                    style: "currency",
                    currency: "USD",
                    maximumFractionDigits: 0,
                  }).format(item.total_market_value)}
                </>
              }
            />
          </ListItemButton>
        </ListItem>
      ))}
    </List>
  );
}

function newestHistoryItem(
  items: readonly AnalysisHistoryItem[],
): AnalysisHistoryItem | null {
  if (items.length === 0) {
    return null;
  }
  return [...items].sort((left, right) => {
    const leftTime = Date.parse(left.uploaded_at);
    const rightTime = Date.parse(right.uploaded_at);
    const leftOk = Number.isFinite(leftTime);
    const rightOk = Number.isFinite(rightTime);
    if (leftOk && rightOk && leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    if (leftOk !== rightOk) {
      return leftOk ? -1 : 1;
    }
    return right.id.localeCompare(left.id);
  })[0];
}

export default function DashboardPage() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const supplemental1099InputRef = useRef<HTMLInputElement>(null);
  const { user, loading: authLoading } = useAuth();
  const emailConfirmed = isEmailConfirmed(user);
  const confirmedUserId = emailConfirmed ? user?.id : undefined;
  const [activeTab, setActiveTab] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [lastUploadedCsv, setLastUploadedCsv] = useState<File | null>(null);
  const [supplemental1099File, setSupplemental1099File] = useState<File | null>(
    null,
  );
  const [loadedAnalysis, setLoadedAnalysis] =
    useState<PortfolioAnalysis | null>(null);
  const [analysisSource, setAnalysisSource] = useState<AnalysisSource | null>(
    null,
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [snackbar, setSnackbar] = useState<{
    message: string;
    severity: "success" | "error" | "info";
  } | null>(null);
  const [forceEmpty, setForceEmpty] = useState(false);
  const [latestRunBanner, setLatestRunBanner] = useState<{
    filename: string;
    uploadedAt: string;
    sampleRun: boolean;
  } | null>(null);
  const [heldAnalysis, setHeldAnalysis] = useState<PortfolioAnalysis | null>(
    null,
  );
  const [isSampleRun, setIsSampleRun] = useState(false);
  const [packetPaid, setPacketPaid] = useState(false);
  const [washChainHint, setWashChainHint] = useState<string | null>(null);
  const washChainFetchedFor = useRef<string | null>(null);
  const [replaceBook, setReplaceBook] = useState(false);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [sampleLoadError, setSampleLoadError] = useState<string | null>(null);
  const sampleAnalyzeStartedRef = useRef(false);
  const resumeReuploadArmed = useRef(false);
  const resumeOriginHandled = useRef(false);
  const resumeBareLoadStarted = useRef(false);
  const blockHistoryAutoload = useRef(false);
  const [resumeReuploadNotice, setResumeReuploadNotice] = useState(false);
  const [signedOutOtherTabNotice, setSignedOutOtherTabNotice] = useState(false);
  const [resumeOriginLoading, setResumeOriginLoading] = useState(false);
  const queryClient = useQueryClient();

  // Load the user's tax profile for analyze params
  const { data: taxProfile } = useTaxProfile({
    enabled: !!confirmedUserId,
    userId: confirmedUserId,
  });

  // Backend health check — shows a banner when the API server is unreachable
  const { isError: backendDown, isFetched: backendChecked } =
    useBackendHealth();

  // Load past upload history
  const {
    data: history,
    error: historyError,
    isFetched: historyFetched,
    refetch: refetchHistory,
  } = usePortfolioHistory(confirmedUserId);
  const historyRef = useRef(history);
  historyRef.current = history;

  // Full portfolio analysis mutation
  const {
    mutate: analyzePortfolio,
    isPending,
    error,
    data: analysis,
  } = useAnalyzePortfolio();

  // --- State persistence: restore analysis from sessionStorage on mount ---
  useEffect(() => {
    let wantsUpload = false;
    let wantsSample = false;
    try {
      wantsUpload = sessionStorage.getItem(UPLOAD_INTENT_KEY) === "1";
      wantsSample = sessionStorage.getItem(LOAD_SAMPLE_KEY) === "1";
      if (wantsUpload) {
        sessionStorage.removeItem(UPLOAD_INTENT_KEY);
      }
    } catch {
      wantsUpload = false;
      wantsSample = false;
    }

    if (wantsUpload || wantsSample) {
      blockHistoryAutoload.current = true;
    }
    const saved = restoreAnalysisFromStorage();
    if (wantsSample) {
      if (saved?.sample_run) {
        setLoadedAnalysis(saved);
        setAnalysisSource("fresh-upload");
        try {
          sessionStorage.removeItem(LOAD_SAMPLE_KEY);
        } catch {
          // ignore
        }
      }
      return;
    }
    if (wantsUpload) {
      setForceEmpty(true);
      if (saved) {
        setHeldAnalysis(saved);
      }
      return;
    }
    if (saved) {
      setLoadedAnalysis(saved);
      setAnalysisSource(sourceForStoredAnalysis(saved));
    }
  }, []);

  // --- State persistence: save displayedAnalysis whenever it changes ---
  const displayedAnalysis = getDisplayedAnalysis(loadedAnalysis, analysis);
  const skippedSuggestionSymbols =
    getSkippedSuggestionSymbolsForAnalysis(displayedAnalysis);

  useEffect(() => {
    if (authLoading || user) {
      return;
    }
    // A signed-out dashboard must not keep the previous account's paid flags.
    clearYearClosePacketClientState();
  }, [authLoading, user]);

  useEffect(() => {
    if (!displayedAnalysis || authLoading) {
      if (!displayedAnalysis) {
        setPacketPaid(false);
      }
      return;
    }
    if (!user) {
      setPacketPaid(false);
      return;
    }
    const analysisId = yearClosePacketStorageId(displayedAnalysis);
    if (displayedAnalysis.packet_unlocked) {
      if (displayedAnalysis.packet_session_id) {
        rememberYearClosePacketPaid(
          analysisId,
          displayedAnalysis.packet_session_id,
        );
      }
      setPacketPaid(true);
      return;
    }
    setPacketPaid(isYearClosePacketPaid(analysisId));
  }, [displayedAnalysis?.analysis_id, displayedAnalysis, authLoading, user]);

  // Unpaid analyze strips chains. After the packet is paid, load them once
  // for this analysis id. packetPaid stays a dependency so checkout can start
  // that single fetch; the ref ignores a later render of the same id.
  const washChainAnalysisId = displayedAnalysis?.analysis_id ?? "";
  const washChainIsSample = Boolean(displayedAnalysis?.sample_run);
  const washChainCount = displayedAnalysis?.wash_sale_chains?.length ?? 0;
  const displayedAnalysisRef = useRef(displayedAnalysis);
  displayedAnalysisRef.current = displayedAnalysis;
  useEffect(() => {
    if (
      !packetPaid ||
      !washChainAnalysisId ||
      washChainIsSample ||
      washChainCount > 0
    ) {
      setWashChainHint(null);
      return;
    }
    if (washChainFetchedFor.current === washChainAnalysisId) {
      return;
    }
    const requestedId = washChainAnalysisId;
    washChainFetchedFor.current = requestedId;
    let cancelled = false;
    fetchWashSaleChains(requestedId)
      .then((result) => {
        if (cancelled) return;
        const chains = result.chains;
        if (chains.length === 0) {
          setWashChainHint(result.hint);
          return;
        }
        setWashChainHint(null);
        setLoadedAnalysis((current) => {
          const base = current ?? displayedAnalysisRef.current;
          if (!base || base.analysis_id !== requestedId) return current;
          if ((base.wash_sale_chains?.length ?? 0) > 0) return current;
          return { ...base, wash_sale_chains: chains };
        });
      })
      .catch(() => {
        if (!cancelled) {
          setWashChainHint(WASH_SALE_CHAIN_REANALYZE_HINT);
        }
      });
    return () => {
      cancelled = true;
      // A cancelled request must not block the next fetch of this id.
      if (washChainFetchedFor.current === requestedId) {
        washChainFetchedFor.current = null;
      }
    };
  }, [packetPaid, washChainAnalysisId, washChainIsSample, washChainCount]);

  useEffect(() => {
    if (!displayedAnalysis || authLoading) {
      return;
    }
    if (!user) {
      const { packet_session_id: _ignored, ...rest } = displayedAnalysis;
      saveAnalysisToStorage({ ...rest, packet_unlocked: false });
      return;
    }
    saveAnalysisToStorage(displayedAnalysis);
  }, [displayedAnalysis, authLoading, user]);

  useEffect(() => {
    if (analysis && !loadedAnalysis) {
      setAnalysisSource("fresh-upload");
    }
  }, [analysis, loadedAnalysis]);

  // --- One-time cleanup: delete orphan history entries without stored result ---
  useEffect(() => {
    if (confirmedUserId) {
      cleanupOrphanHistory()
        .then(() => {
          queryClient.invalidateQueries({
            queryKey: ["portfolio-history", confirmedUserId],
          });
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [confirmedUserId]);

  const handleKeepCurrentRun = () => {
    if (heldAnalysis) {
      setLoadedAnalysis(heldAnalysis);
      setAnalysisSource("restored-session");
    }
    setForceEmpty(false);
  };

  const handleUploadNew = () => {
    blockHistoryAutoload.current = true;
    setLatestRunBanner(null);
    setHeldAnalysis(null);
    setForceEmpty(true);
    setUploadOpen(true);
    clearCurrentAnalysisView({
      setLoadedAnalysis,
      setAnalysisSource,
    });
  };

  const runPortfolioAnalysis = (
    csvFile: File,
    supplementalFile: File | null = supplemental1099File,
  ) => {
    const sample =
      csvFile.name === "sample-robinhood-transactions.csv";
    setIsSampleRun(sample);
    setPacketPaid(false);
    setSampleLoadError(null);
    setLastUploadedCsv(csvFile);
    const mergeMode =
      sample || replaceBook ? "replace" : "auto";
    analyzePortfolio(
      {
        file: csvFile,
        supplemental1099File: supplementalFile ?? undefined,
        filingStatus: taxProfile?.filing_status || "single",
        estimatedIncome: taxProfile?.estimated_annual_income || 75000,
        taxYear: taxProfile?.tax_year || 2026,
        mergeMode,
      },
      {
        onSuccess: (data) => {
          const normalized = normalizeAnalysis(data);
          rememberFreshUploadOrigin(normalized.analysis_id);
          saveAnalysisToStorage(normalized);
          setLoadedAnalysis(normalized);
          setAnalysisSource("fresh-upload");
          setLatestRunBanner(null);
          setForceEmpty(false);
          setSampleLoading(false);
          if (sample) {
            try {
              sessionStorage.removeItem(LOAD_SAMPLE_KEY);
            } catch {
              // ignore
            }
          }
          if (data.packet_unlocked && data.packet_session_id) {
            rememberYearClosePacketPaid(
              yearClosePacketStorageId(normalized),
              data.packet_session_id,
            );
            setPacketPaid(true);
          }
          const book = data.activity_book;
          if (book && book.added_from_this_upload > 0 && !book.replaced) {
            setSnackbar({
              message: `Added ${book.added_from_this_upload} new trade${
                book.added_from_this_upload === 1 ? "" : "s"
              } to your book${
                book.already_in_book
                  ? ` (${book.already_in_book} already on file)`
                  : ""
              }.`,
              severity: "success",
            });
          }
          if (!confirmedUserId) {
            try {
              sessionStorage.setItem(GUEST_UNSAVED_KEY, "1");
              sessionStorage.setItem(
                GUEST_UNSAVED_FILENAME_KEY,
                csvFile.name || "guest-run.csv",
              );
            } catch {
              // ignore
            }
          }
          queryClient.invalidateQueries({
            queryKey: ["portfolio-history", user?.id],
          });
        },
        onError: () => {
          setSampleLoading(false);
          // A failed analyze must not leave the sample handoff spinning.
          clearPacketResumeSessionMarkers();
        },
        onSettled: () => {
          setSampleLoading(false);
        },
      },
    );
  };

  const handleLoadSample = () => {
    setLatestRunBanner(null);
    const abort = new AbortController();
    void (async () => {
      try {
        setSampleLoadError(null);
        setSampleLoading(true);
        const { csvFile, form1099File } = await fetchSampleCsvAnd1099(
          abort.signal,
        );
        clearCurrentAnalysisView({
          setLoadedAnalysis,
          setAnalysisSource,
        });
        setForceEmpty(false);
        setSupplemental1099File(form1099File);
        runPortfolioAnalysis(csvFile, form1099File);
      } catch (err) {
        setSampleLoading(false);
        clearPacketResumeSessionMarkers();
        setSampleLoadError(
          err instanceof Error && err.message.trim()
            ? err.message
            : "Could not load the 2026 sample.",
        );
      }
    })();
  };

  useEffect(() => {
    if (authLoading) {
      return undefined;
    }
    if (user && !emailConfirmed) {
      return undefined;
    }
    if (sampleAnalyzeStartedRef.current) {
      return undefined;
    }

    let wantsSample = false;
    try {
      wantsSample = sessionStorage.getItem(LOAD_SAMPLE_KEY) === "1";
    } catch {
      wantsSample = false;
    }
    if (!wantsSample) {
      return undefined;
    }

    let cancelled = false;
    const abort = new AbortController();
    setSampleLoadError(null);
    setSampleLoading(true);

    void (async () => {
      try {
        const { csvFile, form1099File } = await fetchSampleCsvAnd1099(
          abort.signal,
        );
        if (cancelled || sampleAnalyzeStartedRef.current) {
          if (cancelled) {
            setSampleLoading(false);
          }
          return;
        }
        sampleAnalyzeStartedRef.current = true;
        clearCurrentAnalysisView({
          setLoadedAnalysis,
          setAnalysisSource,
        });
        setForceEmpty(false);
        setSupplemental1099File(form1099File);
        runPortfolioAnalysis(csvFile, form1099File);
      } catch (err) {
        sampleAnalyzeStartedRef.current = false;
        setSampleLoading(false);
        if (!cancelled) {
          clearPacketResumeSessionMarkers();
          setSampleLoadError(
            err instanceof Error && err.message.trim()
              ? err.message
              : "Could not load the 2026 sample.",
          );
        }
      }
    })();

    return () => {
      cancelled = true;
      abort.abort();
      setSampleLoading(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user, emailConfirmed]);

  useEffect(() => {
    if (!confirmedUserId) {
      return undefined;
    }
    let filename = "guest-run.csv";
    try {
      if (sessionStorage.getItem(GUEST_UNSAVED_KEY) !== "1") {
        return undefined;
      }
      filename =
        sessionStorage.getItem(GUEST_UNSAVED_FILENAME_KEY) || "guest-run.csv";
    } catch {
      return undefined;
    }

    const analysis = restoreAnalysisFromStorage();
    if (!analysis) {
      return undefined;
    }

    const persistKey = analysis.analysis_id || filename;
    if (!beginGuestPersist(persistKey)) {
      return undefined;
    }

    void persistGuestAnalysis(analysis, filename)
      .then((saved) => {
        endGuestPersist(persistKey);
        try {
          if (saved) {
            sessionStorage.removeItem(GUEST_UNSAVED_KEY);
            sessionStorage.removeItem(GUEST_UNSAVED_FILENAME_KEY);
          }
        } catch {
          // ignore
        }
        if (saved) {
          queryClient.invalidateQueries({
            queryKey: ["portfolio-history", confirmedUserId],
          });
        }
      })
      .catch(() => {
        endGuestPersist(persistKey);
      });
    return undefined;
  }, [confirmedUserId, queryClient]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setForceEmpty(false);
    handleCsvInputChange({
      event: e,
      clearAnalysis: () =>
        clearCurrentAnalysisView({
          setLoadedAnalysis,
          setAnalysisSource,
        }),
      runPortfolioAnalysis,
    });
  };

  const handleSupplemental1099UploadClick = () => {
    supplemental1099InputRef.current?.click();
  };

  const handleSupplemental1099Change = (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = e.target.files?.[0];
    if (file) {
      setSupplemental1099File(file);
      if (lastUploadedCsv) {
        setSnackbar({
          message: "Applying your 1099 PDF to the latest CSV analysis…",
          severity: "info",
        });
        rerunPortfolioAnalysisWithSupplement({
          csvFile: lastUploadedCsv,
          supplementalFile: file,
          clearAnalysis: () =>
            clearCurrentAnalysisView({
              setLoadedAnalysis,
              setAnalysisSource,
            }),
          runPortfolioAnalysis,
        });
      }
    }
    e.target.value = "";
  };

  const handleClearSupplemental1099 = () => {
    setSupplemental1099File(null);
    if (lastUploadedCsv && displayedAnalysis?.supplemental_1099) {
      setSnackbar({
        message:
          "Removed the 1099 supplement and refreshed the latest CSV analysis.",
        severity: "info",
      });
      rerunPortfolioAnalysisWithSupplement({
        csvFile: lastUploadedCsv,
        supplementalFile: null,
        clearAnalysis: () =>
          clearCurrentAnalysisView({
            setLoadedAnalysis,
            setAnalysisSource,
          }),
        runPortfolioAnalysis,
      });
    }
  };

  useEffect(() => {
    if (authLoading || !user) {
      return;
    }
    try {
      if (sessionStorage.getItem(OPEN_RUNS_KEY) === "1") {
        sessionStorage.removeItem(OPEN_RUNS_KEY);
        setHistoryOpen(true);
      }
    } catch {
      // ignore
    }
  }, [authLoading, user]);

  /**
   * Load a past analysis from history and display it.
   *
   * Closes the drawer immediately for responsiveness, then fetches the
   * full result from the database in the background. A Snackbar confirms
   * the load (or reports an error).
   */
  const handleHistoryItemClick = async (itemId: string, filename: string) => {
    setLatestRunBanner(null);
    await loadHistoryAnalysis({
      userId: user?.id,
      itemId,
      filename,
      closeHistory: () => setHistoryOpen(false),
      setHistoryLoading,
      setLoadedAnalysis,
      setAnalysisSource,
      setActiveTab,
      setSnackbar: (value) => setSnackbar(value),
    });
  };

  /**
   * Delete a history item after user confirms via dialog.
   */
  const handleDeleteConfirm = async () => {
    await deleteHistoryEntry({
      userId: user?.id,
      deleteTarget,
      invalidateHistory: () => {
        queryClient.invalidateQueries({
          queryKey: ["portfolio-history", user?.id],
        });
      },
      clearDeleteTarget: () => setDeleteTarget(null),
    });
  };

  useEffect(() => {
    if (authLoading) {
      return;
    }
    if (user && !emailConfirmed) {
      return;
    }
    // A signed-out second tab must not start the sample re-run. That path
    // is the ~40s spinner during sign-in. Guests still open the sample
    // through LOAD_SAMPLE_KEY.
    if (!user) {
      return;
    }
    if (sampleAnalyzeStartedRef.current) {
      return;
    }
    let resumeSample = false;
    let wantsResume = false;
    let stored: PortfolioAnalysis | null = null;
    try {
      if (sessionStorage.getItem(LOAD_SAMPLE_KEY) === "1") {
        return;
      }
      resumeSample = sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY) === "1";
      const params = new URLSearchParams(window.location.search);
      wantsResume =
        params.get(RESUME_CHECKOUT_PARAM) === "1" ||
        sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
      stored = restoreAnalysisFromStorage();
    } catch {
      return;
    }
    if (!resumeSample || !wantsResume) {
      return;
    }
    // A re-run gets a new id. The same tax year and sample_run still match,
    // so an already-matching sample must not be replaced.
    if (stored && resumeAnalysisMatches(stored)) {
      return;
    }
    if (!consumeAutomaticSampleRerun()) {
      return;
    }
    handleLoadSample();
    // The sample loader is stable for this mount; auth gates the start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user, emailConfirmed]);

  useEffect(() => {
    if (authLoading || user) {
      return;
    }
    if (displayedAnalysis && !forceEmpty) {
      return;
    }
    let checkoutMarker = false;
    try {
      checkoutMarker = sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
    } catch {
      return;
    }
    if (!checkoutMarker) {
      return;
    }
    const timer = setTimeout(() => {
      const otherTab = packetCheckoutStartedRecently();
      clearPacketResumeSessionMarkers();
      clearPacketResumeLocal();
      setSampleLoading(false);
      setResumeOriginLoading(false);
      if (otherTab) {
        setSignedOutOtherTabNotice(true);
      } else {
        setResumeReuploadNotice(true);
      }
    }, SIGNED_OUT_RESUME_TIMEOUT_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [authLoading, user, displayedAnalysis, forceEmpty]);

  useEffect(() => {
    if (resumeOriginHandled.current) {
      return;
    }
    if (authLoading || !confirmedUserId) {
      return;
    }
    if (!historyFetched && !historyError) {
      return;
    }
    if (resumeReuploadArmed.current) {
      return;
    }
    let sampleMarker = false;
    let checkoutMarker = false;
    let wantsResume = false;
    try {
      sampleMarker = sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY) === "1";
      checkoutMarker =
        sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
      const params = new URLSearchParams(window.location.search);
      wantsResume =
        params.get(RESUME_CHECKOUT_PARAM) === "1" || checkoutMarker;
    } catch {
      return;
    }
    if (!wantsResume || sampleMarker || !checkoutMarker) {
      return;
    }
    const expected = readPacketResumeExpected();
    if (!expected) {
      return;
    }
    const stored = restoreAnalysisFromStorage();
    if (
      stored?.analysis_id === expected.analysisId &&
      stored.tax_profile?.tax_year === expected.taxYear
    ) {
      return;
    }

    // The original tab may still be saving this guest run. Keep the resume
    // markers and leave resumeOriginHandled unset until the id shows up.
    // A history error never starts that wait: give up immediately.
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();

    const giveUp = () => {
      if (cancelled || resumeOriginHandled.current) {
        return;
      }
      // Newest-run autoload depends on the mounted analysis. Clearing a
      // mismatched run would otherwise load a different tax year with Pay on.
      // Stay blocked until the user uploads, loads the sample, or picks a run.
      blockHistoryAutoload.current = true;
      // Read the expected id before the markers are cleared. A different run
      // must leave the screen, or Pay turns back on for the wrong tax year.
      // Storage counts too: the restore effect may have queued the wrong run
      // in this same commit, before the ref updates.
      const awaited = readPacketResumeExpected();
      const mounted =
        displayedAnalysisRef.current ?? restoreAnalysisFromStorage();
      const mountedMismatches =
        !!awaited &&
        !!mounted &&
        (mounted.analysis_id !== awaited.analysisId ||
          mounted.tax_profile?.tax_year !== awaited.taxYear);
      resumeOriginHandled.current = true;
      setResumeOriginLoading(false);
      if (mountedMismatches) {
        clearCurrentAnalysisView({
          setLoadedAnalysis,
          setAnalysisSource,
        });
        setForceEmpty(false);
      }
      clearPacketResumeSessionMarkers();
      setResumeReuploadNotice(true);
    };

    if (historyError) {
      giveUp();
      return;
    }
    setResumeOriginLoading(true);

    const schedule = () => {
      if (cancelled || resumeOriginHandled.current) {
        return;
      }
      const elapsed = Date.now() - startedAt;
      if (elapsed >= ORIGIN_RESUME_WAIT_MS) {
        giveUp();
        return;
      }
      timer = setTimeout(() => {
        void attempt();
      }, Math.min(ORIGIN_RESUME_RETRY_MS, ORIGIN_RESUME_WAIT_MS - elapsed));
    };

    const attempt = async () => {
      if (cancelled || resumeOriginHandled.current) {
        return;
      }
      let items = historyRef.current ?? [];
      try {
        const refreshed = await refetchHistory();
        if (cancelled || resumeOriginHandled.current) {
          return;
        }
        if (Array.isArray(refreshed?.data)) {
          items = refreshed.data;
        }
      } catch {
        if (cancelled || resumeOriginHandled.current) {
          return;
        }
      }
      const row = items.find((item) => item.id === expected.analysisId);
      if (row) {
        try {
          const record = await fetchAnalysisById(expected.analysisId);
          if (cancelled || resumeOriginHandled.current) {
            return;
          }
          if (record?.result) {
            resumeOriginHandled.current = true;
            setResumeOriginLoading(false);
            clearPacketResumeSessionMarkers();
            setLoadedAnalysis(normalizeAnalysis(record.result));
            setAnalysisSource("saved-history");
            setForceEmpty(false);
            return;
          }
        } catch {
          if (cancelled || resumeOriginHandled.current) {
            return;
          }
        }
      }
      schedule();
    };

    void attempt();
    return () => {
      cancelled = true;
      setResumeOriginLoading(false);
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    };
  }, [
    authLoading,
    confirmedUserId,
    historyFetched,
    historyError,
    refetchHistory,
  ]);

  useEffect(() => {
    if (resumeBareLoadStarted.current || resumeOriginHandled.current) {
      return;
    }
    if (authLoading || !confirmedUserId || !historyFetched || historyError) {
      return;
    }
    if (resumeReuploadArmed.current) {
      return;
    }
    let hasAnalysis = false;
    let hasCheckout = false;
    let hasSample = false;
    let wantsUrl = false;
    try {
      hasAnalysis = Boolean(sessionStorage.getItem("optionstaxhub-analysis"));
      hasCheckout =
        sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
      hasSample = sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY) === "1";
      wantsUrl =
        new URLSearchParams(window.location.search).get(
          RESUME_CHECKOUT_PARAM,
        ) === "1";
    } catch {
      return;
    }
    if (!wantsUrl || hasAnalysis || hasCheckout || hasSample) {
      return;
    }
    resumeBareLoadStarted.current = true;
    const newest = newestHistoryItem(history ?? []);
    if (!newest) {
      handleLoadSample();
      return;
    }
    void fetchAnalysisById(newest.id)
      .then((record) => {
        if (record?.result) {
          setLoadedAnalysis(normalizeAnalysis(record.result));
          setAnalysisSource("saved-history");
          setForceEmpty(false);
          return;
        }
        handleLoadSample();
      })
      .catch(() => {
        handleLoadSample();
      });
    // handleLoadSample is stable for this mount. A bare resume never sets
    // resume markers, so the packet panel cannot auto-post.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    authLoading,
    confirmedUserId,
    historyFetched,
    history,
    historyError,
  ]);

  // A signed-in dashboard with saved runs and nothing on screen should open
  // the newest run. A failed fetch stays on the upload card and does not
  // start the sample. Resume-owned flows keep their own loaders.
  useEffect(() => {
    if (authLoading || !confirmedUserId || !historyFetched || historyError) {
      return;
    }
    if (
      forceEmpty ||
      blockHistoryAutoload.current ||
      resumeReuploadArmed.current ||
      resumeReuploadNotice
    ) {
      return;
    }
    if (loadedAnalysis || analysis) {
      return;
    }
    let blocked = false;
    try {
      blocked =
        Boolean(sessionStorage.getItem("optionstaxhub-analysis")) ||
        sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1" ||
        sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY) === "1" ||
        sessionStorage.getItem(UPLOAD_INTENT_KEY) === "1" ||
        sessionStorage.getItem(LOAD_SAMPLE_KEY) === "1" ||
        new URLSearchParams(window.location.search).get(RESUME_CHECKOUT_PARAM) ===
          "1";
    } catch {
      return;
    }
    if (blocked) {
      return;
    }
    const newest = newestHistoryItem(history ?? []);
    if (!newest) {
      return;
    }
    let cancelled = false;
    void Promise.resolve()
      .then(() => fetchAnalysisById(newest.id))
      .then((record) => {
        if (cancelled || !record?.result) {
          return;
        }
        try {
          if (
            sessionStorage.getItem("optionstaxhub-analysis") ||
            sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1" ||
            new URLSearchParams(window.location.search).get(
              RESUME_CHECKOUT_PARAM,
            ) === "1"
          ) {
            return;
          }
        } catch {
          return;
        }
        if (displayedAnalysisRef.current) {
          return;
        }
        const normalized = normalizeAnalysis(record.result);
        setLoadedAnalysis((current) => (current ? current : normalized));
        setAnalysisSource((current) => current ?? "saved-history");
        setForceEmpty(false);
        setLatestRunBanner({
          filename: newest.filename,
          uploadedAt: newest.uploaded_at,
          sampleRun: Boolean(normalized.sample_run),
        });
      })
      .catch(() => {
        // Leave the upload card. Do not fall back to the sample.
      });
    return () => {
      cancelled = true;
    };
  }, [
    authLoading,
    confirmedUserId,
    historyFetched,
    historyError,
    history,
    forceEmpty,
    loadedAnalysis,
    analysis,
    resumeReuploadNotice,
  ]);

  const handleSampleRerunForCheckout = (): boolean => {
    if (!consumeAutomaticSampleRerun()) {
      return false;
    }
    rearmResumeCheckout();
    handleLoadSample();
    return true;
  };

  useEffect(() => {
    if (authLoading) {
      return;
    }
    if (user && !emailConfirmed) {
      const next = safeNextPath(
        `${window.location.pathname}${window.location.search}`,
      );
      if (next === DASHBOARD_RESUME_CHECKOUT_PATH) {
        router.push(`/auth/confirm-email?next=${nextParam(next)}`);
        return;
      }
      router.push("/auth/confirm-email");
    }
  }, [authLoading, user, emailConfirmed, router]);

  useEffect(() => {
    if (resumeReuploadArmed.current) {
      setResumeReuploadNotice(true);
    }
  }, []);

  // Cross-tab handoff must be in sessionStorage before the effects above read it.
  if (consumePacketResumeLocalHandoff()) {
    resumeReuploadArmed.current = true;
  }

  if (authLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (user && !emailConfirmed) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  // displayedAnalysis is computed above (near sessionStorage effects)
  const analyzing = isPending || sampleLoading;
  const hasResults = !!displayedAnalysis && !forceEmpty;
  const showLatestRunBar = Boolean(
    hasResults && analysisSource === "saved-history" && latestRunBanner,
  );
  let resumeHandoffPending = false;
  if (!hasResults && !resumeReuploadNotice) {
    try {
      const checkoutMarker =
        sessionStorage.getItem(PACKET_RESUME_CHECKOUT_KEY) === "1";
      const sampleMarker =
        sessionStorage.getItem(PACKET_RESUME_SAMPLE_KEY) === "1";
      const expected = readPacketResumeExpected();
      // The center spinner is only for a loader that is actually running.
      // A history error takes the give-up path instead of spinning forever.
      const uploadedResumeLoading =
        Boolean(confirmedUserId) &&
        checkoutMarker &&
        !sampleMarker &&
        expected != null &&
        !historyError &&
        (resumeOriginLoading || !historyFetched);
      // Sample spinner only while a sample loader is actually running.
      // Marker flags alone never end if the CSV fetch or analyze fails.
      const sampleResumeLoading =
        checkoutMarker && sampleMarker && (sampleLoading || isPending);
      const hasAnalysisOnScreen = !!displayedAnalysis && !forceEmpty;
      const signedOutResumeSpinning =
        !user &&
        checkoutMarker &&
        !hasAnalysisOnScreen &&
        !resumeReuploadNotice &&
        !signedOutOtherTabNotice;
      resumeHandoffPending =
        sampleResumeLoading || uploadedResumeLoading || signedOutResumeSpinning;
    } catch {
      resumeHandoffPending = false;
    }
  }
  const confidenceSummary = displayedAnalysis
    ? getConfidenceSummary(displayedAnalysis)
    : null;
  const packetUnlocked =
    isSampleRun || packetPaid || Boolean(displayedAnalysis?.sample_run);
  const tradeBook = savedTradeBook(history);
  const recommendedNextSteps = displayedAnalysis
    ? getRecommendedNextSteps(displayedAnalysis, packetUnlocked)
    : [];
  const shouldPrompt1099Supplement = shouldRecommendSupplemental1099(
    displayedAnalysis,
    supplemental1099File,
  );
  const supplemental1099Warnings = getSupplemental1099Warnings(
    displayedAnalysis?.warnings,
  );
  const sourceDisplay = analysisSource
    ? getAnalysisSourceDisplay(analysisSource)
    : null;

  // Pre-compute error message using if/else to avoid nested ternary (SonarQube S3358)
  const analysisErrorMessage = error
    ? getAnalysisErrorMessage(error)
    : sampleLoadError;

  const uploadSection = (
          <Accordion
            id="tax-upload"
            data-testid="tax-upload"
            expanded={!hasResults || uploadOpen}
            onChange={(_, expanded) => {
              if (hasResults) {
                setUploadOpen(expanded);
              }
            }}
            disableGutters
            elevation={0}
            sx={{
              bgcolor: "transparent",
              "&:before": { display: "none" },
            }}
          >
            {hasResults && (
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography sx={{ fontWeight: 600 }}>
                  Upload CSV or 1099
                </Typography>
              </AccordionSummary>
            )}
            <AccordionDetails sx={{ px: 0, py: hasResults ? 1 : 0 }}>
          <Card>
            <CardContent>
              <Stack spacing={2}>
                <Box>
                  <Typography variant="h5" sx={{ mb: 0.5, fontWeight: 600 }}>
                    Portfolio Analysis
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {uploadIntroCopy({
                      tradeBook,
                      replaceBook,
                      signedIn: Boolean(confirmedUserId),
                    })}{" "}
                    Your{" "}
                    <MuiLink
                      component={NextLink}
                      href={
                        user
                          ? "/settings"
                          : signInHref("/dashboard", "profile")
                      }
                      data-testid="tax-settings-link"
                      sx={{ fontWeight: 600 }}
                    >
                      Settings
                    </MuiLink>{" "}
                    is used to calculate savings.
                  </Typography>
                </Box>

                {/* Upload area — native label, not programmatic input.click() */}
                <input
                  id="desk-csv-input"
                  ref={fileInputRef}
                  className="file-input-hidden"
                  type="file"
                  accept=".csv"
                  tabIndex={-1}
                  onChange={handleFileChange}
                />
                <Box
                  component="label"
                  htmlFor={analyzing ? undefined : "desk-csv-input"}
                  data-testid="csv-dropzone"
                  role="button"
                  tabIndex={analyzing ? -1 : 0}
                  aria-label="Upload CSV"
                  onKeyDown={(event) => {
                    if (analyzing) {
                      return;
                    }
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      fileInputRef.current?.click();
                    }
                  }}
                  sx={{
                    border: "1px dashed",
                    borderColor: analyzing ? "divider" : "primary.main",
                    borderRadius: 2,
                    p: 3,
                    textAlign: "center",
                    cursor: analyzing ? "default" : "pointer",
                    display: "block",
                    transition: "all 0.2s",
                    opacity: analyzing ? 0.6 : 1,
                    "& *": {
                      cursor: "inherit",
                    },
                    "&:hover": analyzing
                      ? {}
                      : {
                          backgroundColor: "action.hover",
                          borderColor: "primary.light",
                        },
                    "&:focus-visible": {
                      outline: "2px solid",
                      outlineColor: "primary.main",
                      outlineOffset: 2,
                    },
                  }}
                >
                  <CloudUploadIcon
                    sx={{ fontSize: 40, color: "primary.main", mb: 0.5 }}
                  />
                  <Typography variant="body1" sx={{ fontWeight: 500 }}>
                    {analyzing
                      ? "Analyzing portfolio..."
                      : "Click to upload CSV"}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {tradeBook && !replaceBook
                      ? `${tradeBook.count} saved trades — drop a file that starts on or before ${tradeBook.last || "the last date"}`
                      : "Robinhood transaction export or simplified portfolio CSV"}
                  </Typography>
                </Box>

                {Boolean(tradeBook && confirmedUserId) && (
                  <FormControlLabel
                    control={
                      <Checkbox
                        checked={replaceBook}
                        onChange={(event) => setReplaceBook(event.target.checked)}
                        size="small"
                      />
                    }
                    label="Start a new book instead (full export, ignore saved trades)"
                  />
                )}

                <input
                  id="desk-1099-input"
                  ref={supplemental1099InputRef}
                  className="file-input-hidden"
                  type="file"
                  accept=".pdf,application/pdf"
                  onChange={handleSupplemental1099Change}
                />
                <Supplemental1099UploadPanel
                  selectedFileName={supplemental1099File?.name ?? null}
                  appliedSummary={displayedAnalysis?.supplemental_1099}
                  analysisTaxYear={displayedAnalysis?.tax_profile?.tax_year}
                  analysisSource={analysisSource}
                  hasUploadedCsv={Boolean(lastUploadedCsv)}
                  canRemoveSupplement={
                    analysisSource !== "saved-history" &&
                    analysisSource !== "restored-session" &&
                    Boolean(
                      supplemental1099File ||
                        (lastUploadedCsv && displayedAnalysis?.supplemental_1099),
                    )
                  }
                  onChooseFile={handleSupplemental1099UploadClick}
                  onClearFile={handleClearSupplemental1099}
                  isPending={analyzing}
                />
                {supplemental1099Warnings.length > 0 && (
                  <Alert severity="warning">
                    <AlertTitle>1099 warning</AlertTitle>
                    {supplemental1099Warnings.map((warning) => (
                      <Typography key={warning} variant="body2">
                        {warning}
                      </Typography>
                    ))}
                    {!displayedAnalysis?.supplemental_1099 && (
                      <Typography variant="body2">
                        CSV analysis still completed. The 1099 was not used to
                        rebuild lots.
                      </Typography>
                    )}
                  </Alert>
                )}
              </Stack>
            </CardContent>
          </Card>
            </AccordionDetails>
          </Accordion>
  );

  return (
    <AppShell
      onUpload={() => {
        setUploadOpen(true);
        window.requestAnimationFrame(() => {
          document.getElementById("tax-upload")?.scrollIntoView?.({ block: "start" });
        });
      }}
      onSavedRuns={() => setHistoryOpen(true)}
    >
      <ServiceWorkerRegistration />

      {/* History Drawer */}
      <Drawer
        anchor="left"
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        <Box sx={{ width: 300, pt: 2 }}>
          <Typography
            variant="h6"
            component="h2"
            sx={{
              px: 2,
              pb: 1,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              gap: 1,
            }}
          >
            <HistoryIcon /> Saved runs
          </Typography>
          <Divider />
          <HistoryContent
            history={history}
            historyError={historyError || null}
            historyLoading={historyLoading}
            onItemClick={handleHistoryItemClick}
            onDeleteClick={(id, filename) => setDeleteTarget({ id, filename })}
          />
        </Box>
      </Drawer>

      {/* Delete Confirmation Dialog */}
      <Dialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>Delete Analysis</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete the analysis for{" "}
            <strong>{deleteTarget?.filename}</strong>? This action cannot be
            undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button
            onClick={handleDeleteConfirm}
            color="error"
            variant="contained"
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Loading bar — shows for new analysis (isPending) and history fetch (historyLoading) */}
      {analyzing && <LinearProgress />}
      {historyLoading && <LinearProgress color="secondary" />}

      {/* Snackbar for history load feedback */}
      <Snackbar
        open={!!snackbar}
        autoHideDuration={4000}
        onClose={() => setSnackbar(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity={snackbar?.severity ?? "info"}
          onClose={() => setSnackbar(null)}
          variant="filled"
          sx={{ width: "100%" }}
        >
          {snackbar?.message}
        </Alert>
      </Snackbar>

      {/* Main Content */}
      <Container maxWidth="lg" sx={{ py: 3 }}>
        <Stack spacing={3}>
          {showLatestRunBar && latestRunBanner && (
            <LatestRunBar
              filename={latestRunBanner.filename}
              uploadedAt={latestRunBanner.uploadedAt}
              sampleRun={latestRunBanner.sampleRun}
              onUploadNew={handleUploadNew}
              onAllSavedRuns={() => setHistoryOpen(true)}
            />
          )}
          {!hasResults && !resumeHandoffPending && uploadSection}
          {!hasResults && resumeHandoffPending && !analyzing && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
              <CircularProgress />
            </Box>
          )}

          {/* Backend health banner — shown when the API server is unreachable */}
          {backendChecked && backendDown && (
            <Alert severity="warning">
              <AlertTitle>Analysis service unreachable</AlertTitle>
              {getBackendUnreachableMessage()}
            </Alert>
          )}

          {resumeReuploadNotice && (
            <Alert severity="info" data-testid="packet-resume-reupload">
              {PACKET_RESUME_REUPLOAD_COPY}
            </Alert>
          )}

          {signedOutOtherTabNotice && (
            <Alert severity="info" data-testid="packet-checkout-other-tab">
              {PACKET_CHECKOUT_OTHER_TAB_COPY}
            </Alert>
          )}

          {/* Error Alert */}
          {(error || sampleLoadError) && analysisErrorMessage && (
            <Alert severity="error" icon={<ErrorIcon />}>
              <AlertTitle>Analysis Failed</AlertTitle>{" "}
              <Typography variant="caption">{analysisErrorMessage}</Typography>
            </Alert>
          )}

          {forceEmpty && heldAnalysis && !analyzing && (
            <Alert
              severity="info"
              variant="outlined"
              action={
                <Button
                  color="inherit"
                  size="small"
                  onClick={handleKeepCurrentRun}
                >
                  Keep current run
                </Button>
              }
            >
              <AlertTitle>Drop a new file</AlertTitle>
              A previous analysis is still on this device. Upload a CSV to
              replace it, or keep the current run.
            </Alert>
          )}

          {/* First-run guidance only when nothing is held on this device */}
          {!hasResults &&
            !analyzing &&
            !resumeHandoffPending &&
            !error &&
            !sampleLoadError &&
            !(forceEmpty && heldAnalysis) && (
              <FirstRunEmptyState
                onLoadSample={handleLoadSample}
                settingsHref={
                  user ? "/settings" : signInHref("/dashboard", "profile")
                }
              />
            )}

          {/* Results — shown ABOVE warnings so actionable info is immediately visible */}
          {hasResults && (
            <ResultsSection
              displayedAnalysis={displayedAnalysis}
              analysisSource={analysisSource}
              sourceDisplay={sourceDisplay}
              confidenceSummary={confidenceSummary}
              recommendedNextSteps={recommendedNextSteps}
              activeTab={activeTab}
              setActiveTab={setActiveTab}
              skippedSuggestionSymbols={skippedSuggestionSymbols}
              shouldPrompt1099Supplement={shouldPrompt1099Supplement}
              guest={!user}
              packetUnlocked={packetUnlocked}
              washChainHint={washChainHint}
            />
          )}

          {/* Data quality notes — collapsible accordion, shown below results */}
          {displayedAnalysis &&
            warningsForDisplay(displayedAnalysis).length > 0 && (
              <Accordion
                elevation={0}
                sx={{
                  border: "1px solid",
                  borderColor: "warning.light",
                  borderRadius: 1,
                  "&:before": { display: "none" },
                }}
              >
                <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                  <Typography
                    variant="body2"
                    color="warning.dark"
                    sx={{ fontWeight: 500 }}
                  >
                    ⚠ {warningsForDisplay(displayedAnalysis).length} data quality note
                    {warningsForDisplay(displayedAnalysis).length === 1 ? "" : "s"} — click
                    to expand
                  </Typography>
                </AccordionSummary>
                <AccordionDetails sx={{ pt: 0 }}>
                  <Alert
                    severity="warning"
                    variant="outlined"
                    sx={{ border: 0 }}
                  >
                    {Array.from(
                      new Set<string>(warningsForDisplay(displayedAnalysis)),
                    ).map((w: string) => (
                      <Typography
                        key={w}
                        variant="caption"
                        sx={{ display: "block", mb: 0.25 }}
                      >
                        {w}
                      </Typography>
                    ))}
                  </Alert>
                </AccordionDetails>
              </Accordion>
            )}
          {hasResults && uploadSection}
          {hasResults && displayedAnalysis && (
            <Box data-testid="tax-section-packet">
              <YearClosePacketPanel
                analysis={
                  user
                    ? displayedAnalysis
                    : {
                        ...displayedAnalysis,
                        packet_unlocked: false,
                        packet_session_id: undefined,
                      }
                }
                guest={!user}
                restoredRun={analysisSource === "restored-session"}
                freshUpload={analysisSource === "fresh-upload"}
                onPaidChange={setPacketPaid}
                onSampleRerunForCheckout={handleSampleRerunForCheckout}
              />
            </Box>
          )}
        </Stack>
      </Container>
    </AppShell>
  );
}
