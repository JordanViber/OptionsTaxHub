/**
 * Single-leg options what-if: max gain / max loss / breakeven / short collateral.
 *
 * Premium is per share (Robinhood / OCC). Multiplier is 100.
 * Not tax treatment, not a filed Form 8949, not the year-close packet.
 */
import type { Position } from "./types";

export const CONTRACT_MULTIPLIER = 100;

export type OptionRight = "call" | "put";
export type OptionSide = "buy" | "sell";

export interface EntryProposal {
  symbol: string;
  right: OptionRight | "";
  strike: number | null;
  expiration: string;
  side: OptionSide | "";
  quantity: number | null;
  premium: number | null;
}

export interface EntryPayoff {
  debitCredit: number;
  maxLoss: number | null;
  maxGain: number | null;
  breakeven: number;
  collateralNote: string | null;
}

export type EntryFailReason = "incomplete" | "invalid" | "expired";

export type EntryAnalysisResult =
  | { ok: true; payoff: EntryPayoff }
  | { ok: false; reason: EntryFailReason; message: string };

const CONTRACT_LABEL_PATTERN =
  /^([A-Z]+)\s+(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(Call|Put)\s+\$(\d+(?:\.\d+)?)$/i;

const INCOMPLETE_MESSAGE =
  "Enter premium to see max gain, max loss, and breakeven.";

export function todayIso(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function formatUsdCents(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function parseContractLabel(label: string): {
  symbol: string;
  expiration: string;
  right: OptionRight;
  strike: number;
} | null {
  const match = CONTRACT_LABEL_PATTERN.exec(label.trim());
  if (!match) return null;
  const month = match[2].padStart(2, "0");
  const day = match[3].padStart(2, "0");
  return {
    symbol: match[1].toUpperCase(),
    expiration: `${match[4]}-${month}-${day}`,
    right: match[5].toLowerCase() as OptionRight,
    strike: Number(match[6]),
  };
}

function roundCents(value: number): number {
  return Math.round(value * 100) / 100;
}

function isFiniteNumber(value: number | null): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function putBreakevenInvalid(breakeven: number): boolean {
  return breakeven <= 0;
}

export function analyzeEntry(
  proposal: EntryProposal,
  asOf: string = todayIso(),
): EntryAnalysisResult {
  const symbol = proposal.symbol.trim();
  const { right, side, expiration } = proposal;
  const { strike, quantity, premium } = proposal;

  const missingCore =
    !symbol ||
    (right !== "call" && right !== "put") ||
    (side !== "buy" && side !== "sell") ||
    !expiration.trim();
  const missingNumbers =
    !isFiniteNumber(strike) ||
    !isFiniteNumber(quantity) ||
    !isFiniteNumber(premium);

  if (missingCore || missingNumbers) {
    return { ok: false, reason: "incomplete", message: INCOMPLETE_MESSAGE };
  }

  if (strike <= 0 || quantity < 1 || !Number.isInteger(quantity) || premium < 0) {
    return {
      ok: false,
      reason: "invalid",
      message: "Strike, quantity, and premium must be valid numbers.",
    };
  }

  if (expiration < asOf) {
    return {
      ok: false,
      reason: "expired",
      message: "Expiration is in the past.",
    };
  }

  const debitCredit = roundCents(premium * CONTRACT_MULTIPLIER * quantity);
  const isBuy = side === "buy";
  const isCall = right === "call";

  if (isCall) {
    const breakeven = roundCents(strike + premium);
    if (isBuy) {
      return {
        ok: true,
        payoff: {
          debitCredit,
          maxLoss: debitCredit,
          maxGain: null,
          breakeven,
          collateralNote: null,
        },
      };
    }
    return {
      ok: true,
      payoff: {
        debitCredit,
        maxLoss: null,
        maxGain: debitCredit,
        breakeven,
        collateralNote:
          "Naked call: broker margin, not a fixed cash amount. Max loss is theoretically unlimited.",
      },
    };
  }

  const breakeven = roundCents(strike - premium);
  if (putBreakevenInvalid(breakeven)) {
    return {
      ok: false,
      reason: "invalid",
      message: "Breakeven is at or below $0 for this put — check strike and premium.",
    };
  }

  const maxPutLoss = roundCents(
    Math.max(0, strike - premium) * CONTRACT_MULTIPLIER * quantity,
  );

  if (isBuy) {
    return {
      ok: true,
      payoff: {
        debitCredit,
        maxLoss: debitCredit,
        maxGain: maxPutLoss,
        breakeven,
        collateralNote: null,
      },
    };
  }

  const cashSecured = roundCents(strike * CONTRACT_MULTIPLIER * quantity);
  const shareCount = quantity * CONTRACT_MULTIPLIER;
  return {
    ok: true,
    payoff: {
      debitCredit,
      maxLoss: maxPutLoss,
      maxGain: debitCredit,
      breakeven,
      collateralNote: `Cash-secured: about ${formatUsdCents(cashSecured)} to buy ${shareCount} shares at ${formatUsdCents(strike)}. Robinhood buying-power / margin not modeled.`,
    },
  };
}

function formatShareCount(quantity: number): string {
  if (Number.isInteger(quantity)) return String(quantity);
  return quantity.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function optionLabel(position: Position): string {
  return (position.contract_label || position.display_label || "").trim();
}

function holdsSameContract(
  position: Position,
  proposal: EntryProposal,
): boolean {
  const parsed = parseContractLabel(optionLabel(position));
  if (!parsed) return false;
  if (parsed.symbol !== proposal.symbol.trim().toUpperCase()) return false;
  if (proposal.right !== parsed.right) return false;
  if (!isFiniteNumber(proposal.strike)) return false;
  if (Math.abs(parsed.strike - proposal.strike) >= 0.001) return false;
  return parsed.expiration === proposal.expiration;
}

/**
 * One-line book context when positions are loaded. Not tax treatment.
 * Returns null on an empty desk or when the underlying is not in the book.
 */
export function buildEntryContextLine(
  proposal: Pick<EntryProposal, "symbol" | "right" | "strike" | "expiration" | "side" | "quantity">,
  positions: Position[] | null | undefined,
): string | null {
  if (!positions || positions.length === 0) return null;
  const symbol = proposal.symbol.trim().toUpperCase();
  if (!symbol) return null;

  const matches = positions.filter(
    (position) => position.symbol.trim().toUpperCase() === symbol,
  );
  if (matches.length === 0) return null;

  const sameContract = matches.find(
    (position) =>
      position.asset_type === "option" && holdsSameContract(position, proposal),
  );
  if (sameContract) {
    return "You already hold this contract";
  }

  const stockPositions = matches.filter(
    (position) => position.asset_type === "stock",
  );
  const optionPositions = matches.filter(
    (position) => position.asset_type === "option",
  );
  const stockQty = stockPositions.reduce(
    (sum, position) => sum + position.quantity,
    0,
  );

  const parts: string[] = [];
  if (stockPositions.length > 0) {
    parts.push(`Open ${symbol}: ${formatShareCount(stockQty)} sh`);
  }
  if (optionPositions.length === 1) {
    const label =
      optionPositions[0].display_label ||
      optionPositions[0].contract_label ||
      `${symbol} option`;
    parts.push(
      stockPositions.length > 0
        ? "1 option position"
        : `Open ${symbol} option: ${label}`,
    );
  } else if (optionPositions.length > 1) {
    parts.push(
      stockPositions.length > 0
        ? `${optionPositions.length} option positions`
        : `Open ${symbol}: ${optionPositions.length} option positions`,
    );
  }

  if (parts.length === 0) return null;

  let line =
    parts.length === 2 && stockPositions.length > 0
      ? `${parts[0]} and ${parts[1]}`
      : parts[0];

  const contracts = proposal.quantity ?? 0;
  // No short-commitment flag on public positions. If any same-symbol option
  // is already open, do not claim this short call may be covered.
  const covered =
    proposal.side === "sell" &&
    proposal.right === "call" &&
    Number.isInteger(contracts) &&
    contracts >= 1 &&
    stockQty >= contracts * CONTRACT_MULTIPLIER &&
    optionPositions.length === 0;
  if (covered) {
    line = `${line} — this short call may be covered if you keep the shares`;
  }

  return line;
}
