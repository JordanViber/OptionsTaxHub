import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import YearClosePacketPanel, {
  YEAR_CLOSE_PACKET_COPY,
  YEAR_CLOSE_PACKET_TITLE,
} from "../../app/components/YearClosePacketPanel";
import type { PortfolioAnalysis } from "../../lib/types";

const mockFetch = jest.fn();
globalThis.fetch = mockFetch;

const analysis: PortfolioAnalysis = {
  analysis_id: "analysis-1",
  positions: [],
  tax_lots: [
    {
      symbol: "AMD",
      quantity: 10,
      cost_basis_per_share: 125,
      total_cost_basis: 1250,
      purchase_date: "2025-07-25",
      current_price: 125,
      asset_type: "stock",
      unrealized_pnl: 0,
      unrealized_pnl_pct: 0,
      holding_period_days: 10,
      is_long_term: false,
      wash_sale_disallowed: 300,
    },
  ],
  suggestions: [],
  wash_sale_flags: [],
  summary: {
    total_market_value: 0,
    total_cost_basis: 0,
    total_unrealized_pnl: 0,
    total_unrealized_pnl_pct: 0,
    total_harvestable_losses: 0,
    estimated_tax_savings: 0,
    positions_count: 1,
    lots_with_losses: 0,
    lots_with_gains: 0,
    wash_sale_flags_count: 0,
  },
  tax_profile: {
    filing_status: "single",
    estimated_annual_income: 75000,
    state: "",
    tax_year: 2025,
  },
  supplemental_1099: null,
  disclaimer: "test",
  errors: [],
  warnings: [],
};

describe("YearClosePacketPanel", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn(() => null),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
    globalThis.URL.createObjectURL = jest.fn(() => "blob:packet");
    globalThis.URL.revokeObjectURL = jest.fn();
  });

  it("shows the $49 year-close packet, not tip tiers", () => {
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(screen.getByText(YEAR_CLOSE_PACKET_TITLE)).toBeInTheDocument();
    expect(screen.getByText(/reconciliation packet, not a filed Form 8949/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download/i })).toBeInTheDocument();
    expect(screen.queryByText("Coffee")).not.toBeInTheDocument();
    expect(screen.queryByText("Buy us a coffee")).not.toBeInTheDocument();
  });

  it("starts packet checkout, not tips checkout", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          checkout_url: "https://checkout.stripe.com/c/pay/cs_test_packet",
        }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/year-close-packet/checkout"),
        expect.objectContaining({ method: "POST" }),
      );
    });
    const init = mockFetch.mock.calls[0][1];
    expect(JSON.parse(init.body).analysis_id).toBe("analysis-1");
    expect(mockFetch.mock.calls[0][0]).not.toContain("/api/tips/checkout");
  });

  it("unpaid download shows a blocked error from 403", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ detail: "Year-close packet download requires payment." }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Pay \$49 to download the year-close packet/i,
      );
    });
  });
});
