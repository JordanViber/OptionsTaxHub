/**
 * Single-leg and single-vertical options what-if: max gain / max loss /
 * breakeven / capital or collateral.
 *
 * Premium is per share (Robinhood / OCC). Multiplier is 100.
 * Not tax treatment, not a filed Form 8949, not the year-close packet.
 */
import type { Position } from "./types";

export const CONTRACT_MULTIPLIER = 100;

export type OptionRight = "call" | "put";
export type OptionSide = "buy" | "sell";
export type VerticalStructure = "debit" | "credit";

export interface EntryProposal {
  symbol: string;
  right: OptionRight | "";
  strike: number | null;
  expiration: string;
  side: OptionSide | "";
  quantity: number | null;
  premium: number | null;
}

export interface VerticalProposal {
  symbol: string;
  right: OptionRight | "";
  structure: VerticalStructure | "";
  lowerStrike: number | null;
  higherStrike: number | null;
  expiration: string;
  quantity: number | null;
  lowerPremium: number | null;
  higherPremium: number | null;
}

export type ComboKind = "straddle" | "strangle";

export interface ComboProposal {
  symbol: string;
  kind: ComboKind;
  side: OptionSide | "";
  /** Straddle only. Ignored for strangle. */
  strike: number | null;
  /** Strangle only. Ignored for straddle. */
  putStrike: number | null;
  callStrike: number | null;
  expiration: string;
  quantity: number | null;
  callPremium: number | null;
  putPremium: number | null;
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

export interface ComboPayoff {
  debitCredit: number;
  maxLoss: number | null;
  maxGain: number | null;
  breakevenLow: number;
  breakevenHigh: number;
  collateralNote: string | null;
}

export type ComboAnalysisResult =
  | { ok: true; payoff: ComboPayoff }
  | { ok: false; reason: EntryFailReason; message: string };

const CONTRACT_LABEL_PATTERN =
  /^([A-Z]+)\s+(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(Call|Put)\s+\$(\d+(?:\.\d+)?)$/i;

const INCOMPLETE_MESSAGE =
  "Enter premium to see max gain, max loss, and breakeven.";

export const VERTICAL_INCOMPLETE_MESSAGE =
  "Enter both strikes and both premiums to see max gain, max loss, and breakeven.";
export const VERTICAL_SAME_STRIKES_MESSAGE =
  "Choose two different strikes for the same expiry.";
export const VERTICAL_STRIKE_ORDER_MESSAGE =
  "Higher strike must be above the lower strike.";
export const VERTICAL_CALL_PREMIUM_ORDER_MESSAGE =
  "Lower-strike premium should be at least the higher-strike premium.";
export const VERTICAL_PUT_PREMIUM_ORDER_MESSAGE =
  "Higher-strike premium should be at least the lower-strike premium.";
export const VERTICAL_NET_DEBIT_EXCEEDS_WIDTH_MESSAGE =
  "Net debit cannot exceed the strike width.";
export const VERTICAL_NET_CREDIT_EXCEEDS_WIDTH_MESSAGE =
  "Net credit cannot exceed the strike width.";
export const VERTICAL_PUT_BREAKEVEN_MESSAGE =
  "Breakeven is at or below $0 for this put vertical — check strikes and premiums.";
export const STRADDLE_INCOMPLETE_MESSAGE =
  "Enter strike and both premiums to see max gain, max loss, and both breakevens.";
export const STRANGLE_INCOMPLETE_MESSAGE =
  "Enter both strikes and both premiums to see max gain, max loss, and both breakevens.";
export const STRANGLE_STRIKE_ORDER_MESSAGE =
  "Put strike must be below the call strike.";
export const COMBO_BREAKEVEN_MESSAGE =
  "Lower breakeven is at or below $0 for this position — check strikes and premiums.";

export function todayIso(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** UTC calendar date. Rank windows must match server as_of, not local CT. */
export function utcTodayIso(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

export function addCalendarDaysIso(base: string, days: number): string {
  const [year, month, day] = base.split("-").map(Number);
  const utc = new Date(Date.UTC(year, month - 1, day));
  utc.setUTCDate(utc.getUTCDate() + days);
  return utc.toISOString().slice(0, 10);
}

export type LeapWindowPreset = "12-24" | "12-18" | "18-24";

export const LEAP_WINDOW_PRESET_DAYS: Record<
  LeapWindowPreset,
  { from: number; to: number }
> = {
  "12-24": { from: 365, to: 730 },
  "12-18": { from: 365, to: 547 },
  "18-24": { from: 547, to: 730 },
};

export function leapWindowForPreset(
  preset: LeapWindowPreset,
  asOf: string = utcTodayIso(),
): { from: string; to: string } {
  const span = LEAP_WINDOW_PRESET_DAYS[preset];
  return {
    from: addCalendarDaysIso(asOf, span.from),
    to: addCalendarDaysIso(asOf, span.to),
  };
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

export function analyzeVertical(
  proposal: VerticalProposal,
  asOf: string = todayIso(),
): EntryAnalysisResult {
  const symbol = proposal.symbol.trim();
  const { right, structure, expiration } = proposal;
  const { lowerStrike, higherStrike, quantity, lowerPremium, higherPremium } =
    proposal;

  const missingCore =
    !symbol ||
    (right !== "call" && right !== "put") ||
    (structure !== "debit" && structure !== "credit") ||
    !expiration.trim();
  const missingNumbers =
    !isFiniteNumber(lowerStrike) ||
    !isFiniteNumber(higherStrike) ||
    !isFiniteNumber(quantity) ||
    !isFiniteNumber(lowerPremium) ||
    !isFiniteNumber(higherPremium);

  if (missingCore || missingNumbers) {
    return {
      ok: false,
      reason: "incomplete",
      message: VERTICAL_INCOMPLETE_MESSAGE,
    };
  }

  if (
    lowerStrike <= 0 ||
    higherStrike <= 0 ||
    quantity < 1 ||
    !Number.isInteger(quantity) ||
    lowerPremium < 0 ||
    higherPremium < 0
  ) {
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

  if (lowerStrike === higherStrike) {
    return {
      ok: false,
      reason: "invalid",
      message: VERTICAL_SAME_STRIKES_MESSAGE,
    };
  }

  if (higherStrike < lowerStrike) {
    return {
      ok: false,
      reason: "invalid",
      message: VERTICAL_STRIKE_ORDER_MESSAGE,
    };
  }

  const width = roundCents(higherStrike - lowerStrike);
  const callNet = roundCents(lowerPremium - higherPremium);
  const putNet = roundCents(higherPremium - lowerPremium);
  const net = right === "call" ? callNet : putNet;

  if (net < 0) {
    return {
      ok: false,
      reason: "invalid",
      message:
        right === "call"
          ? VERTICAL_CALL_PREMIUM_ORDER_MESSAGE
          : VERTICAL_PUT_PREMIUM_ORDER_MESSAGE,
    };
  }

  if (net > width) {
    return {
      ok: false,
      reason: "invalid",
      message:
        structure === "debit"
          ? VERTICAL_NET_DEBIT_EXCEEDS_WIDTH_MESSAGE
          : VERTICAL_NET_CREDIT_EXCEEDS_WIDTH_MESSAGE,
    };
  }

  const isDebit = structure === "debit";
  const debitCredit = roundCents(net * CONTRACT_MULTIPLIER * quantity);
  const widthValue = roundCents(width * CONTRACT_MULTIPLIER * quantity);
  const maxLoss = isDebit ? debitCredit : roundCents(widthValue - debitCredit);
  const maxGain = isDebit ? roundCents(widthValue - debitCredit) : debitCredit;
  const breakeven =
    right === "call"
      ? roundCents(lowerStrike + net)
      : roundCents(higherStrike - net);

  if (right === "put" && putBreakevenInvalid(breakeven)) {
    return {
      ok: false,
      reason: "invalid",
      message: VERTICAL_PUT_BREAKEVEN_MESSAGE,
    };
  }

  const collateralNote = isDebit
    ? `Defined-risk debit: capital is the ${formatUsdCents(maxLoss)} net debit paid.`
    : `Defined-risk credit: collateral is ${formatUsdCents(maxLoss)} (width minus credit). Broker margin not modeled.`;

  return {
    ok: true,
    payoff: {
      debitCredit,
      maxLoss,
      maxGain,
      breakeven,
      collateralNote,
    },
  };
}

export function analyzeCombo(
  proposal: ComboProposal,
  asOf: string = todayIso(),
): ComboAnalysisResult {
  const symbol = proposal.symbol.trim();
  const { kind, side, expiration } = proposal;
  const { strike, putStrike, callStrike, quantity, callPremium, putPremium } =
    proposal;

  const missingCore =
    !symbol ||
    (side !== "buy" && side !== "sell") ||
    !expiration.trim();
  const missingNumbers =
    kind === "straddle"
      ? !isFiniteNumber(strike) ||
        !isFiniteNumber(quantity) ||
        !isFiniteNumber(callPremium) ||
        !isFiniteNumber(putPremium)
      : !isFiniteNumber(putStrike) ||
        !isFiniteNumber(callStrike) ||
        !isFiniteNumber(quantity) ||
        !isFiniteNumber(callPremium) ||
        !isFiniteNumber(putPremium);

  if (missingCore || missingNumbers) {
    return {
      ok: false,
      reason: "incomplete",
      message:
        kind === "strangle"
          ? STRANGLE_INCOMPLETE_MESSAGE
          : STRADDLE_INCOMPLETE_MESSAGE,
    };
  }

  const comboStrikes =
    kind === "straddle" ? [strike, strike] : [putStrike, callStrike];
  if (
    comboStrikes.some((value) => !isFiniteNumber(value) || value <= 0) ||
    !isFiniteNumber(quantity) ||
    quantity < 1 ||
    !Number.isInteger(quantity) ||
    !isFiniteNumber(callPremium) ||
    !isFiniteNumber(putPremium) ||
    callPremium < 0 ||
    putPremium < 0
  ) {
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

  const lowerStrike = kind === "straddle" ? strike : putStrike;
  const upperStrike = kind === "straddle" ? strike : callStrike;
  if (!isFiniteNumber(lowerStrike) || !isFiniteNumber(upperStrike)) {
    return {
      ok: false,
      reason: "invalid",
      message: "Strike, quantity, and premium must be valid numbers.",
    };
  }

  if (kind === "strangle" && lowerStrike >= upperStrike) {
    return {
      ok: false,
      reason: "invalid",
      message: STRANGLE_STRIKE_ORDER_MESSAGE,
    };
  }

  const net = roundCents(callPremium + putPremium);
  const debitCredit = roundCents(net * CONTRACT_MULTIPLIER * quantity);
  const breakevenLow = roundCents(lowerStrike - net);
  const breakevenHigh = roundCents(upperStrike + net);

  if (putBreakevenInvalid(breakevenLow)) {
    return {
      ok: false,
      reason: "invalid",
      message: COMBO_BREAKEVEN_MESSAGE,
    };
  }

  const isBuy = side === "buy";
  const collateralNote = isBuy
    ? `Long ${kind}: capital is the ${formatUsdCents(debitCredit)} net debit paid.`
    : `Short ${kind}: broker-specific collateral is not modeled.`;

  return {
    ok: true,
    payoff: {
      debitCredit,
      maxLoss: isBuy ? debitCredit : null,
      maxGain: isBuy ? null : debitCredit,
      breakevenLow,
      breakevenHigh,
      collateralNote,
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
  proposal: Pick<EntryProposal, "symbol" | "right" | "strike" | "expiration">,
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

/**
 * Book context for a vertical. Underlying holdings only — never covered,
 * never "already hold this contract".
 */
export function buildVerticalContextLine(
  proposal: Pick<VerticalProposal, "symbol">,
  positions: Position[] | null | undefined,
): string | null {
  if (!positions || positions.length === 0) return null;
  const symbol = proposal.symbol.trim().toUpperCase();
  if (!symbol) return null;

  const matches = positions.filter(
    (position) => position.symbol.trim().toUpperCase() === symbol,
  );
  if (matches.length === 0) return null;

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

  return parts.length === 2 && stockPositions.length > 0
    ? `${parts[0]} and ${parts[1]}`
    : parts[0];
}

/**
 * Book context for a straddle or strangle. Underlying holdings only —
 * never covered, never "already hold this contract".
 */
export function buildComboContextLine(
  proposal: Pick<ComboProposal, "symbol">,
  positions: Position[] | null | undefined,
): string | null {
  return buildVerticalContextLine(proposal, positions);
}
