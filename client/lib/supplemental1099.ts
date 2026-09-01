import type { RealizedSummary, Supplemental1099Summary } from "@/lib/types";

export const SUPPLEMENTAL_1099_UPLOAD_TITLE =
  "Robinhood 1099 for the tax year you are closing";

export const SUPPLEMENTAL_1099_CONTEXT_COPY =
  "Broker 1099 for the tax year you are closing — reconciliation context, not lot history.";

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

export const SUPPLEMENTAL_1099_COMPARE_TITLE = "1099 vs your export";

export const SUPPLEMENTAL_1099_COMPARE_COPY =
  "Two columns, totals only. Broker 1099 uses settlement date; this export uses trade date. ST/LT nets include wash-sale disallowed (1099 definition); wash is also shown separately.";

export const SUPPLEMENTAL_1099_BROKER_COLUMN = "Broker 1099 (settlement date)";

export const SUPPLEMENTAL_1099_EXPORT_COLUMN = "This export (trade date)";

export const SUPPLEMENTAL_1099_GAP_COPY =
  "These totals often disagree. A year-end short option (for example SPX 12/31) can print a gain on the 1099 while this export still shows a loss until January settlement. That is not a software bug. We do not parse settlement lots from the PDF. An incomplete export also shows up here. Traders on r/options have reported the same gap — a Robinhood 1099 showing +$2,699 while the export showed a $542 loss.";

export const SUPPLEMENTAL_1099_SETTLEMENT_FAQ =
  "Robinhood 1099 uses settlement date, so a year-end short option (for example SPX 12/31) can show a gain on the 1099 for a trade that does not settle until January. Totals only — we do not parse settlement-date lots from the PDF.";

export const SUPPLEMENTAL_1099_WASH_SALE_FAQ =
  "Options and credit-spread wash-sale treatment can differ from the broker 1099. We show the 1099 wash-sale disallowed figure as reported.";

export const SUPPLEMENTAL_1099_AFTER_FIRST_RUN_TITLE =
  "Optional: 1099 for the tax year you are closing";

export const SUPPLEMENTAL_1099_AFTER_FIRST_RUN_COPY =
  "Upload your Robinhood 1099 PDF for the tax year you are closing. Reconciliation context, not lot history. We show broker-reported totals next to this export.";

export const SUPPLEMENTAL_1099_FIRST_RUN_HINT =
  "You can also attach the Robinhood 1099 PDF for the tax year you are closing next to the CSV. Reconciliation context, not lot history.";

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
