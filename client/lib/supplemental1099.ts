import type {
  PortfolioAnalysis,
  RealizedSummary,
  Supplemental1099Summary,
} from "@/lib/types";

export const SUPPLEMENTAL_1099_UPLOAD_TITLE =
  "Robinhood 1099 for the tax year you are closing";

export const PACKET_SCOPE_COPY =
  "The PDF packages the reconciliation, wash chains, and harvest notes into a downloadable year-close document, plus updates for that tax year.";

export const SUPPLEMENTAL_1099_CONTEXT_COPY =
  "Broker 1099 for the tax year you are closing — totals compare is free. " +
  PACKET_SCOPE_COPY;

export const SUPPLEMENTAL_1099_APPLIED_TITLE =
  "Previous-year 1099 supplement applied";

export const SUPPLEMENTAL_1099_APPLIED_COPY =
  "This 1099 is a different tax year than the dashboard year, so it is shown as a previous-year supplement — not a same-year 1099 vs export compare.";

export const SUPPLEMENTAL_1099_UNKNOWN_YEAR_TITLE = "1099 tax year unknown";

export const SUPPLEMENTAL_1099_UNKNOWN_YEAR_COPY =
  "This 1099's tax year could not be determined. That is not a year mismatch, and it is not a same-year 1099 vs export compare. Totals are shown as a supplement only.";

export const SUPPLEMENTAL_1099_UNKNOWN_YEAR_HELPER =
  "Included as a 1099 supplement — tax year could not be determined, so this is not a same-year compare and not a previous-year mismatch.";

export const SUPPLEMENTAL_1099_UNKNOWN_YEAR_RESTORED_HELPER =
  "This restored result already includes a broker 1099 whose tax year could not be determined — reconciliation context, not lot history. Upload the PDF again only if you want to refresh it.";

export const SCHEDULE_D_RECONCILE_COPY =
  "OptionsTaxHub helps reconcile a Robinhood whole-portfolio 1099-B and wash-sale gaps toward Schedule D. Path is your CSV export plus the 1099 — not a Form 8949 rebuild, and not tax advice.";

export const WASH_ACCOUNT_SCOPE_COPY =
  "Wash flags on a 1099 are often scoped to that one account, so a second account or a year-end clear around 12/31 can also explain why the export and the 1099 disagree.";

export const SUPPLEMENTAL_1099_COMPARE_TITLE = "1099 vs your export";

export const SUPPLEMENTAL_1099_COMPARE_COPY =
  "Two columns, totals only. Broker 1099 uses settlement date; this export uses trade date. Short-term and long-term nets include wash-sale disallowed (1099 definition); wash is also shown separately. The $49 PDF is separate from this free compare.";

export const SUPPLEMENTAL_1099_BROKER_COLUMN = "Broker 1099 (settlement date)";

export const SUPPLEMENTAL_1099_EXPORT_COLUMN = "This export (trade date)";

/** Sample figures. Landing preview only — do not reuse on a real user's compare or PDF. */
export const ST_TOTALS_EXPLANATION =
  "Short-term on the 1099 is +$2,699. This export's short-term result, before the disallowed wash sale is counted, is −$924. Both sides disallow $924, so the export column can read $0 once that disallowed amount is counted. That is not a software bug.";

/** Dashboard disclosure only. No sample dollar figures. The 12/31 year-end note lives here once. */
export const ST_GAP_EXPLANATION =
  "The 1099 uses settlement date and the export uses trade date. A year-end short option such as SPX expiring 12/31 can settle in January. That is not a software bug.";

/** Landing wash-sale radar states this in full. Other screens use a tooltip. */
export const WASH_WINDOW_EXPLANATION =
  "The rule covers the same or a substantially identical security, including options or contracts to acquire it, bought within 30 days before or after the sale (a 61-day window). The disallowed loss is added to the replacement's basis. This app flags same-symbol matches.";

export const SUPPLEMENTAL_1099_GAP_COPY =
  "These totals often disagree. " +
  WASH_ACCOUNT_SCOPE_COPY +
  " " +
  PACKET_SCOPE_COPY +
  " An incomplete export also shows up here.";

export const SUPPLEMENTAL_1099_SETTLEMENT_FAQ =
  "Robinhood 1099 uses settlement date. Matched lots still show both dates so the split is visible.";

export const LOT_MATCHED_1099B_TITLE = "Lot-matched 1099-B";

export const LOT_MATCHED_1099B_LOCKED_COPY =
  "Pay $49 for lot-matched 1099-B — matched, gap, and unmatched lots on the year-close PDF.";

export const LOT_MATCHED_1099B_UNLOCKED_COPY =
  "Matched: 1099 sold date equals export trade date. Gap: paired lots with a settlement vs trade-date split (matched_settlement_gap). Unmatched: 1099_only and csv_only. Not a filed Form 8949.";

export const SUPPLEMENTAL_1099_WASH_SALE_FAQ =
  "Options and credit-spread wash-sale treatment can differ from the broker 1099. We show the 1099 wash-sale disallowed figure as reported.";

export const SUPPLEMENTAL_1099_AFTER_FIRST_RUN_TITLE =
  "Optional: 1099 for the tax year you are closing";

export const SUPPLEMENTAL_1099_AFTER_FIRST_RUN_COPY =
  "Upload your Robinhood 1099 PDF for the tax year you are closing. Totals compare stays free. " +
  PACKET_SCOPE_COPY;

export const SUPPLEMENTAL_1099_FIRST_RUN_HINT =
  "You can also attach the Robinhood 1099 PDF for the tax year you are closing next to the CSV. Totals compare stays free. " +
  PACKET_SCOPE_COPY;

const IN_APP_SAMPLE_1099 = "sample-robinhood-1099-2026.pdf";

export function isInAppSampleAnalysis(
  analysis: { supplemental_1099?: { source_filename?: string } | null } | null | undefined,
): boolean {
  const name = (analysis?.supplemental_1099?.source_filename || "").toLowerCase();
  return name.endsWith(IN_APP_SAMPLE_1099);
}

export function redactUnpaidLotMatch(analysis: PortfolioAnalysis): PortfolioAnalysis {
  if (
    analysis.packet_unlocked ||
    analysis.sample_run ||
    isInAppSampleAnalysis(analysis)
  ) {
    return analysis;
  }
  const report = analysis.lot_match_report;
  const supplemental = analysis.supplemental_1099;
  const redacted: PortfolioAnalysis = {
    ...analysis,
    lot_match_report: report
      ? { ...report, matched: [], gap: [], unmatched: [] }
      : report,
    supplemental_1099: supplemental
      ? { ...supplemental, lots: [] }
      : supplemental,
  };
  delete redacted.wash_sale_chains;
  return redacted;
}

export function formatUsd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function combinedWashSaleDisallowed(
  summary: Pick<
    Supplemental1099Summary,
    "short_term_wash_sale_disallowed" | "long_term_wash_sale_disallowed"
  >,
): number {
  return (
    summary.short_term_wash_sale_disallowed +
    summary.long_term_wash_sale_disallowed
  );
}

export function isSupplemental1099Warning(warning: string): boolean {
  return /1099/i.test(warning);
}

export function supplemental1099Warnings(warnings: string[] | undefined): string[] {
  return (warnings ?? []).filter(isSupplemental1099Warning);
}

export function dataQualityWarnings(warnings: string[] | undefined): string[] {
  return (warnings ?? []).filter((warning) => !isSupplemental1099Warning(warning));
}

export function isUnknown1099Year(
  form1099TaxYear: number | null | undefined,
): boolean {
  // Explicit null/undefined only. A missing 1099 is not an unknown-year 1099;
  // callers must also check that a summary exists.
  return form1099TaxYear == null;
}

export function isSameYear1099Compare(
  form1099TaxYear: number | null | undefined,
  analysisTaxYear: number | null | undefined,
): boolean {
  return (
    typeof form1099TaxYear === "number" &&
    typeof analysisTaxYear === "number" &&
    form1099TaxYear === analysisTaxYear
  );
}

export function roundCents(value: number): number {
  return Math.round(value * 100) / 100;
}

export type WashSaleFlagLike = {
  disallowed_loss?: number;
  purchase_date?: string | null;
  sale_date?: string | null;
  repurchase_date?: string | null;
};

export type ClassifiedWashInput =
  | WashSaleFlagLike[]
  | number
  | null
  | undefined;

// Same threshold as csv_parser / harvesting: held more than 365 days = long-term.
const LONG_TERM_HOLDING_DAYS = 365;

function parseFlagDate(value: string | null | undefined): Date | null {
  if (value == null || value === "") {
    return null;
  }
  const day = String(value).slice(0, 10);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!match) {
    return null;
  }
  const parsed = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  );
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function washFlagIsLongTerm(flag: WashSaleFlagLike): boolean {
  const start = parseFlagDate(flag.purchase_date);
  const end =
    parseFlagDate(flag.sale_date) ?? parseFlagDate(flag.repurchase_date);
  if (!start || !end) {
    return false;
  }
  const days = Math.round((end.getTime() - start.getTime()) / 86_400_000);
  return days > LONG_TERM_HOLDING_DAYS;
}

export function csvWashSaleDisallowedTotal(
  flags: Array<{ disallowed_loss?: number }> | undefined,
): number {
  return (flags ?? []).reduce(
    (sum, flag) => sum + (Number(flag.disallowed_loss) || 0),
    0,
  );
}

export function hasRealizedNets(
  realized: RealizedSummary | null | undefined,
): realized is RealizedSummary {
  return realized != null && (realized.net_st != null || realized.net_lt != null);
}

export function classifiedExportWash(
  flagsOrAmount: ClassifiedWashInput = 0,
): { shortTerm: number; longTerm: number } {
  // Classify each CSV wash-sale flag by the holding term of its underlying
  // sale (purchase_date vs sale_date, falling back to repurchase). A scalar
  // total is treated as short-term. Do not dump long-term wash into ST
  // just because ST loss buckets have room.
  if (!Array.isArray(flagsOrAmount)) {
    const wash = Math.max(Number(flagsOrAmount) || 0, 0);
    return { shortTerm: roundCents(wash), longTerm: 0 };
  }
  let shortTerm = 0;
  let longTerm = 0;
  for (const flag of flagsOrAmount) {
    const amount = Math.max(Number(flag.disallowed_loss) || 0, 0);
    if (amount <= 0) {
      continue;
    }
    if (washFlagIsLongTerm(flag)) {
      longTerm += amount;
    } else {
      shortTerm += amount;
    }
  }
  return {
    shortTerm: roundCents(shortTerm),
    longTerm: roundCents(longTerm),
  };
}

export function exportShortTermNet(
  realized: RealizedSummary | null | undefined,
  flagsOrAmount: ClassifiedWashInput = 0,
): number {
  if (!hasRealizedNets(realized)) {
    return 0;
  }
  return roundCents(
    (realized.net_st ?? 0) + classifiedExportWash(flagsOrAmount).shortTerm,
  );
}

export function exportLongTermNet(
  realized: RealizedSummary | null | undefined,
  flagsOrAmount: ClassifiedWashInput = 0,
): number {
  if (!hasRealizedNets(realized)) {
    return 0;
  }
  return roundCents(
    (realized.net_lt ?? 0) + classifiedExportWash(flagsOrAmount).longTerm,
  );
}
