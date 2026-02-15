import { render, screen } from "@testing-library/react";
import PortfolioSummaryCards from "../../app/components/PortfolioSummaryCards";
import type { PortfolioSummary } from "../../lib/types";

const baseSummary: PortfolioSummary = {
  total_market_value: 50000,
  total_cost_basis: 55000,
  total_unrealized_pnl: -5000,
  total_unrealized_pnl_pct: -9.1,
  total_harvestable_losses: 3000,
  estimated_tax_savings: 750,
  positions_count: 5,
  lots_with_losses: 3,
  lots_with_gains: 2,
  wash_sale_flags_count: 0,
};

describe("PortfolioSummaryCards", () => {
  it("renders all four metric cards", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("Portfolio Value")).toBeInTheDocument();
    expect(screen.getByText("Unrealized P&L")).toBeInTheDocument();
    expect(screen.getByText("Harvestable Losses")).toBeInTheDocument();
    expect(screen.getByText("Est. Tax Savings")).toBeInTheDocument();
  });

  it("displays formatted market value", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("$50,000")).toBeInTheDocument();
  });

  it("displays position count subtitle", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("5 positions")).toBeInTheDocument();
  });

  it("shows negative P&L with correct format", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("-$5,000")).toBeInTheDocument();
    expect(screen.getByText("-9.1%")).toBeInTheDocument();
  });

  it("shows positive P&L with plus sign", () => {
    const gainSummary: PortfolioSummary = {
      ...baseSummary,
      total_unrealized_pnl: 2000,
      total_unrealized_pnl_pct: 4.2,
    };
    render(<PortfolioSummaryCards summary={gainSummary} />);

    expect(screen.getByText("+$2,000")).toBeInTheDocument();
    expect(screen.getByText("+4.2%")).toBeInTheDocument();
  });

  it("shows lots with losses count", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("3 lots with losses")).toBeInTheDocument();
  });

  it("shows 'From harvesting losses' when no wash-sale flags", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("From harvesting losses")).toBeInTheDocument();
  });

  it("shows wash-sale warning count when flags exist", () => {
    const flagsSummary: PortfolioSummary = {
      ...baseSummary,
      wash_sale_flags_count: 2,
    };
    render(<PortfolioSummaryCards summary={flagsSummary} />);

    expect(screen.getByText("2 wash-sale warning(s)")).toBeInTheDocument();
  });

  it("displays tax savings amount", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("$750")).toBeInTheDocument();
  });

  it("displays harvestable losses amount", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.getByText("$3,000")).toBeInTheDocument();
  });
});
