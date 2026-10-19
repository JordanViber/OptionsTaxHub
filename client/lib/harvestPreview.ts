import type { HarvestingSuggestion, Position, TaxLot } from "./types";

export const PACKET_PANEL_ID = "year-close-packet";
export const PREVIEW_CARD_LIMIT = 4;

export interface HarvestTeaser {
  id: string;
  label: string;
  symbol: string;
  estimatedLoss: number;
  taxSavings: number | null;
  washSaleRisk: boolean;
  isLongTerm: boolean;
  priceNote?: string;
  reviewOnly?: boolean;
}

export interface HarvestTeaserTotals {
  count: number;
  totalLoss: number;
  totalSavings: number;
  hasTaxSavings: boolean;
  unknownLossCount: number;
}

export function purchaseFillUnknownCopy(count: number): string {
  const noun = count === 1 ? "lot uses" : "lots use";
  return `${count} ${noun} your CSV purchase price, no live quote, loss unknown.`;
}

function isPurchaseFill(teaser: HarvestTeaser): boolean {
  return Boolean(teaser.priceNote) || Boolean(teaser.reviewOnly);
}

function losingLots(position: Position): TaxLot[] {
  return (position.tax_lots ?? []).filter(
    (lot) => lot.unrealized_pnl != null && lot.unrealized_pnl < 0,
  );
}

function teaserFromSuggestion(suggestion: HarvestingSuggestion): HarvestTeaser {
  return {
    id: suggestion.suggestion_id || `${suggestion.symbol}-${suggestion.priority}`,
    label: suggestion.display_label || suggestion.symbol,
    symbol: suggestion.symbol,
    estimatedLoss: suggestion.estimated_loss,
    taxSavings: suggestion.tax_savings_estimate,
    washSaleRisk: suggestion.wash_sale_risk,
    isLongTerm: suggestion.is_long_term,
    priceNote: suggestion.price_note || undefined,
    reviewOnly: suggestion.action === "REVIEW",
  };
}

function teaserFromLot(
  position: Position,
  lot: TaxLot,
  index: number,
): HarvestTeaser {
  return {
    id: `${position.position_id ?? position.symbol}-lot-${index}`,
    label: position.display_label || position.symbol,
    symbol: position.symbol,
    estimatedLoss: Math.abs(lot.unrealized_pnl ?? 0),
    taxSavings: null,
    washSaleRisk: position.wash_sale_risk,
    isLongTerm: Boolean(lot.is_long_term),
  };
}

function teaserFromPosition(position: Position): HarvestTeaser {
  return {
    id: position.position_id ?? position.symbol,
    label: position.display_label || position.symbol,
    symbol: position.symbol,
    estimatedLoss: Math.abs(position.unrealized_pnl ?? 0),
    taxSavings: null,
    washSaleRisk: position.wash_sale_risk,
    isLongTerm: Boolean(position.is_long_term),
  };
}

/**
 * Preview rows for the unpaid desk.
 *
 * Prefer engine suggestions (including wash-risk lots). If the engine returned
 * none, fall back to open lots / positions that already show an unrealized loss
 * so the Suggestions tab does not advertise 0 while Positions is red.
 */
export function buildHarvestTeasers(
  suggestions: HarvestingSuggestion[],
  positions: Position[] = [],
): HarvestTeaser[] {
  if (suggestions.length > 0) {
    return [...suggestions]
      .map(teaserFromSuggestion)
      .sort((left, right) => right.estimatedLoss - left.estimatedLoss);
  }

  const teasers: HarvestTeaser[] = [];
  for (const position of positions) {
    const lots = losingLots(position);
    if (lots.length > 0) {
      lots.forEach((lot, index) => {
        teasers.push(teaserFromLot(position, lot, index));
      });
      continue;
    }
    if (position.unrealized_pnl != null && position.unrealized_pnl < 0) {
      teasers.push(teaserFromPosition(position));
    }
  }
  return teasers.sort((left, right) => right.estimatedLoss - left.estimatedLoss);
}

export function harvestTeaserTotals(
  teasers: HarvestTeaser[],
): HarvestTeaserTotals {
  const losses = teasers.filter((teaser) => !isPurchaseFill(teaser));
  const totalLoss = losses.reduce((sum, teaser) => sum + teaser.estimatedLoss, 0);
  const totalSavings = losses.reduce(
    (sum, teaser) => sum + (teaser.taxSavings ?? 0),
    0,
  );
  return {
    count: losses.length,
    totalLoss,
    totalSavings,
    hasTaxSavings: losses.some(
      (teaser) => teaser.taxSavings != null && teaser.taxSavings > 0,
    ),
    unknownLossCount: teasers.length - losses.length,
  };
}

export function formatUsd(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

export function scrollToPacketPanel(): void {
  if (typeof document === "undefined") return;
  document.getElementById(PACKET_PANEL_ID)?.scrollIntoView({
    behavior: "smooth",
    block: "center",
  });
}
