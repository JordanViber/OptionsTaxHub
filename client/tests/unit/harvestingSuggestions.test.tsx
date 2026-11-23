import { render, screen, fireEvent } from "@testing-library/react";
import HarvestingSuggestions, {
  getRecommendedActionCopy,
} from "../../app/components/HarvestingSuggestions";
import type { HarvestingSuggestion, PriceBasis } from "../../lib/types";

const baseSuggestion: HarvestingSuggestion = {
  symbol: "AAPL",
  suggestion_id: "AAPL-stock-2025-01-01",
  display_label: "AAPL",
  lot_details: "Tax lot opened Jan 15, 2025 at $150.00/share",
  manual_review_required: false,
  manual_review_reason: "",
  action: "SELL",
  quantity: 10,
  current_price: 140,
  cost_basis_per_share: 150,
  estimated_loss: 100,
  tax_savings_estimate: 25,
  holding_period_days: 200,
  is_long_term: false,
  wash_sale_risk: false,
  wash_sale_explanation: "",
  replacement_candidates: [],
  ai_explanation: "",
  ai_generated: false,
  priority: 1,
};

describe("HarvestingSuggestions", () => {
  it("renders empty state when no suggestions", () => {
    render(<HarvestingSuggestions suggestions={[]} />);

    expect(
      screen.getByText(/No tax-loss harvesting opportunities found/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No open lots currently show an unrealized loss/),
    ).toBeInTheDocument();
  });

  it("does not claim all positions are at a gain when losing lots exist", () => {
    render(<HarvestingSuggestions suggestions={[]} lotsWithLosses={3} />);

    expect(
      screen.getByText(/Open lots with unrealized losses were found/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/All positions are currently at a gain/),
    ).not.toBeInTheDocument();
  });

  it("keeps a purchase-fill note on unlocked harvest cards", () => {
    const note = "Purchase price from your CSV, no live quote";
    render(
      <HarvestingSuggestions
        suggestions={[
          baseSuggestion,
          { ...baseSuggestion, suggestion_id: "AAPL-fill", price_note: note, estimated_loss: 0 },
        ]}
      />,
    );

    expect(screen.getByTestId("harvest-price-note")).toHaveTextContent(note);
    expect(screen.getByTestId("harvest-unknown-loss-count")).toHaveTextContent(
      "1 lot uses your CSV purchase price, no live quote, loss unknown.",
    );
    expect(screen.getAllByText("Harvest candidate")).toHaveLength(1);
    expect(
      screen.queryByText(
        "10 AAPL shares show a short-term loss you may be able to harvest.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  });

  it("keeps a purchase-fill note on locked harvest teasers", () => {
    const note = "Purchase price from your CSV, no live quote";
    render(
      <HarvestingSuggestions
        suggestions={[
          baseSuggestion,
          { ...baseSuggestion, suggestion_id: "AAPL-fill", price_note: note, estimated_loss: 133 },
        ]}
        locked
      />,
    );

    expect(screen.getByTestId("harvest-price-note")).toHaveTextContent(note);
    expect(screen.getByText(/1\s*lot\s*with unrealized losses/)).toBeInTheDocument();
    expect(screen.getByTestId("harvest-unknown-loss-count")).toHaveTextContent(
      "1 lot uses your CSV purchase price, no live quote, loss unknown.",
    );
    expect(screen.queryByText(/2 lots with unrealized losses/)).not.toBeInTheDocument();
    expect(screen.queryByText("Harvest candidate")).not.toBeInTheDocument();
    expect(screen.queryByText(/\bSell\b/)).not.toBeInTheDocument();
  });

  it("does not present a purchase-fill card as a sell recommendation", () => {
    const note = "Purchase price from your CSV, no live quote";
    const fill: HarvestingSuggestion = {
      ...baseSuggestion,
      suggestion_id: "AAPL-fill",
      action: "REVIEW",
      priority: 4,
      estimated_loss: 0,
      tax_savings_estimate: 0,
      price_note: note,
      ai_generated: true,
      ai_explanation: "Sell this fill lot now",
      replacement_candidates: [
        { symbol: "QQQ", name: "Invesco QQQ", reason: "Similar exposure" },
      ],
    };
    render(<HarvestingSuggestions suggestions={[baseSuggestion, fill]} />);

    expect(screen.getByTestId("harvest-price-note")).toHaveTextContent(note);
    expect(screen.getByTestId("harvest-unknown-loss-count")).toHaveTextContent(
      "1 lot uses your CSV purchase price, no live quote, loss unknown.",
    );
    expect(screen.getAllByText("Harvest candidate")).toHaveLength(1);
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.queryByText("#4")).not.toBeInTheDocument();
    expect(screen.queryByText("AI")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Show more")).not.toBeInTheDocument();

    expect(screen.queryByText("Replacements")).not.toBeInTheDocument();
    expect(screen.queryByText("AI Analysis")).not.toBeInTheDocument();
    expect(screen.queryByText("Sell this fill lot now")).not.toBeInTheDocument();
    expect(screen.queryByText("Invesco QQQ")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/this sale is less likely to be a wash sale/),
    ).not.toBeInTheDocument();
  });

  it("shows packet preview instead of sell recipes when locked", () => {
    render(
      <HarvestingSuggestions
        suggestions={[baseSuggestion]}
        lotsWithLosses={1}
        locked
      />,
    );

    expect(screen.getByTestId("harvest-packet-preview")).toBeInTheDocument();
    expect(screen.getByTestId("harvest-teaser-card")).toBeInTheDocument();
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("Open loss")).toBeInTheDocument();
    expect(screen.queryByText("Harvest candidate")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "10 AAPL shares show a short-term loss you may be able to harvest.",
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/\bSell\b/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Unlock harvest plan — \$49/ }),
    ).toBeInTheDocument();
  });

  it("previews losing positions when the engine returned no suggestions", () => {
    render(
      <HarvestingSuggestions
        suggestions={[]}
        lotsWithLosses={1}
        locked
        positions={[
          {
            symbol: "NVDA",
            display_label: "NVDA",
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
            tax_lots: [],
            wash_sale_risk: true,
          },
        ]}
      />,
    );

    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.getByText("31-day wait")).toBeInTheDocument();
    expect(
      screen.queryByText(/No tax-loss harvesting opportunities found/),
    ).not.toBeInTheDocument();
  });

  it("does not preview a TSLA or SPCX loss as a harvest prompt", () => {
    render(
      <HarvestingSuggestions
        suggestions={[]}
        positions={[
          {
            symbol: "TSLA",
            display_label: "TSLA",
            quantity: 4,
            avg_cost_basis: 365,
            total_cost_basis: 1460,
            current_price: 355,
            market_value: 1420,
            unrealized_pnl: -40,
            unrealized_pnl_pct: -2.7,
            earliest_purchase_date: "2026-04-08",
            holding_period_days: 100,
            is_long_term: false,
            asset_type: "stock",
            tax_lots: [],
            wash_sale_risk: false,
          },
          {
            symbol: "SPCX",
            display_label: "SPCX",
            quantity: 2,
            avg_cost_basis: 20,
            total_cost_basis: 40,
            current_price: 10,
            market_value: 20,
            unrealized_pnl: -20,
            unrealized_pnl_pct: -50,
            earliest_purchase_date: "2026-01-02",
            holding_period_days: 200,
            is_long_term: false,
            asset_type: "stock",
            tax_lots: [],
            wash_sale_risk: false,
          },
        ]}
      />,
    );

    expect(screen.queryByText("TSLA")).not.toBeInTheDocument();
    expect(screen.queryByText("SPCX")).not.toBeInTheDocument();
    expect(screen.queryByText(/Sell/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/No tax-loss harvesting opportunities found/),
    ).toBeInTheDocument();
  });

  it("renders suggestion cards for each suggestion", () => {
    const suggestions: HarvestingSuggestion[] = [
      baseSuggestion,
      {
        ...baseSuggestion,
        symbol: "MSFT",
        suggestion_id: "MSFT-stock-2025-01-02",
        display_label: "MSFT",
        lot_details: "Tax lot opened Jan 16, 2025 at $150.00/share",
        priority: 2,
      },
    ];
    render(<HarvestingSuggestions suggestions={suggestions} />);

    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
  });

  it("shows priority badge", () => {
    render(<HarvestingSuggestions suggestions={[baseSuggestion]} />);

    expect(screen.getByText("#1")).toBeInTheDocument();
  });

  it("shows per-lot details for repeated symbols", () => {
    const suggestions: HarvestingSuggestion[] = [
      baseSuggestion,
      {
        ...baseSuggestion,
        suggestion_id: "AAPL-stock-2025-02-01",
        lot_details: "Tax lot opened Feb 01, 2025 at $155.00/share",
        cost_basis_per_share: 155,
        priority: 2,
      },
    ];

    render(<HarvestingSuggestions suggestions={suggestions} />);

    expect(screen.getByText("Lot 1/2")).toBeInTheDocument();
    expect(screen.getByText("Lot 2/2")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Lot 1 of 2 • Tax lot opened Jan 15, 2025 at $150.00/share",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Lot 2 of 2 • Tax lot opened Feb 01, 2025 at $155.00/share",
      ),
    ).toBeInTheDocument();
  });

  it("shows manual-review context for affected symbols", () => {
    const requiresReview: HarvestingSuggestion = {
      ...baseSuggestion,
      manual_review_required: true,
      manual_review_reason:
        "Recent stock split activity affected AAPL. Verify reported quantities, adjusted contracts, and cost basis manually before acting.",
    };

    render(<HarvestingSuggestions suggestions={[requiresReview]} />);

    expect(screen.getByText("Manual review")).toBeInTheDocument();
    expect(
      screen.getByText(/Recent stock split activity affected AAPL/i),
    ).toBeInTheDocument();
  });

  it("shows Short-Term chip for short holding period", () => {
    render(<HarvestingSuggestions suggestions={[baseSuggestion]} />);

    expect(screen.getByText("Short-Term")).toBeInTheDocument();
  });

  it("shows Long-Term chip for long holding period", () => {
    const longTerm = { ...baseSuggestion, is_long_term: true };
    render(<HarvestingSuggestions suggestions={[longTerm]} />);

    expect(screen.getByText("Long-Term")).toBeInTheDocument();
  });

  it("shows AI badge when ai_generated is true", () => {
    const aiSuggestion = { ...baseSuggestion, ai_generated: true };
    render(<HarvestingSuggestions suggestions={[aiSuggestion]} />);

    expect(screen.getByText("AI")).toBeInTheDocument();
  });

  it("does not show AI badge when ai_generated is false", () => {
    render(<HarvestingSuggestions suggestions={[baseSuggestion]} />);

    expect(screen.queryByText("AI")).not.toBeInTheDocument();
  });

  it("displays estimated loss and tax savings", () => {
    render(<HarvestingSuggestions suggestions={[baseSuggestion]} />);

    expect(screen.getByText("$100")).toBeInTheDocument();
    expect(screen.getByText("$25")).toBeInTheDocument();
  });

  it("states the harvest note without a trade instruction or replacement ticker", () => {
    const withCandidates: HarvestingSuggestion = {
      ...baseSuggestion,
      quantity: 40,
      estimated_loss: 9538,
      tax_savings_estimate: 2098,
      replacement_candidates: [
        {
          symbol: "SMH",
          name: "VanEck Semiconductor ETF",
          reason: "Similar exposure",
        },
        {
          symbol: "SOXX",
          name: "iShares Semiconductor ETF",
          reason: "Semiconductor index",
        },
      ],
    };
    render(<HarvestingSuggestions suggestions={[withCandidates]} />);

    expect(screen.getByText("Harvest candidate")).toBeInTheDocument();
    expect(screen.getByText("Harvest note")).toBeInTheDocument();
    expect(
      screen.getByText(
        "40 AAPL shares show a short-term loss you may be able to harvest.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Estimated loss \$9,538/)).toBeInTheDocument();
    expect(screen.getByText(/Estimated tax savings \$2,098/)).toBeInTheDocument();
    expect(
      screen.getByText(/Buying the same stock or its options within 30 days/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/year-end harvest check/i)).toBeInTheDocument();
    expect(screen.queryByText(/SMH|SOXX|VanEck/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bSell\b/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bBuy\b/)).not.toBeInTheDocument();
  });

  it("does not tell the reader to sell TSLA or SPCX", () => {
    const tsla = getRecommendedActionCopy({
      ...baseSuggestion,
      symbol: "TSLA",
      display_label: "TSLA",
    });
    expect(tsla.headline).toBe("A wash-sale rule may apply, ask a tax pro.");
    expect(tsla.headline.toLowerCase()).not.toContain("sell");
    expect(tsla.detail.toLowerCase()).not.toContain("sell");

    render(
      <HarvestingSuggestions
        suggestions={[
          {
            ...baseSuggestion,
            symbol: "SPCX",
            display_label: "SPCX",
            suggestion_id: "SPCX-1",
          },
        ]}
      />,
    );
    expect(screen.queryByText("Harvest candidate")).not.toBeInTheDocument();
    expect(screen.queryByText(/Sell \d+ SPCX/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/SPCX shares show/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("A wash-sale rule may apply, ask a tax pro."),
    ).toBeInTheDocument();
  });

  it("builds wash-sale-aware action copy", () => {
    const copy = getRecommendedActionCopy({
      ...baseSuggestion,
      wash_sale_risk: true,
      estimated_loss: 300,
      tax_savings_estimate: 66,
    });
    expect(copy.headline).toBe(
      "10 AAPL shares show a short-term loss you may be able to harvest.",
    );
    expect(copy.headline).not.toMatch(/\bSell\b|\bBuy\b/);
    expect(copy.detail).toMatch(/wash sale may apply/i);
    expect(copy.detail).toMatch(/Estimated loss \$300/);
    expect(copy.detail).toMatch(/Estimated tax savings \$66/);
    expect(copy.detail).toMatch(/year-end harvest check/);
    expect(copy.detail).not.toMatch(/\bSell\b|\bBuy\b/);
  });

  it("shows wash-sale risk warning when flagged", () => {
    const washRisk: HarvestingSuggestion = {
      ...baseSuggestion,
      wash_sale_risk: true,
      wash_sale_explanation: "Repurchased within 30 days",
    };
    render(<HarvestingSuggestions suggestions={[washRisk]} />);

    expect(screen.getByText("Repurchased within 30 days")).toBeInTheDocument();
  });

  it("does not show a sell instruction inside the wash warning", () => {
    render(
      <HarvestingSuggestions
        suggestions={[
          {
            ...baseSuggestion,
            wash_sale_risk: true,
            wash_sale_explanation:
              "Selling now and repurchasing within 30 days would disallow the loss.",
          },
        ]}
      />,
    );
    expect(screen.queryByText(/Selling now/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/A wash sale may apply. See the year-end harvest check/),
    ).toBeInTheDocument();
  });

  it("hides wash-sale warning when not flagged", () => {
    render(<HarvestingSuggestions suggestions={[baseSuggestion]} />);

    expect(
      screen.queryByText(/Wash-sale risk detected/),
    ).not.toBeInTheDocument();
  });

  it("expands card details on click", () => {
    const withDetails: HarvestingSuggestion = {
      ...baseSuggestion,
      ai_explanation: "Consider selling to harvest losses",
      replacement_candidates: [
        { symbol: "SPY", name: "S&P 500 ETF", reason: "Broad market exposure" },
      ],
    };
    render(<HarvestingSuggestions suggestions={[withDetails]} />);

    // Details should not be visible initially
    expect(
      screen.queryByText("Consider selling to harvest losses"),
    ).not.toBeVisible();

    // Click expand button
    fireEvent.click(screen.getByLabelText("Show more"));

    // Details should now be visible
    expect(
      screen.getByText("Consider selling to harvest losses"),
    ).toBeVisible();
  });

  it("does not name replacement tickers when the card is expanded", () => {
    const withCandidates: HarvestingSuggestion = {
      ...baseSuggestion,
      ai_explanation: "This lot is already at a loss.",
      replacement_candidates: [
        {
          symbol: "VTI",
          name: "Vanguard Total Market",
          reason: "Similar exposure",
        },
      ],
    };
    render(<HarvestingSuggestions suggestions={[withCandidates]} />);

    fireEvent.click(screen.getByLabelText("Show more"));

    expect(screen.getByText("This lot is already at a loss.")).toBeVisible();
    expect(screen.queryByText("VTI")).not.toBeInTheDocument();
    expect(screen.queryByText("Vanguard Total Market")).not.toBeInTheDocument();
    expect(screen.queryByText("Similar exposure")).not.toBeInTheDocument();
    expect(screen.queryByText("Replacements")).not.toBeInTheDocument();
  });

  it("says harvest candidates use CSV prices", () => {
    const basis: PriceBasis = {
      quotes: [
        {
          symbol: "AAPL",
          price: 140,
          source: "csv",
          provider: null,
          as_of: "2026-08-12",
        },
      ],
      live: false,
      csv_as_of: "2026-08-12",
      label: "Prices as of your CSV (2026-08-12)",
    };
    const { rerender } = render(
      <HarvestingSuggestions
        suggestions={[baseSuggestion]}
        priceBasis={basis}
      />,
    );
    expect(screen.getByTestId("price-as-of-harvest")).toHaveTextContent(
      "Prices as of your CSV (2026-08-12)",
    );
    expect(screen.queryByText(/^Live /)).not.toBeInTheDocument();

    rerender(
      <HarvestingSuggestions
        suggestions={[baseSuggestion]}
        locked
        priceBasis={basis}
      />,
    );
    expect(screen.getByTestId("price-as-of-harvest")).toHaveTextContent(
      "Prices as of your CSV (2026-08-12)",
    );
  });

  it("collapses details on second click", () => {
    const withDetails: HarvestingSuggestion = {
      ...baseSuggestion,
      ai_explanation: "Explanation text here",
    };
    render(<HarvestingSuggestions suggestions={[withDetails]} />);

    // Expand
    fireEvent.click(screen.getByLabelText("Show more"));
    expect(screen.getByText("Explanation text here")).toBeVisible();

    // Collapse
    fireEvent.click(screen.getByLabelText("Show less"));
    // After collapse, the MUI Collapse component hides content
  });
});
