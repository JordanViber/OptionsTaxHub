import type { PriceBasis } from "./types";

/** Desk copy when a price did not come from a live quote. */
export function csvPriceLabel(asOf: string): string {
  return `Prices as of your CSV (${asOf})`;
}

/**
 * Sentence shown next to price-dependent numbers.
 * The word "Live" is used only when every quote in the basis succeeded.
 */
export function priceAsOfCopy(
  basis: PriceBasis | null | undefined,
): string | null {
  if (!basis) return null;
  if (basis.live && basis.as_of) {
    const provider = basis.provider ? `${basis.provider} ` : "";
    return `Live ${provider}prices as of ${basis.as_of}`;
  }
  if (basis.label) return basis.label;
  if (basis.csv_as_of) return csvPriceLabel(basis.csv_as_of);
  return null;
}

/** Legacy payloads with no basis still render. A basis with no source does not. */
export function pricedNumbersVisible(
  basis: PriceBasis | null | undefined,
): boolean {
  if (!basis) return true;
  return priceAsOfCopy(basis) != null;
}
