import { render, screen } from "@testing-library/react";
import PortfolioSummaryCards from "../../app/components/PortfolioSummaryCards";
import type { PortfolioSummary, RealizedSummary } from "../../lib/types";

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

  it("does not render realized card when realized_summary is absent", () => {
    render(<PortfolioSummaryCards summary={baseSummary} />);

    expect(screen.queryByText(/Realized/)).not.toBeInTheDocument();
    expect(screen.queryByText(/trades/)).not.toBeInTheDocument();
  });
});

describe("PortfolioSummaryCards — RealizedCard", () => {
  const positiveRealized: RealizedSummary = {
    tax_year: 2025,
    st_gains: 3000,
    st_losses: -500,
    lt_gains: 5000,
    lt_losses: -1000,
    net_st: 2500,
    net_lt: 4000,
    total_net: 6500,
    transactions_count: 15,
  };

  const negativeRealized: RealizedSummary = {
    tax_year: 2025,
    st_gains: 0,
    st_losses: -2000,
    lt_gains: 0,
    lt_losses: -3500,
    net_st: -2000,
    net_lt: -3500,
    total_net: -5500,
    transactions_count: 8,
  };

  it("renders realized card when realized_summary is present", () => {
    render(
      <PortfolioSummaryCards
        summary={{ ...baseSummary, realized_summary: positiveRealized }}
      />,
    );

    expect(screen.getByText("2025 Realized")).toBeInTheDocument();
    expect(screen.getByText("15 trades")).toBeInTheDocument();
  });

  it("shows correct net amount for positive realized gains", () => {
    render(
      <PortfolioSummaryCards
        summary={{ ...baseSummary, realized_summary: positiveRealized }}
      />,
    );

    // +$6,500 net realized at top
    expect(screen.getByText("+$6,500")).toBeInTheDocument();
  });

  it("shows ST and LT breakdown", () => {
    render(
      <PortfolioSummaryCards
        summary={{ ...baseSummary, realized_summary: positiveRealized }}
      />,
    );

    // "ST:" and "LT:" labels with values
    expect(screen.getByText(/ST:/)).toBeInTheDocument();
    expect(screen.getByText(/LT:/)).toBeInTheDocument();
  });

  it("shows negative net realized without plus sign", () => {
    render(
      <PortfolioSummaryCards
        summary={{ ...baseSummary, realized_summary: negativeRealized }}
      />,
    );

    // -$5,500 net (no plus prefix on negative)
    expect(screen.getByText("-$5,500")).toBeInTheDocument();
    expect(screen.getByText("8 trades")).toBeInTheDocument();
  });

  it("renders when total_net is exactly zero", () => {
    const zeroRealized: RealizedSummary = {
      ...positiveRealized,
      total_net: 0,
      net_st: 0,
      net_lt: 0,
      transactions_count: 3,
    };

    render(
      <PortfolioSummaryCards
        summary={{ ...baseSummary, realized_summary: zeroRealized }}
      />,
    );

    // Renders the realized card (zero is >= 0, so "+" prefix, $0 value)
    expect(screen.getByText("3 trades")).toBeInTheDocument();
    expect(screen.getByText("2025 Realized")).toBeInTheDocument();
  });
});
