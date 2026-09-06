import {
  analyzeEntry,
  buildEntryContextLine,
  formatUsdCents,
  parseContractLabel,
  todayIso,
  type EntryProposal,
} from "../../lib/entryAnalysis";
import type { Position } from "../../lib/types";

const AS_OF = "2026-09-06";

function proposal(
  overrides: Partial<EntryProposal> = {},
): EntryProposal {
  return {
    symbol: "NVDA",
    right: "call",
    strike: 250,
    expiration: "2026-12-18",
    side: "buy",
    quantity: 1,
    premium: 4.2,
    ...overrides,
  };
}

function stockPosition(
  symbol: string,
  quantity: number,
  extras: Partial<Position> = {},
): Position {
  return {
    position_id: `${symbol}:stock`,
    symbol,
    display_label: symbol,
    quantity,
    avg_cost_basis: 100,
    total_cost_basis: 100 * quantity,
    current_price: 100,
    market_value: 100 * quantity,
    unrealized_pnl: 0,
    unrealized_pnl_pct: 0,
    earliest_purchase_date: "2026-01-15",
    holding_period_days: 200,
    is_long_term: false,
    asset_type: "stock",
    tax_lots: [],
    wash_sale_risk: false,
    ...extras,
  };
}

function optionPosition(
  symbol: string,
  label: string,
  quantity = 1,
): Position {
  return {
    position_id: `${symbol}:option:${label}`,
    symbol,
    display_label: label,
    contract_label: label,
    quantity,
    avg_cost_basis: 4,
    total_cost_basis: 400,
    current_price: 4,
    market_value: 400,
    unrealized_pnl: 0,
    unrealized_pnl_pct: 0,
    earliest_purchase_date: "2026-08-01",
    holding_period_days: 30,
    is_long_term: false,
    asset_type: "option",
    tax_lots: [],
    wash_sale_risk: false,
  };
}

describe("analyzeEntry", () => {
  it("long call: max loss is premium paid, unlimited gain, breakeven strike + premium", () => {
    const result = analyzeEntry(proposal(), AS_OF);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payoff.maxLoss).toBe(420);
    expect(result.payoff.maxGain).toBeNull();
    expect(result.payoff.breakeven).toBe(254.2);
    expect(result.payoff.collateralNote).toBeNull();
    expect(formatUsdCents(result.payoff.maxLoss ?? 0)).toBe("$420.00");
  });

  it("long put: max loss is premium paid; max gain to zero; breakeven strike − premium", () => {
    const result = analyzeEntry(
      proposal({
        right: "put",
        strike: 100,
        premium: 3,
        quantity: 2,
      }),
      AS_OF,
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payoff.maxLoss).toBe(600);
    expect(result.payoff.maxGain).toBe(19400);
    expect(result.payoff.breakeven).toBe(97);
  });

  it("short put: cash-secured collateral, finite max loss, breakeven strike − premium", () => {
    const result = analyzeEntry(
      proposal({
        right: "put",
        side: "sell",
        strike: 50,
        premium: 2,
        quantity: 1,
      }),
      AS_OF,
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payoff.maxGain).toBe(200);
    expect(result.payoff.maxLoss).toBe(4800);
    expect(result.payoff.breakeven).toBe(48);
    expect(result.payoff.collateralNote).toMatch(/Cash-secured: about \$5,000\.00/);
    expect(result.payoff.collateralNote).toMatch(/100 shares at \$50\.00/);
    expect(result.payoff.collateralNote).toMatch(/buying-power \/ margin not modeled/);
  });

  it("short call: unlimited max loss, naked margin note, breakeven strike + premium", () => {
    const result = analyzeEntry(
      proposal({
        side: "sell",
        strike: 200,
        premium: 1.5,
      }),
      AS_OF,
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payoff.maxGain).toBe(150);
    expect(result.payoff.maxLoss).toBeNull();
    expect(result.payoff.breakeven).toBe(201.5);
    expect(result.payoff.collateralNote).toMatch(/Naked call: broker margin/);
    expect(result.payoff.collateralNote).toMatch(/theoretically unlimited/);
  });

  it("incomplete fields return no payoff", () => {
    const result = analyzeEntry(proposal({ premium: null }), AS_OF);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toBe("incomplete");
    expect(result.message).toMatch(/Enter premium/);
  });

  it("missing symbol is incomplete", () => {
    const result = analyzeEntry(proposal({ symbol: "  " }), AS_OF);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toBe("incomplete");
  });

  it("past expiration returns an honest error and no payoff", () => {
    const result = analyzeEntry(
      proposal({ expiration: "2026-01-16" }),
      AS_OF,
    );
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toBe("expired");
    expect(result.message).toMatch(/Expiration is in the past/);
  });

  it("rejects non-positive strike, fractional qty, and negative premium", () => {
    expect(analyzeEntry(proposal({ strike: 0 }), AS_OF).ok).toBe(false);
    expect(analyzeEntry(proposal({ quantity: 1.5 }), AS_OF).ok).toBe(false);
    expect(analyzeEntry(proposal({ premium: -1 }), AS_OF).ok).toBe(false);
  });

  it("rejects a put whose breakeven is at or below zero", () => {
    const result = analyzeEntry(
      proposal({ right: "put", strike: 3, premium: 3 }),
      AS_OF,
    );
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toBe("invalid");
    expect(result.message).toMatch(/Breakeven is at or below \$0/);
  });

  it("allows an explicit premium of 0 on a long call", () => {
    const result = analyzeEntry(proposal({ premium: 0 }), AS_OF);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payoff.maxLoss).toBe(0);
    expect(result.payoff.breakeven).toBe(250);
  });
});

describe("buildEntryContextLine", () => {
  const nvda = stockPosition("NVDA", 52);

  it("returns null when no positions are loaded", () => {
    expect(buildEntryContextLine(proposal(), null)).toBeNull();
    expect(buildEntryContextLine(proposal(), [])).toBeNull();
    expect(buildEntryContextLine(proposal(), undefined)).toBeNull();
  });

  it("returns a share-count line for a 2026-sample-shaped NVDA stock position", () => {
    expect(buildEntryContextLine(proposal({ symbol: "nvda" }), [nvda])).toBe(
      "Open NVDA: 52 sh",
    );
  });

  it("returns null for an unknown ticker", () => {
    expect(buildEntryContextLine(proposal({ symbol: "QQQ" }), [nvda])).toBeNull();
  });

  it("returns null when the symbol is blank even if positions exist", () => {
    expect(buildEntryContextLine(proposal({ symbol: "" }), [nvda])).toBeNull();
  });

  it("does not mention wash, 1099, harvest, or $49", () => {
    const line = buildEntryContextLine(proposal({ symbol: "NVDA" }), [nvda]);
    expect(line).toBeTruthy();
    expect(line).not.toMatch(/wash|1099|harvest|\$49|Form 8949/i);
  });

  it("names a single open option when there is no stock", () => {
    const option = optionPosition("TSLA", "TSLA 12/18/2026 Put $250.00");
    expect(
      buildEntryContextLine(proposal({ symbol: "TSLA", right: "call" }), [
        option,
      ]),
    ).toBe("Open TSLA option: TSLA 12/18/2026 Put $250.00");
  });

  it("counts option positions alongside stock", () => {
    const option = optionPosition("NVDA", "NVDA 1/16/2027 Call $400.00");
    expect(buildEntryContextLine(proposal(), [nvda, option])).toBe(
      "Open NVDA: 52 sh and 1 option position",
    );
  });

  it("counts multiple option positions", () => {
    const a = optionPosition("NVDA", "NVDA 1/16/2027 Call $400.00");
    const b = optionPosition("NVDA", "NVDA 1/16/2027 Call $260.00");
    expect(buildEntryContextLine(proposal(), [nvda, a, b])).toBe(
      "Open NVDA: 52 sh and 2 option positions",
    );
    expect(
      buildEntryContextLine(proposal(), [a, b]),
    ).toBe("Open NVDA: 2 option positions");
  });

  it("says you already hold this contract when the label matches", () => {
    const option = optionPosition("NVDA", "NVDA 12/18/2026 Call $250.00");
    expect(
      buildEntryContextLine(
        proposal({
          symbol: "NVDA",
          right: "call",
          strike: 250,
          expiration: "2026-12-18",
        }),
        [option],
      ),
    ).toBe("You already hold this contract");
  });

  it("notes a short call may be covered when enough shares are open", () => {
    const tsla = stockPosition("TSLA", 100);
    expect(
      buildEntryContextLine(
        proposal({
          symbol: "TSLA",
          right: "call",
          side: "sell",
          quantity: 1,
        }),
        [tsla],
      ),
    ).toBe(
      "Open TSLA: 100 sh — this short call may be covered if you keep the shares",
    );
  });

  it("does not claim covered when share count is short of 100 per contract", () => {
    const tsla = stockPosition("TSLA", 4);
    expect(
      buildEntryContextLine(
        proposal({
          symbol: "TSLA",
          right: "call",
          side: "sell",
          quantity: 1,
        }),
        [tsla],
      ),
    ).toBe("Open TSLA: 4 sh");
  });

  it("formats fractional share counts", () => {
    const frac = stockPosition("AAPL", 10.25);
    expect(buildEntryContextLine(proposal({ symbol: "AAPL" }), [frac])).toBe(
      "Open AAPL: 10.25 sh",
    );
  });

  it("falls back when an option has no display label", () => {
    const option = optionPosition("TSLA", "");
    option.display_label = "";
    option.contract_label = "";
    expect(
      buildEntryContextLine(proposal({ symbol: "TSLA", right: "put" }), [
        option,
      ]),
    ).toBe("Open TSLA option: TSLA option");
  });

  it("ignores a contract label whose ticker does not match the position", () => {
    const option = optionPosition("NVDA", "TSLA 12/18/2026 Call $250.00");
    expect(buildEntryContextLine(proposal(), [nvda, option])).toBe(
      "Open NVDA: 52 sh and 1 option position",
    );
  });

  it("does not treat an unstruck proposal as the same contract", () => {
    const option = optionPosition("NVDA", "NVDA 12/18/2026 Call $250.00");
    expect(
      buildEntryContextLine(proposal({ strike: null }), [option]),
    ).toBe("Open NVDA option: NVDA 12/18/2026 Call $250.00");
  });

  it("returns null when matched rows are neither stock nor option", () => {
    const other = {
      ...nvda,
      asset_type: "crypto" as Position["asset_type"],
    };
    expect(buildEntryContextLine(proposal(), [other])).toBeNull();
  });

  it("does not claim covered when quantity is missing", () => {
    const tsla = stockPosition("TSLA", 100);
    expect(
      buildEntryContextLine(
        proposal({
          symbol: "TSLA",
          right: "call",
          side: "sell",
          quantity: null,
        }),
        [tsla],
      ),
    ).toBe("Open TSLA: 100 sh");
  });
});

describe("parseContractLabel and todayIso", () => {
  it("parses a Robinhood-style contract label", () => {
    expect(parseContractLabel("TSLA 3/16/2026 Put $375.00")).toEqual({
      symbol: "TSLA",
      expiration: "2026-03-16",
      right: "put",
      strike: 375,
    });
  });

  it("returns null for unparseable labels", () => {
    expect(parseContractLabel("Tesla Inc")).toBeNull();
  });

  it("returns an ISO calendar date for todayIso", () => {
    expect(todayIso(new Date(2026, 8, 6, 15))).toBe("2026-09-06");
    expect(todayIso()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("uses today as the default as-of date", () => {
    const nextYear = `${new Date().getFullYear() + 1}-06-15`;
    const result = analyzeEntry(proposal({ expiration: nextYear }));
    expect(result.ok).toBe(true);
  });
});
