import type { Supplemental1099Summary } from "@/lib/types";

export const SUPPLEMENTAL_1099_UPLOAD_TITLE = "Previous year's Robinhood 1099 PDF";

export const SUPPLEMENTAL_1099_CONTEXT_COPY =
  "Last year's broker 1099 for reconciliation context — not a rebuild of lots.";

export const SUPPLEMENTAL_1099_APPLIED_TITLE =
  "Previous-year 1099 supplement applied";

export const SUPPLEMENTAL_1099_APPLIED_COPY =
  "Last year's broker 1099 is shown as reconciliation context, not a rebuild of lots.";

export const SUPPLEMENTAL_1099_SETTLEMENT_FAQ =
  "Robinhood 1099 uses settlement date, so a year-end short option (for example SPX 12/31) can show a gain on the 1099 for a trade that does not settle until January. Totals only — we do not parse settlement-date lots from the PDF.";

export const SUPPLEMENTAL_1099_WASH_SALE_FAQ =
  "Options and credit-spread wash-sale treatment can differ from the broker 1099. We show the 1099 wash-sale disallowed figure as reported.";

export const SUPPLEMENTAL_1099_AFTER_FIRST_RUN_TITLE =
  "Optional: last year's 1099 for context";

export const SUPPLEMENTAL_1099_AFTER_FIRST_RUN_COPY =
  "Upload your previous year’s Robinhood 1099 PDF for broker-reported totals as reconciliation context. This does not rebuild tax lots from the PDF.";

export const SUPPLEMENTAL_1099_FIRST_RUN_HINT =
  "You can also attach last year's Robinhood 1099 PDF next to the CSV upload. It is reconciliation context, not a rebuild of lots.";

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
