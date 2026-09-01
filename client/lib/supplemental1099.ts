import type { RealizedSummary, Supplemental1099Summary } from "@/lib/types";

export const SUPPLEMENTAL_1099_UPLOAD_TITLE =
  "Robinhood 1099 for the tax year you are closing";

export const SUPPLEMENTAL_1099_CONTEXT_COPY =
  "Broker 1099 for the tax year you are closing — reconciliation context, not lot history.";

export const SUPPLEMENTAL_1099_APPLIED_TITLE =
  "Previous-year 1099 supplement applied";

export const SUPPLEMENTAL_1099_APPLIED_COPY =
  "This 1099 is a different tax year than the dashboard year, so it is shown as a previous-year supplement — not a same-year 1099 vs export compare.";

export const SUPPLEMENTAL_1099_COMPARE_TITLE = "1099 vs your export";

export const SUPPLEMENTAL_1099_COMPARE_COPY =
  "Two columns, totals only. Broker 1099 uses settlement date; this export uses trade date. ST/LT/wash as reported.";

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

export function csvWashSaleDisallowedTotal(
  flags: Array<{ disallowed_loss?: number }> | undefined,
): number {
  return (flags ?? []).reduce(
    (sum, flag) => sum + (Number(flag.disallowed_loss) || 0),
    0,
  );
}

export function exportShortTermNet(
  realized: RealizedSummary | null | undefined,
): number {
  return realized?.net_st ?? 0;
}

export function exportLongTermNet(
  realized: RealizedSummary | null | undefined,
): number {
  return realized?.net_lt ?? 0;
}
