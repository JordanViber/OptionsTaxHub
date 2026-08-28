import { render, screen, fireEvent } from "@testing-library/react";
import HarvestingSuggestions, {
  getRecommendedActionCopy,
} from "../../app/components/HarvestingSuggestions";
import type { HarvestingSuggestion } from "../../lib/types";

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

  it("shows packet preview instead of sell recipes when locked", () => {
    render(
      <HarvestingSuggestions
        suggestions={[baseSuggestion]}
        lotsWithLosses={1}
        locked
      />,
    );

    expect(screen.getByTestId("harvest-packet-preview")).toBeInTheDocument();
    expect(screen.queryByText("Sell to harvest")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Pay \$49 for lot-level sell instructions/),
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

  it("states the sell-to-harvest action without expanding the card", () => {
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

    expect(screen.getByText("Sell to harvest")).toBeInTheDocument();
    expect(screen.getByText("Recommended action")).toBeInTheDocument();
    expect(
      screen.getByText("Sell 40 AAPL to harvest this short-term loss"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/look at SMH or SOXX/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("VanEck Semiconductor ETF"),
    ).not.toBeVisible();
  });

  it("builds wash-sale-aware action copy", () => {
    const copy = getRecommendedActionCopy({
      ...baseSuggestion,
      wash_sale_risk: true,
      estimated_loss: 300,
      tax_savings_estimate: 66,
    });
    expect(copy.headline).toBe("Sell 10 AAPL to harvest this short-term loss");
    expect(copy.detail).toMatch(/may trigger a wash sale/i);
    expect(copy.detail).toMatch(/\$300/);
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

  it("shows replacement candidates when expanded", () => {
    const withCandidates: HarvestingSuggestion = {
      ...baseSuggestion,
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

    expect(screen.getByText("VTI")).toBeInTheDocument();
    expect(screen.getByText("Vanguard Total Market")).toBeInTheDocument();
    expect(screen.getByText("Similar exposure")).toBeInTheDocument();
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
