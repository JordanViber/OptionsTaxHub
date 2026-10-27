import type { PortfolioAnalysis } from "@/lib/types";

/** 2026 Season Unlock. Off unless NEXT_PUBLIC_SEASON_UNLOCK is 1, true, or yes. */
export const SEASON_UNLOCK_YEAR = 2026;
export const SEASON_UNLOCK_PAY_LABEL = "Unlock 2026";
export const SEASON_UNLOCK_PRICE_COPY = "$49 one-time, 2026 tax season";
export const SEASON_NOTHING_TO_REPORT = "Nothing to report for 2026 yet.";

export function seasonUnlockEnabled(): boolean {
  const raw = (process.env.NEXT_PUBLIC_SEASON_UNLOCK || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes";
}

export function seasonYearHintLine(year: number): string {
  return `This run points to ${year}.`;
}

/** A year other than 2026 named by the totals, the profile, or the last activity date. */
export function seasonYearHint(analysis: PortfolioAnalysis): number | null {
  const realizedYear = analysis.summary?.realized_summary?.tax_year;
  if (typeof realizedYear === "number" && realizedYear !== SEASON_UNLOCK_YEAR) {
    return realizedYear;
  }
  const profileYear = analysis.tax_profile?.tax_year;
  if (typeof profileYear === "number" && profileYear !== SEASON_UNLOCK_YEAR) {
    return profileYear;
  }
  const activity = analysis.summary?.activity_last_date;
  if (typeof activity === "string" && activity.length >= 4) {
    const year = Number(activity.slice(0, 4));
    if (Number.isInteger(year) && year >= 2000 && year !== SEASON_UNLOCK_YEAR) {
      return year;
    }
  }
  return null;
}

export function realized2026SaleCount(analysis: PortfolioAnalysis): number {
  const realized = analysis.summary?.realized_summary;
  if (!realized || realized.tax_year !== SEASON_UNLOCK_YEAR) {
    return 0;
  }
  return realized.transactions_count > 0 ? realized.transactions_count : 0;
}

export type SeasonPayGate = {
  hidePay: boolean;
  disabled: boolean;
  lines: string[];
};

/** Null keeps the current Pay $49 button. */
export function seasonPayGate(analysis: PortfolioAnalysis): SeasonPayGate | null {
  if (!seasonUnlockEnabled()) {
    return null;
  }
  if (analysis.sample_run) {
    return { hidePay: true, disabled: true, lines: [] };
  }
  if (realized2026SaleCount(analysis) > 0) {
    return { hidePay: false, disabled: false, lines: [] };
  }
  const lines = [SEASON_NOTHING_TO_REPORT];
  const hint = seasonYearHint(analysis);
  if (hint != null) {
    lines.push(seasonYearHintLine(hint));
  }
  return { hidePay: false, disabled: true, lines };
}
