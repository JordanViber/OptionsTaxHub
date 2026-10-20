import type { PriceBasis } from "./types";

/** Desk copy when a price did not come from a live quote. */
export function csvPriceLabel(asOf: string): string {
  return `Prices as of your CSV (${asOf})`;
}

/** Known quote vendors. Anything else is omitted from the live sentence. */
const PROVIDER_DISPLAY: Record<string, string> = {
  yahoo: "Yahoo",
  alpaca: "Alpaca",
  tradier: "Tradier",
  marketdata: "MarketData",
  polygon: "Polygon",
};

const LIVE_AS_OF = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
});

function providerDisplayName(provider: string | null | undefined): string | null {
  if (!provider) return null;
  return PROVIDER_DISPLAY[provider.trim().toLowerCase()] ?? null;
}

/** Viewer's local date and short time. Unparseable input stays hidden. */
export function formatLiveAsOf(asOf: string | null | undefined): string | null {
  if (!asOf?.trim()) return null;
  const parsed = new Date(asOf);
  if (Number.isNaN(parsed.getTime())) return null;
  return LIVE_AS_OF.format(parsed).replaceAll(/[\u202f\u00a0]/g, " ");
}

/**
 * Sentence shown next to price-dependent numbers.
 * The word "Live" is used only when every quote in the basis succeeded.
 */
export function priceAsOfCopy(
  basis: PriceBasis | null | undefined,
): string | null {
  if (!basis) return null;
  if (basis.live) {
    const when = formatLiveAsOf(basis.as_of);
    if (!when) return "Live prices";
    const provider = providerDisplayName(basis.provider);
    return provider
      ? `Live ${provider} prices as of ${when}`
      : `Live prices as of ${when}`;
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
