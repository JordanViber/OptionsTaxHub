import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import YearClosePacketPanel, {
  compactAnalysis,
  purchaseDateFromSuggestionId,
  PACKET_CHECKOUT_CANCELED_COPY,
  PACKET_CHECKOUT_INFLIGHT_KEY,
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
  const store: Record<string, string> = {};

  beforeEach(() => {
    mockFetch.mockReset();
    for (const key of Object.keys(store)) {
      delete store[key];
    }
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn((key: string) => store[key] ?? null),
        setItem: jest.fn((key: string, value: string) => {
          store[key] = value;
        }),
        removeItem: jest.fn((key: string) => {
          delete store[key];
        }),
        clear: jest.fn(() => {
          for (const key of Object.keys(store)) delete store[key];
        }),
      },
      writable: true,
    });
    window.history.replaceState(null, "", "/dashboard");
    globalThis.URL.createObjectURL = jest.fn(() => "blob:packet");
    globalThis.URL.revokeObjectURL = jest.fn();
  });

  it("shows the $49 year-close packet, not tip tiers", () => {
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(screen.getByText(YEAR_CLOSE_PACKET_TITLE)).toBeInTheDocument();
    expect(screen.getByText(/reconciliation packet, not a filed Form 8949/i)).toBeInTheDocument();
    expect(screen.getByText(/Lot-matched 1099-B is a worksheet for this run/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download/i })).toBeInTheDocument();
    expect(screen.queryByText("Coffee")).not.toBeInTheDocument();
    expect(screen.queryByText("Buy us a coffee")).not.toBeInTheDocument();
  });

  it("compact checkout payload includes harvest suggestions for reconstruct", () => {
    const withHarvest: PortfolioAnalysis = {
      ...analysis,
      suggestions: [
        {
          symbol: "TSLA",
          display_label: "TSLA",
          suggestion_id: "TSLA::stock::stock-lot::2025-01-01::250::1",
          lot_details: "Tax lot opened Jan 01, 2025 at $250.00/share",
          action: "SELL",
          quantity: 1,
          current_price: 200,
          cost_basis_per_share: 250,
          estimated_loss: 50,
          tax_savings_estimate: 12,
          holding_period_days: 120,
          is_long_term: false,
          wash_sale_risk: true,
          wash_sale_explanation: "Recent TSLA buy inside 30 days.",
          replacement_candidates: [],
          ai_explanation: "",
          ai_generated: false,
          priority: 1,
        },
      ],
    };
    const compact = compactAnalysis(withHarvest);
    expect(compact.suggestions).toHaveLength(1);
    expect(compact.suggestions[0]).toMatchObject({
      symbol: "TSLA",
      suggestion_id: "TSLA::stock::stock-lot::2025-01-01::250::1",
      lot_details: "Tax lot opened Jan 01, 2025 at $250.00/share",
      quantity: 1,
      purchase_date: "2025-01-01",
      tax_savings_estimate: 12,
      is_long_term: false,
      wash_sale_risk: true,
      wash_sale_explanation: "Recent TSLA buy inside 30 days.",
    });
  });

  it("compact checkout payload keeps two same-ticker lots distinct", () => {
    const amdLot = (
      id: string,
      details: string,
      costBasis: number,
    ) => ({
      symbol: "AMD",
      display_label: "AMD",
      suggestion_id: id,
      lot_details: details,
      action: "SELL" as const,
      quantity: 10,
      current_price: 90,
      cost_basis_per_share: costBasis,
      estimated_loss: 40,
      tax_savings_estimate: 10,
      holding_period_days: 120,
      is_long_term: false,
      wash_sale_risk: false,
      wash_sale_explanation: "",
      replacement_candidates: [],
      ai_explanation: "",
      ai_generated: false,
      priority: 1,
    });
    const compact = compactAnalysis({
      ...analysis,
      suggestions: [
        amdLot(
          "AMD::stock::stock-lot::2024-01-02::100::10",
          "Tax lot opened Jan 02, 2024 at $100.00/share",
          100,
        ),
        amdLot(
          "AMD::stock::stock-lot::2025-06-01::125::10",
          "Tax lot opened Jun 01, 2025 at $125.00/share",
          125,
        ),
      ],
    });
    expect(compact.suggestions).toHaveLength(2);
    expect(compact.suggestions[0].suggestion_id).not.toBe(
      compact.suggestions[1].suggestion_id,
    );
    expect(compact.suggestions[0].quantity).toBe(compact.suggestions[1].quantity);
    expect(compact.suggestions[0].is_long_term).toBe(false);
    expect(compact.suggestions[1].is_long_term).toBe(false);
    expect(compact.suggestions[0]).toMatchObject({
      symbol: "AMD",
      suggestion_id: "AMD::stock::stock-lot::2024-01-02::100::10",
      quantity: 10,
      purchase_date: "2024-01-02",
      cost_basis_per_share: 100,
      lot_details: "Tax lot opened Jan 02, 2024 at $100.00/share",
    });
    expect(compact.suggestions[1]).toMatchObject({
      symbol: "AMD",
      suggestion_id: "AMD::stock::stock-lot::2025-06-01::125::10",
      quantity: 10,
      purchase_date: "2025-06-01",
      cost_basis_per_share: 125,
      lot_details: "Tax lot opened Jun 01, 2025 at $125.00/share",
    });
  });

  it("compact payload keeps purchase date and suggestion_id when lot_details is empty", () => {
    const amdLot = (id: string, costBasis: number) => ({
      symbol: "AMD",
      display_label: "AMD",
      suggestion_id: id,
      lot_details: "",
      action: "SELL" as const,
      quantity: 10,
      current_price: 90,
      cost_basis_per_share: costBasis,
      estimated_loss: 40,
      tax_savings_estimate: 10,
      holding_period_days: 120,
      is_long_term: false,
      wash_sale_risk: false,
      wash_sale_explanation: "",
      replacement_candidates: [],
      ai_explanation: "",
      ai_generated: false,
      priority: 1,
    });
    const compact = compactAnalysis({
      ...analysis,
      suggestions: [
        amdLot("AMD::stock::stock-lot::2024-01-02::100::10", 100),
        amdLot("AMD::stock::stock-lot::2025-06-01::125::10", 125),
      ],
    });
    expect(compact.suggestions).toHaveLength(2);
    expect(compact.suggestions[0].lot_details).toBe("");
    expect(compact.suggestions[1].lot_details).toBe("");
    expect(compact.suggestions[0].quantity).toBe(10);
    expect(compact.suggestions[1].quantity).toBe(10);
    expect(compact.suggestions[0].purchase_date).toBe("2024-01-02");
    expect(compact.suggestions[1].purchase_date).toBe("2025-06-01");
    expect(compact.suggestions[0].suggestion_id).toBe(
      "AMD::stock::stock-lot::2024-01-02::100::10",
    );
    expect(compact.suggestions[1].suggestion_id).toBe(
      "AMD::stock::stock-lot::2025-06-01::125::10",
    );
  });

  it("parses purchase date from suggestion_id", () => {
    expect(
      purchaseDateFromSuggestionId(
        "AMD::stock::stock-lot::2024-01-02::100::10",
      ),
    ).toBe("2024-01-02");
    expect(purchaseDateFromSuggestionId("amd-lot-jan")).toBeUndefined();
    expect(purchaseDateFromSuggestionId("")).toBeUndefined();
  });

  it("skips a second $49 when the tax year is already unlocked", () => {
    render(
      <YearClosePacketPanel
        analysis={{
          ...analysis,
          packet_unlocked: true,
          packet_session_id: "cs_test_year",
        }}
      />,
    );
    expect(
      screen.getByText(/later updates this year stay included/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
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

  it("releases the Pay spinner when returning from a closed Stripe checkout", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          checkout_url: "https://checkout.stripe.com/c/pay/cs_test_packet",
        }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    const pay = screen.getByRole("button", { name: /Pay \$49/i });
    fireEvent.click(pay);

    await waitFor(() => {
      expect(pay).toBeDisabled();
    });
    expect(store[PACKET_CHECKOUT_INFLIGHT_KEY]).toBe("analysis-1");

    fireEvent(window, new Event("pageshow"));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    });
    expect(store[PACKET_CHECKOUT_INFLIGHT_KEY]).toBeUndefined();
  });

  it("treats Stripe cancel_url as a closed checkout, not a hang", async () => {
    window.history.replaceState(null, "", "/dashboard?packet_canceled=1");
    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByTestId("packet-checkout-canceled")).toHaveTextContent(
        PACKET_CHECKOUT_CANCELED_COPY,
      );
    });
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
  });

  it("clears a leftover inflight flag when the dashboard remounts after closing Stripe", async () => {
    store[PACKET_CHECKOUT_INFLIGHT_KEY] = "analysis-1";
    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByTestId("packet-checkout-canceled")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    expect(store[PACKET_CHECKOUT_INFLIGHT_KEY]).toBeUndefined();
  });

  it("preserves the Stripe session URL when confirmation fails", async () => {
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_packet&packet_analysis=analysis-1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: "Confirmation temporarily failed." }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Confirmation temporarily failed.",
      );
    });
    expect(window.location.search).toContain("packet_session=cs_test_packet");
  });
});
