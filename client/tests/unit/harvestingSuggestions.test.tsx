import { render, screen, fireEvent } from "@testing-library/react";
import HarvestingSuggestions from "../../app/components/HarvestingSuggestions";
import type { HarvestingSuggestion } from "../../lib/types";

const baseSuggestion: HarvestingSuggestion = {
  symbol: "AAPL",
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
  });

  it("renders suggestion cards for each suggestion", () => {
    const suggestions: HarvestingSuggestion[] = [
      baseSuggestion,
      { ...baseSuggestion, symbol: "MSFT", priority: 2 },
    ];
    render(<HarvestingSuggestions suggestions={suggestions} />);

    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
  });

  it("shows priority badge", () => {
    render(<HarvestingSuggestions suggestions={[baseSuggestion]} />);

    expect(screen.getByText("#1")).toBeInTheDocument();
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
        { symbol: "VTI", name: "Vanguard Total Market", reason: "Similar exposure" },
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
