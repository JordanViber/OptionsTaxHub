import { render, screen } from "@testing-library/react";
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

    expect(
      screen.getByText("Wash-Sale Rule Violations Detected (2)"),
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

    expect(screen.getByText(/AAPL: \$500 loss disallowed/)).toBeInTheDocument();
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

    expect(
      screen.getByText(/AAPL: \$500 loss disallowed/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/TSLA: \$1,200 loss disallowed/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Repurchased identical security"),
    ).toBeInTheDocument();
  });
});
