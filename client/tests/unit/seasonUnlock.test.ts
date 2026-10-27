import {
  SEASON_NOTHING_TO_REPORT,
  seasonPayGate,
  seasonUnlockEnabled,
  seasonYearHintLine,
} from "@/lib/seasonUnlock";
import type { PortfolioAnalysis, RealizedSummary } from "@/lib/types";

function analysis(overrides: Partial<PortfolioAnalysis> = {}): PortfolioAnalysis {
  const summary = {
    total_market_value: 0,
    total_cost_basis: 0,
    total_unrealized_pnl: 0,
    total_unrealized_pnl_pct: 0,
    total_harvestable_losses: 0,
    estimated_tax_savings: 0,
    positions_count: 0,
    lots_with_losses: 0,
    lots_with_gains: 0,
    wash_sale_flags_count: 0,
    ...overrides.summary,
  };
  return {
    positions: [],
    tax_lots: [],
    suggestions: [],
    wash_sale_flags: [],
    summary,
    tax_profile: {
      filing_status: "single",
      estimated_annual_income: 0,
      state: "",
      tax_year: 2026,
    },
    disclaimer: "",
    errors: [],
    warnings: [],
    ...overrides,
    summary,
  };
}

function realized(taxYear: number, count: number): RealizedSummary {
  return {
    tax_year: taxYear,
    st_gains: 0,
    st_losses: 0,
    lt_gains: 0,
    lt_losses: 0,
    net_st: 0,
    net_lt: 0,
    total_net: 0,
    transactions_count: count,
  };
}

describe("season unlock gate", () => {
  const previous = process.env.NEXT_PUBLIC_SEASON_UNLOCK;

  afterEach(() => {
    if (previous === undefined) {
      delete process.env.NEXT_PUBLIC_SEASON_UNLOCK;
    } else {
      process.env.NEXT_PUBLIC_SEASON_UNLOCK = previous;
    }
  });

  it("stays off and leaves Pay unchanged when the flag is unset", () => {
    delete process.env.NEXT_PUBLIC_SEASON_UNLOCK;
    expect(seasonUnlockEnabled()).toBe(false);
    expect(seasonPayGate(analysis({ sample_run: true }))).toBeNull();
  });

  it("hides Pay on the sample", () => {
    process.env.NEXT_PUBLIC_SEASON_UNLOCK = "1";
    const gate = seasonPayGate(
      analysis({
        sample_run: true,
        summary: { ...analysis().summary, realized_summary: realized(2026, 3) },
      }),
    );
    expect(gate).toEqual({ hidePay: true, disabled: true, lines: [] });
  });

  it("disables Pay when 2026 has no sales and names a different year", () => {
    process.env.NEXT_PUBLIC_SEASON_UNLOCK = "true";
    const gate = seasonPayGate(
      analysis({
        tax_profile: {
          filing_status: "single",
          estimated_annual_income: 0,
          state: "",
          tax_year: 2025,
        },
        summary: { ...analysis().summary, realized_summary: realized(2025, 4) },
      }),
    );
    expect(gate?.hidePay).toBe(false);
    expect(gate?.disabled).toBe(true);
    expect(gate?.lines).toEqual([
      SEASON_NOTHING_TO_REPORT,
      seasonYearHintLine(2025),
    ]);
  });

  it("points at the activity year when the profile is 2026 but nothing sold", () => {
    process.env.NEXT_PUBLIC_SEASON_UNLOCK = "yes";
    const gate = seasonPayGate(
      analysis({
        summary: {
          ...analysis().summary,
          realized_summary: realized(2026, 0),
          activity_last_date: "2024-11-02",
        },
      }),
    );
    expect(gate?.lines).toEqual([
      SEASON_NOTHING_TO_REPORT,
      seasonYearHintLine(2024),
    ]);
  });

  it("uses one line when nothing names another year", () => {
    process.env.NEXT_PUBLIC_SEASON_UNLOCK = "1";
    const gate = seasonPayGate(
      analysis({
        summary: { ...analysis().summary, realized_summary: realized(2026, 0) },
      }),
    );
    expect(gate?.lines).toEqual([SEASON_NOTHING_TO_REPORT]);
  });

  it("leaves Pay enabled when the run has 2026 sales", () => {
    process.env.NEXT_PUBLIC_SEASON_UNLOCK = "1";
    const gate = seasonPayGate(
      analysis({
        summary: { ...analysis().summary, realized_summary: realized(2026, 2) },
      }),
    );
    expect(gate).toEqual({ hidePay: false, disabled: false, lines: [] });
  });
});
