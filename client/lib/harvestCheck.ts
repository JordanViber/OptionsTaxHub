import type { HarvestBuyKind, HarvestCheck } from "@/lib/types";
import { seasonUnlockEnabled } from "@/lib/seasonUnlock";

/** Names that never get a harvest or sell prompt. */
export const HARVEST_PROMPT_BLOCKED = new Set(["TSLA", "SPCX"]);

export const HARVEST_TAX_PRO_COPY =
  "A wash-sale rule may apply, ask a tax pro.";

export function harvestPromptBlocked(symbol: string | null | undefined): boolean {
  return HARVEST_PROMPT_BLOCKED.has((symbol || "").trim().toUpperCase());
}

export function harvestDetailOpen(
  analysis: { sample_run?: boolean; season_detail?: boolean | null },
  seasonUnlocked = false,
): boolean {
  if (analysis.sample_run) {
    return true;
  }
  if (!seasonUnlockEnabled()) {
    return false;
  }
  return seasonUnlocked || analysis.season_detail === true;
}

export function harvestCountLine(count: number): string {
  if (count === 1) {
    return "1 replacement buy";
  }
  return `${count} replacement buys`;
}

export function harvestBuyKindLabel(kind: HarvestBuyKind): string {
  switch (kind) {
    case "regular_buy":
      return "Regular buy";
    case "recurring_buy":
      return "Recurring buy";
    case "fractional_buy":
      return "Fractional buy";
    case "dividend_reinvestment":
      return "Dividend reinvestment";
    case "bought_call":
      return "Bought call";
    case "short_put":
      return "Short put";
    default: {
      const unexpected: never = kind;
      return unexpected;
    }
  }
}

export function formatHarvestMoney(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  if (Math.abs(rounded - Math.round(rounded)) < 0.001) {
    return `$${Math.round(rounded).toLocaleString("en-US")}`;
  }
  return rounded.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}

export function formatHarvestDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return iso;
  const parsed = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function harvestRowShowsDates(check: HarvestCheck): boolean {
  if (harvestPromptBlocked(check.symbol)) {
    return false;
  }
  return Boolean(check.earliest_clean_sell_date || check.safe_rebuy_date);
}
