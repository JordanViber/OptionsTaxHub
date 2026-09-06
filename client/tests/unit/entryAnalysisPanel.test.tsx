import { fireEvent, render, screen } from "@testing-library/react";
import EntryAnalysisPanel from "../../app/components/EntryAnalysisPanel";
import type { Position } from "../../lib/types";

function futureIso(): string {
  const date = new Date();
  date.setFullYear(date.getFullYear() + 1);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function fillLongCall() {
  fireEvent.change(screen.getByTestId("entry-symbol"), {
    target: { value: "NVDA" },
  });
  fireEvent.change(screen.getByTestId("entry-strike"), {
    target: { value: "250" },
  });
  fireEvent.change(screen.getByTestId("entry-expiration"), {
    target: { value: futureIso() },
  });
  fireEvent.change(screen.getByTestId("entry-premium"), {
    target: { value: "4.20" },
  });
}

const nvdaStock: Position = {
  position_id: "NVDA:stock",
  symbol: "NVDA",
  display_label: "NVDA",
  quantity: 52,
  avg_cost_basis: 280,
  total_cost_basis: 14560,
  current_price: 250,
  market_value: 13000,
  unrealized_pnl: -1560,
  unrealized_pnl_pct: -10,
  earliest_purchase_date: "2026-01-15",
  holding_period_days: 200,
  is_long_term: false,
  asset_type: "stock",
  tax_lots: [],
  wash_sale_risk: false,
};

describe("EntryAnalysisPanel", () => {
  it("renders Analyze a new option with no analysis and no packet unlock", () => {
    render(<EntryAnalysisPanel />);

    expect(screen.getByTestId("entry-analysis-panel")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Analyze a new option/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/What-if · single-leg/i)).toBeInTheDocument();
    expect(screen.getByTestId("entry-empty")).toHaveTextContent(
      /Enter premium to see max gain, max loss, and breakeven/i,
    );
    expect(screen.queryByTestId("entry-results")).not.toBeInTheDocument();
    expect(screen.queryByTestId("entry-context")).not.toBeInTheDocument();
    expect(
      screen.getByText(/not a filed Form 8949/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/not the year-close packet/i)).toBeInTheDocument();
  });

  it("stacks fields instead of using a table", () => {
    render(<EntryAnalysisPanel />);
    expect(screen.getByTestId("entry-analysis-stack")).toBeInTheDocument();
    expect(
      screen.getByTestId("entry-analysis-panel").querySelector("table"),
    ).toBeNull();
  });

  it("shows max loss = premium paid for a long call", () => {
    render(<EntryAnalysisPanel />);
    fillLongCall();

    expect(screen.getByTestId("entry-results")).toBeInTheDocument();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
    expect(screen.getByTestId("entry-max-gain")).toHaveTextContent("Unlimited");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$254.20");
    expect(screen.queryByTestId("entry-collateral")).not.toBeInTheDocument();
  });

  it("shows cash-secured collateral for a short put", () => {
    render(<EntryAnalysisPanel />);
    fireEvent.click(screen.getByTestId("entry-right-put"));
    fireEvent.click(screen.getByTestId("entry-side-sell"));
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "AMD" },
    });
    fireEvent.change(screen.getByTestId("entry-strike"), {
      target: { value: "50" },
    });
    fireEvent.change(screen.getByTestId("entry-expiration"), {
      target: { value: futureIso() },
    });
    fireEvent.change(screen.getByTestId("entry-premium"), {
      target: { value: "2" },
    });

    expect(screen.getByTestId("entry-max-gain")).toHaveTextContent("$200.00");
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$4,800.00");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$48.00");
    expect(screen.getByTestId("entry-collateral")).toHaveTextContent(
      /Cash-secured: about \$5,000\.00/,
    );
  });

  it("shows an honest error for a past expiration", () => {
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.change(screen.getByTestId("entry-strike"), {
      target: { value: "250" },
    });
    fireEvent.change(screen.getByTestId("entry-expiration"), {
      target: { value: "2020-01-16" },
    });
    fireEvent.change(screen.getByTestId("entry-premium"), {
      target: { value: "4.20" },
    });

    expect(screen.getByTestId("entry-error")).toHaveTextContent(
      /Expiration is in the past/,
    );
    expect(screen.queryByTestId("entry-results")).not.toBeInTheDocument();
  });

  it("does not show portfolio context when no positions are loaded", () => {
    render(<EntryAnalysisPanel positions={[]} />);
    fillLongCall();
    expect(screen.queryByTestId("entry-context")).not.toBeInTheDocument();
  });

  it("shows one-line portfolio context when positions are loaded", () => {
    render(<EntryAnalysisPanel positions={[nvdaStock]} />);
    fillLongCall();
    expect(screen.getByTestId("entry-context")).toHaveTextContent(
      "Open NVDA: 52 sh",
    );
    expect(screen.getByTestId("entry-context")).not.toHaveTextContent(
      /wash|1099|harvest|\$49/i,
    );
  });

  it("ignores a second click that would clear Call/Put or Buy/Sell", () => {
    render(<EntryAnalysisPanel />);
    fireEvent.click(screen.getByTestId("entry-right-call"));
    fireEvent.click(screen.getByTestId("entry-side-buy"));
    fillLongCall();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
  });

  it("shows unlimited max loss for a short call", () => {
    render(<EntryAnalysisPanel />);
    fireEvent.click(screen.getByTestId("entry-side-sell"));
    fillLongCall();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("Unlimited");
    expect(screen.getByTestId("entry-max-gain")).toHaveTextContent("$420.00");
    expect(screen.getByTestId("entry-collateral")).toHaveTextContent(
      /Naked call: broker margin/,
    );
  });

  it("treats non-numeric premium as incomplete", () => {
    render(<EntryAnalysisPanel />);
    fillLongCall();
    fireEvent.change(screen.getByTestId("entry-premium"), {
      target: { value: "abc" },
    });
    expect(screen.getByTestId("entry-empty")).toHaveTextContent(
      /Enter premium to see max gain, max loss, and breakeven/i,
    );
  });

  it("rejects a fractional quantity as invalid", () => {
    render(<EntryAnalysisPanel />);
    fillLongCall();
    fireEvent.change(screen.getByTestId("entry-quantity"), {
      target: { value: "1.5" },
    });
    expect(screen.getByTestId("entry-error")).toHaveTextContent(
      /Strike, quantity, and premium must be valid numbers/,
    );
  });
});
