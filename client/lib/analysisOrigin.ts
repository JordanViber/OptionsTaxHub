import type { PortfolioAnalysis } from "@/lib/types";

let freshUploadAnalysisId: string | null = null;

/** Tests only. A full reload already starts with no fresh-upload id. */
export function resetAnalysisOriginForTests(): void {
  freshUploadAnalysisId = null;
}

export function rememberFreshUploadOrigin(analysisId: string | undefined): void {
  freshUploadAnalysisId = analysisId || null;
}

export function sourceForStoredAnalysis(
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
