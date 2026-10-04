import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import YearClosePacketPanel, {
  compactAnalysis,
  purchaseDateFromSuggestionId,
  PACKET_CHECKOUT_CANCELED_COPY,
  PACKET_CHECKOUT_INFLIGHT_KEY,
  isYearClosePacketPaid,
  YEAR_CLOSE_PACKET_TITLE,
  yearClosePacketStorageId,
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

  it("uses the same stable, analysis-specific storage key outside the panel", () => {
    const first = { ...analysis, analysis_id: undefined };
    const second = {
      ...analysis,
      analysis_id: undefined,
      tax_lots: analysis.tax_lots.map((lot) => ({ ...lot, quantity: 9 })),
    };

    const firstId = yearClosePacketStorageId(first);
    expect(firstId).toMatch(/^local-/);
    expect(yearClosePacketStorageId(first)).toBe(firstId);
    expect(yearClosePacketStorageId(second)).not.toBe(firstId);
  });

  it("lets the dashboard read a local analysis payment only through its saved canonical ID", () => {
    const local = yearClosePacketStorageId({ ...analysis, analysis_id: undefined });
    store[`optionstaxhub-packet-canonical:${local}`] = "canonical-analysis";
    store["optionstaxhub-packet-paid:canonical-analysis"] = "cs_test_paid";
    store[`optionstaxhub-packet-paid:${local}`] = "cs_test_stale";

    expect(isYearClosePacketPaid(local)).toBe(true);
    store[`optionstaxhub-packet-canonical:${local}`] = "different-analysis";
    expect(isYearClosePacketPaid(local)).toBe(false);
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
          session_id: "cs_test_packet",
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
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_packet");
    expect(store["optionstaxhub-packet-pending-url:analysis-1"]).toBe(
      "https://checkout.stripe.com/c/pay/cs_test_packet",
    );
  });

  it("keeps the canonical checkout ID through confirm and download for guest IDs", async () => {
    const canonicalId = "11111111-1111-4111-8111-111111111111";
    const localIdentity = yearClosePacketStorageId({
      ...analysis,
      analysis_id: undefined,
    });
    store[`optionstaxhub-packet-canonical:${localIdentity}`] = canonicalId;
    store[PACKET_CHECKOUT_INFLIGHT_KEY] = localIdentity;
    window.history.replaceState(
      null,
      "",
      `/dashboard?packet_session=cs_test_packet&packet_analysis=${canonicalId}`,
    );
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ paid: true, analysis_id: canonicalId }),
      })
      .mockResolvedValueOnce({
        ok: true,
        blob: () => Promise.resolve(new Blob(["pdf"])),
      });

    render(
      <YearClosePacketPanel
        analysis={{ ...analysis, analysis_id: undefined }}
      />,
    );

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/year-close-packet/confirm"),
        expect.any(Object),
      );
    });
    const confirmBody = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(confirmBody.analysis_id).toBe(canonicalId);
    expect(confirmBody.packet_analysis).toBe(canonicalId);
    expect(
      Object.entries(store).some(
        ([key, value]) =>
          key.startsWith("optionstaxhub-packet-canonical:local-") &&
          value === canonicalId,
      ),
    ).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const downloadBody = JSON.parse(mockFetch.mock.calls[1][1].body);
    expect(downloadBody.analysis_id).toBe(canonicalId);
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

  it("requires a saved analysis ID before checkout while isolating local state", () => {
    const firstAnalysis = { ...analysis, analysis_id: undefined };
    const secondAnalysis = {
      ...analysis,
      analysis_id: undefined,
      tax_profile: { ...analysis.tax_profile!, tax_year: 2026 },
    };
    const first = render(<YearClosePacketPanel analysis={firstAnalysis} />);
    const firstPay = screen.getByRole("button", { name: /Pay \$49/i });
    expect(firstPay).toBeDisabled();
    expect(screen.getByRole("button", { name: /Download/i })).toBeDisabled();
    expect(screen.getByText(/Re-run this analysis to access its packet/i)).toBeInTheDocument();
    fireEvent.click(firstPay);
    expect(mockFetch).not.toHaveBeenCalled();
    first.unmount();

    render(<YearClosePacketPanel analysis={secondAnalysis} />);
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Download/i })).toBeDisabled();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(yearClosePacketStorageId(firstAnalysis)).not.toBe(
      yearClosePacketStorageId(secondAnalysis),
    );
  });

  it("uses an existing paid-year entitlement without opening another Checkout", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          already_paid: true,
          session_id: "cs_test_existing_year",
          analysis_id: "analysis-1",
        }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));

    await waitFor(() =>
      expect(screen.getByText(/Unlocked for this tax year/i)).toBeInTheDocument(),
    );
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
    expect(store["optionstaxhub-packet-paid:analysis-1"]).toBe(
      "cs_test_existing_year",
    );
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

  it("retries the same Stripe session after a transient confirm failure", async () => {
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_packet&packet_analysis=analysis-1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: "Confirmation temporarily failed." }),
    }).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ analysis_id: "analysis-1" }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Confirmation temporarily failed.",
      );
    });
    expect(window.location.search).toContain("packet_session=cs_test_packet");
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    expect(mockFetch.mock.calls[1][0]).toContain("/api/year-close-packet/confirm");
    expect(JSON.parse(mockFetch.mock.calls[1][1].body).session_id).toBe(
      "cs_test_packet",
    );
    await waitFor(() => expect(window.location.search).not.toContain("packet_session"));
  });

  it("confirms a restored pending session instead of resuming Checkout blindly", async () => {
    store["optionstaxhub-packet-pending:analysis-1"] = "cs_test_pending";
    store["optionstaxhub-packet-pending-url:analysis-1"] =
      "https://checkout.stripe.com/c/pay/cs_test_pending";
    store["optionstaxhub-packet-pending-confirm:analysis-1"] = "1";
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: "Confirmation temporarily failed." }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(mockFetch.mock.calls[0][0]).toContain("/api/year-close-packet/confirm");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body).session_id).toBe(
      "cs_test_pending",
    );
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_pending");
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBe("1");
    expect(mockFetch.mock.calls[0][0]).not.toContain("/checkout");
  });

  it("keeps a pending checkout through confirm failures instead of opening another", async () => {
    store["optionstaxhub-packet-pending:analysis-1"] = "cs_test_real";
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_real&packet_analysis=analysis-1",
    );
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ detail: "Confirmation temporarily failed." }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ detail: "Confirmation temporarily failed." }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/temporarily failed/i),
    );
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_real");
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBe("1");

    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    expect(String(mockFetch.mock.calls[1][0])).toContain("/api/year-close-packet/confirm");
    expect(String(mockFetch.mock.calls[1][0])).not.toContain("/checkout");
    expect(JSON.parse(mockFetch.mock.calls[1][1].body).session_id).toBe("cs_test_real");
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_real");
  });

  it("resumes Checkout after reload only once confirm reported the session still open", async () => {
    store["optionstaxhub-packet-pending:analysis-1"] = "cs_test_open";
    store["optionstaxhub-packet-pending-url:analysis-1"] =
      "https://checkout.stripe.com/c/pay/cs_test_open";
    store["optionstaxhub-packet-pending-open:analysis-1"] = "1";

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetch).not.toHaveBeenCalled();
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_open");
  });

  it("keeps and resumes the same checkout when Stripe returns an open session", async () => {
    store["optionstaxhub-packet-pending:analysis-1"] = "cs_test_open";
    store["optionstaxhub-packet-pending-url:analysis-1"] =
      "https://checkout.stripe.com/c/pay/cs_test_open";
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_open&packet_analysis=analysis-1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: "This checkout session is still open." }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/still open/i));
    expect(window.location.search).not.toContain("packet_session");
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_open");
  });

  it("retains the paid checkout receipt when its source document is missing", async () => {
    store["optionstaxhub-packet-pending:analysis-1"] = "cs_test_paid_missing_source";
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_paid_missing_source&packet_analysis=analysis-1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () =>
        Promise.resolve({
          detail:
            "Payment was received, but this analysis has no source document.",
        }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("source document"),
    );
    expect(window.location.search).toContain("packet_session=cs_test_paid_missing_source");
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe(
      "cs_test_paid_missing_source",
    );
  });

  it("does not confirm a return URL for a different analysis", async () => {
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_packet&packet_analysis=analysis-2",
    );
    render(<YearClosePacketPanel analysis={analysis} />);

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mockFetch).not.toHaveBeenCalled();
    expect(window.location.search).toContain("packet_analysis=analysis-2");
  });
});
