import {
  buildHarvestTeasers,
  harvestTeaserTotals,
} from "../../lib/harvestPreview";
import type { HarvestingSuggestion, Position } from "../../lib/types";

const suggestion: HarvestingSuggestion = {
  symbol: "AAPL",
  suggestion_id: "AAPL-1",
  display_label: "AAPL",
  action: "SELL",
  quantity: 10,
  current_price: 140,
  cost_basis_per_share: 150,
  estimated_loss: 400,
  tax_savings_estimate: 88,
  holding_period_days: 40,
  is_long_term: false,
  wash_sale_risk: true,
  wash_sale_explanation: "Recent buy",
  replacement_candidates: [],
  ai_explanation: "",
  ai_generated: false,
  priority: 1,
};

const losingPosition: Position = {
  symbol: "TSLA",
  display_label: "TSLA",
  quantity: 16,
  avg_cost_basis: 350,
  total_cost_basis: 5600,
  current_price: 320,
  market_value: 5120,
  unrealized_pnl: -480,
  unrealized_pnl_pct: -8.6,
  earliest_purchase_date: "2026-08-27",
  holding_period_days: 1,
  is_long_term: false,
  asset_type: "stock",
  tax_lots: [
    {
      symbol: "TSLA",
      quantity: 16,
      cost_basis_per_share: 350,
      total_cost_basis: 5600,
      purchase_date: "2026-08-27",
      current_price: 320,
      asset_type: "stock",
      unrealized_pnl: -480,
      unrealized_pnl_pct: -8.6,
      holding_period_days: 1,
      is_long_term: false,
      wash_sale_disallowed: 0,
    },
  ],
  wash_sale_risk: true,
};

describe("buildHarvestTeasers", () => {
  it("uses engine suggestions when present", () => {
    const teasers = buildHarvestTeasers([suggestion], [losingPosition]);
    expect(teasers).toHaveLength(1);
    expect(teasers[0].symbol).toBe("AAPL");
    expect(teasers[0].estimatedLoss).toBe(400);
    expect(teasers[0].taxSavings).toBe(88);
  });

  it("falls back to losing lots when suggestions are empty", () => {
    const teasers = buildHarvestTeasers([], [losingPosition]);
    expect(teasers).toHaveLength(1);
    expect(teasers[0].symbol).toBe("TSLA");
    expect(teasers[0].estimatedLoss).toBe(480);
    expect(teasers[0].taxSavings).toBeNull();
    expect(teasers[0].washSaleRisk).toBe(true);
  });

  it("falls back to position P&L when lots are missing", () => {
    const teasers = buildHarvestTeasers([], [
      { ...losingPosition, tax_lots: [] },
    ]);
    expect(teasers).toHaveLength(1);
    expect(teasers[0].estimatedLoss).toBe(480);
  });

  it("returns nothing for an all-gain book", () => {
    const teasers = buildHarvestTeasers([], [
      { ...losingPosition, unrealized_pnl: 200, tax_lots: [] },
    ]);
    expect(teasers).toHaveLength(0);
  });
});

describe("harvestTeaserTotals", () => {
  it("sums open losses and tax savings", () => {
    const totals = harvestTeaserTotals(
      buildHarvestTeasers([suggestion], []),
    );
    expect(totals.count).toBe(1);
    expect(totals.totalLoss).toBe(400);
    expect(totals.totalSavings).toBe(88);
    expect(totals.hasTaxSavings).toBe(true);
  });
});
