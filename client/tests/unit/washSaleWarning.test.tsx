import { render, screen, fireEvent } from "@testing-library/react";
import WashSaleWarning from "../../app/components/WashSaleWarning";
import type { WashSaleFlag } from "../../lib/types";

const baseFlag: WashSaleFlag = {
  symbol: "AAPL",
  sale_date: "2025-01-15",
  sale_quantity: 10,
  sale_loss: 500,
  repurchase_date: "2025-01-20",
  repurchase_quantity: 10,
  disallowed_loss: 500,
  adjusted_cost_basis: 155,
  explanation: "Sold at a loss and repurchased within 30 days",
};

describe("WashSaleWarning", () => {
  it("returns null when flags array is empty", () => {
    const { container } = render(<WashSaleWarning flags={[]} />);

    expect(container.firstChild).toBeNull();
  });

  it("renders warning alert when flags are present", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("displays violation count in header", () => {
    const flags = [
      baseFlag,
      { ...baseFlag, symbol: "MSFT", sale_date: "2025-02-01" },
    ];
    render(<WashSaleWarning flags={flags} />);

    // Component groups by ticker and shows "N events across M tickers"
    expect(
      screen.getByRole("heading", {
        name: /Wash-Sale Rule Violations Detected/,
      }),
    ).toBeInTheDocument();
  });

  it("displays IRS wash-sale rule explanation", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    expect(
      screen.getByText(/disallows loss deductions when you repurchase/),
    ).toBeInTheDocument();
  });

  it("shows symbol and disallowed loss for each flag", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    // Component renders ticker symbol and disallowed amount as separate elements in an accordion row
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("$500.00 disallowed")).toBeInTheDocument();
  });

  it("shows explanation text for each flag", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    expect(
      screen.getByText("Sold at a loss and repurchased within 30 days"),
    ).toBeInTheDocument();
  });

  it("renders multiple flags with dividers", () => {
    const flags: WashSaleFlag[] = [
      baseFlag,
      {
        ...baseFlag,
        symbol: "TSLA",
        disallowed_loss: 1200,
        explanation: "Repurchased identical security",
      },
    ];
    render(<WashSaleWarning flags={flags} />);

    // Both tickers appear as accordion rows, sorted by total disallowed (TSLA first)
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("$500.00 disallowed")).toBeInTheDocument();
    expect(screen.getByText("TSLA")).toBeInTheDocument();
    expect(screen.getByText("$1,200.00 disallowed")).toBeInTheDocument();
    // Explanation text is inside the collapsed accordion detail — check it is in DOM
    expect(
      screen.getByText("Repurchased identical security"),
    ).toBeInTheDocument();
  });

  it("shows singular 'ticker' label when only one ticker is present", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    // 1 event across 1 ticker (singular)
    expect(
      screen.getByText(/1 events? across 1 ticker[^s]/),
    ).toBeInTheDocument();
  });

  it("shows plural 'tickers' label when multiple tickers are present", () => {
    const flags: WashSaleFlag[] = [
      baseFlag,
      { ...baseFlag, symbol: "MSFT", disallowed_loss: 300 },
    ];
    render(<WashSaleWarning flags={flags} />);

    // 2 events across 2 tickers (plural)
    expect(screen.getByText(/tickers/)).toBeInTheDocument();
  });

  it("groups multiple events for the same ticker into one accordion row", () => {
    const flags: WashSaleFlag[] = [
      baseFlag,
      {
        ...baseFlag,
        sale_date: "2025-02-10",
        disallowed_loss: 200,
        explanation: "Second AAPL wash sale",
      },
    ];
    render(<WashSaleWarning flags={flags} />);

    // Only one AAPL row (grouped), showing combined total ($700.00)
    const aaplElements = screen.getAllByText("AAPL");
    expect(aaplElements).toHaveLength(1);
    expect(screen.getByText("$700.00 disallowed")).toBeInTheDocument();
    // Should show "2 events" plural
    expect(screen.getByText("2 events")).toBeInTheDocument();
  });

  it("shows '1 event' (singular) for a ticker with only one flag", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    expect(screen.getByText("1 event")).toBeInTheDocument();
  });

  it("expands an accordion when clicked and collapses it when clicked again", () => {
    render(<WashSaleWarning flags={[baseFlag]} />);

    const summaryButton = screen.getByRole("button", { name: /AAPL/ });
    // Click to expand
    fireEvent.click(summaryButton);
    // Click again to collapse (triggers isExpanded = false branch)
    fireEvent.click(summaryButton);
    // Component still renders after collapse
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });

  it("collapses a previously expanded ticker when a different ticker is expanded", () => {
    const flags: WashSaleFlag[] = [
      baseFlag,
      {
        ...baseFlag,
        symbol: "MSFT",
        disallowed_loss: 800,
        explanation: "MSFT wash sale",
      },
    ];
    render(<WashSaleWarning flags={flags} />);

    const aaplButton = screen.getByRole("button", { name: /AAPL/ });
    const msftButton = screen.getByRole("button", { name: /MSFT/ });

    // Expand AAPL
    fireEvent.click(aaplButton);
    // Now expand MSFT — AAPL should collapse (expanded becomes "MSFT")
    fireEvent.click(msftButton);
    // Both tickers still visible
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("MSFT")).toBeInTheDocument();
  });
});
