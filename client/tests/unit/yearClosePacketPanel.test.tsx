import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { getSession } from "@/lib/supabase";
import YearClosePacketPanel, {
  compactAnalysis,
  purchaseDateFromSuggestionId,
  PACKET_CHECKOUT_CANCELED_COPY,
  PACKET_CHECKOUT_INFLIGHT_KEY,
  isYearClosePacketPaid,
  resetPacketResumeCheckoutLock,
  PACKET_ENTITLEMENT_UNCONFIRMED_COPY,
  YEAR_CLOSE_PACKET_TITLE,
  yearClosePacketStorageId,
} from "../../app/components/YearClosePacketPanel";
import {
  DASHBOARD_RESUME_CHECKOUT_PATH,
  PACKET_CHECKOUT_STARTED_KEY,
  PACKET_RESUME_ANALYSIS_KEY,
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_LOCAL_KEY,
  PACKET_RESUME_LOCAL_TTL_MS,
  PACKET_RESUME_LOCAL_VERSION,
  PACKET_RESUME_SAMPLE_KEY,
  safeNextPath,
} from "../../lib/safeNextPath";
import { PACKET_CHECKOUT_OTHER_TAB_COPY } from "../../app/components/YearClosePacketPanel";
import type { PortfolioAnalysis } from "../../lib/types";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: jest.fn(),
  }),
  usePathname: () => "/dashboard",
}));

const mockFetch = jest.fn();
globalThis.fetch = mockFetch;

const signedInSession = {
  access_token: "mock-jwt-token",
  user: { email_confirmed_at: "2025-01-01T00:00:00Z" },
};

function checkoutOk() {
  return {
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        checkout_url: "https://checkout.stripe.com/c/pay/cs_test_packet",
        session_id: "cs_test_packet",
      }),
  };
}

function unpaidEntitlement() {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve({ paid: false, tax_year: 2025 }),
  };
}

function mockEntitlementThen(checkout: unknown) {
  mockFetch.mockImplementation(async (url: RequestInfo | URL) => {
    if (String(url).includes("/api/year-close-packet/entitlement")) {
      return unpaidEntitlement();
    }
    return checkout;
  });
}

function checkoutPosts() {
  return mockFetch.mock.calls.filter((call) =>
    String(call[0]).includes("/api/year-close-packet/checkout"),
  );
}

function checkoutConflict(detail: string) {
  return {
    ok: false,
    status: 409,
    json: () => Promise.resolve({ detail }),
  };
}

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
    mockPush.mockReset();
    resetPacketResumeCheckoutLock();
    (getSession as jest.Mock).mockReset();
    (getSession as jest.Mock).mockResolvedValue(signedInSession);
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
    localStorage.clear();
    globalThis.URL.createObjectURL = jest.fn(() => "blob:packet");
    globalThis.URL.revokeObjectURL = jest.fn();
  });

  it("shows the $49 year-close packet, not tip tiers", () => {
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(screen.getByText(YEAR_CLOSE_PACKET_TITLE)).toBeInTheDocument();
    expect(screen.getByText(/reconciliation packet, not a filed Form 8949/i)).toBeInTheDocument();
    expect(
      screen.getByText(/downloadable year-close PDF/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not change the free 1099 totals, harvest queue, or wash-sale radar/i),
    ).toBeInTheDocument();
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
    expect(screen.getByText(/Paid for this tax year/i)).toBeInTheDocument();
    expect(
      screen.getByText(/later updates this year stay included/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pay \$49/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download/i })).toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("does not post checkout when Pay would have been clicked on an unlocked packet", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(
      <YearClosePacketPanel
        analysis={{
          ...analysis,
          packet_unlocked: true,
          packet_session_id: "cs_test_year",
        }}
      />,
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Pay \$49/i })).not.toBeInTheDocument();
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
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_packet");
    expect(store["optionstaxhub-packet-pending-url:analysis-1"]).toBe(
      "https://checkout.stripe.com/c/pay/cs_test_packet",
    );
  });

  it("sends a guest Pay $49 click to sign-in and does not call checkout", async () => {
    (getSession as jest.Mock).mockResolvedValueOnce(null);
    render(
      <YearClosePacketPanel analysis={{ ...analysis, sample_run: true }} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalled();
    });
    const href = String(mockPush.mock.calls[0][0]);
    const next = new URL(href, "https://optionstaxhub.invalid").searchParams.get(
      "next",
    );
    expect(safeNextPath(next)).toBe(DASHBOARD_RESUME_CHECKOUT_PATH);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBe("1");
    expect(store[PACKET_RESUME_SAMPLE_KEY]).toBe("1");
    const handoff = JSON.parse(
      localStorage.getItem(PACKET_RESUME_LOCAL_KEY) as string,
    ) as Record<string, unknown>;
    expect(Object.keys(handoff).sort()).toEqual([
      "analysisId",
      "exp",
      "sample",
      "taxYear",
      "v",
    ]);
    expect(handoff).toEqual({
      v: PACKET_RESUME_LOCAL_VERSION,
      sample: true,
      exp: expect.any(Number),
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    expect(handoff.exp as number).toBeGreaterThan(Date.now());
    expect(handoff.exp as number).toBeLessThanOrEqual(
      Date.now() + PACKET_RESUME_LOCAL_TTL_MS + 1000,
    );
    expect(JSON.stringify(handoff)).not.toMatch(/position|symbol|csv|filing|income|lot/i);
    expect(store[PACKET_RESUME_ANALYSIS_KEY]).toBe(
      JSON.stringify({ analysisId: "analysis-1", taxYear: 2025 }),
    );
    expect(store[PACKET_CHECKOUT_INFLIGHT_KEY]).toBeUndefined();
    expect(screen.queryByText(/Authentication required/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
  });

  it("does not mark a non-sample guest run for sample re-run", async () => {
    (getSession as jest.Mock).mockResolvedValueOnce(null);
    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalled();
    });
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBe("1");
    expect(store[PACKET_RESUME_SAMPLE_KEY]).toBeUndefined();
    expect(JSON.parse(localStorage.getItem(PACKET_RESUME_LOCAL_KEY) as string)).toMatchObject({
      v: PACKET_RESUME_LOCAL_VERSION,
      sample: false,
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    expect(store[PACKET_RESUME_ANALYSIS_KEY]).toBe(
      JSON.stringify({ analysisId: "analysis-1", taxYear: 2025 }),
    );
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("clears a stale sample resume flag on a non-sample guest Pay", async () => {
    (getSession as jest.Mock).mockResolvedValueOnce(null);
    store[PACKET_RESUME_SAMPLE_KEY] = "1";
    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalled();
    });
    expect(store[PACKET_RESUME_SAMPLE_KEY]).toBeUndefined();
    expect(JSON.parse(localStorage.getItem(PACKET_RESUME_LOCAL_KEY) as string)).toMatchObject({
      v: PACKET_RESUME_LOCAL_VERSION,
      sample: false,
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows the navigation error when sign-in routing throws", async () => {
    (getSession as jest.Mock).mockResolvedValueOnce(null);
    mockPush.mockImplementation(() => {
      throw new Error("nav failed");
    });
    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("nav failed");
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(store[PACKET_CHECKOUT_INFLIGHT_KEY]).toBeUndefined();
  });

  it("posts checkout once when the resume analysis id and tax year match", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: true,
        exp: Date.now() + 60_000,
        analysisId: "analysis-1",
        taxYear: 2025,
      }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockEntitlementThen(checkoutOk());

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(1);
    });
    const [url, init] = checkoutPosts()[0];
    expect(url).toContain("/api/year-close-packet/checkout");
    expect(url).not.toContain("/api/tips/checkout");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer mock-jwt-token");
    expect(JSON.parse(init.body).analysis_id).toBe("analysis-1");
    expect(window.location.search).not.toContain("resume_checkout");
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBeUndefined();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toMatch(/^\d+$/);
  });

  it("does not post checkout when the resume analysis id does not match", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "other-run",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBe("1");
  });

  it("does not post checkout when the resume analysis id is missing", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({ v: 1, sample: false, exp: Date.now() + 60_000 }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
  });

  it("posts checkout for a re-run sample with the same tax year", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_SAMPLE_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "original-sample",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockEntitlementThen(checkoutOk());
    render(
      <YearClosePacketPanel
        analysis={{ ...analysis, analysis_id: "rerun-sample", sample_run: true }}
      />,
    );
    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(1);
    });
    expect(checkoutPosts()[0][1].headers.Authorization).toBe(
      "Bearer mock-jwt-token",
    );
    expect(JSON.parse(checkoutPosts()[0][1].body).analysis_id).toBe(
      "rerun-sample",
    );
  });

  it("shows Checkout opened in your other tab and does not post when resume_checkout=1 and checkout started recently", async () => {
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now()));
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(await screen.findByTestId("packet-checkout-other-tab")).toHaveTextContent(
      PACKET_CHECKOUT_OTHER_TAB_COPY,
    );
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(window.location.search).not.toContain("resume_checkout");
  });

  it("shows the other-tab note when the mounted analysis matches a recent claim", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    const started = String(Date.now());
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, started);
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(await screen.findByTestId("packet-checkout-other-tab")).toHaveTextContent(
      PACKET_CHECKOUT_OTHER_TAB_COPY,
    );
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBe(started);
    expect(store[PACKET_RESUME_ANALYSIS_KEY]).toBeUndefined();
  });

  it("keeps the expected run and ignores Pay when a recent claim is for a different analysis", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_SAMPLE_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "origin-id",
      taxYear: 2025,
    });
    const started = String(Date.now());
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, started);
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "origin-id",
        taxYear: 2025,
      }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const view = render(<YearClosePacketPanel analysis={analysis} />);
    const pay = screen.getByRole("button", { name: /Pay \$49/i });
    expect(pay).toBeDisabled();
    fireEvent.click(pay);
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.queryByTestId("packet-checkout-other-tab")).not.toBeInTheDocument();
    expect(store[PACKET_RESUME_ANALYSIS_KEY]).toContain("origin-id");
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBe("1");
    expect(store[PACKET_RESUME_SAMPLE_KEY]).toBe("1");
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toContain("origin-id");
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBe(started);
    expect(window.location.search).toContain("resume_checkout=1");

    delete store[PACKET_RESUME_CHECKOUT_KEY];
    delete store[PACKET_RESUME_SAMPLE_KEY];
    delete store[PACKET_RESUME_ANALYSIS_KEY];
    view.rerender(<YearClosePacketPanel analysis={analysis} />);
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
  });

  it("writes checkout-started before the checkout POST and does not post when that timestamp is already recent", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    const started = String(Date.now() - 1000);
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, started);
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(await screen.findByTestId("packet-checkout-other-tab")).toHaveTextContent(
      PACKET_CHECKOUT_OTHER_TAB_COPY,
    );
    expect(mockFetch).not.toHaveBeenCalled();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBe(started);
  });

  it("aborts auto-resume when another tab writes checkout-started during getSession", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    (getSession as jest.Mock).mockImplementation(async () => {
      localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now() - 50));
      return signedInSession;
    });
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(await screen.findByTestId("packet-checkout-other-tab")).toHaveTextContent(
      PACKET_CHECKOUT_OTHER_TAB_COPY,
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("aborts the checkout POST when another tab replaces the claim after getSession", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    let calls = 0;
    (getSession as jest.Mock).mockImplementation(async () => {
      calls += 1;
      if (calls >= 2) {
        localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, "1");
      }
      return signedInSession;
    });
    mockEntitlementThen(checkoutOk());
    render(<YearClosePacketPanel analysis={analysis} />);
    expect(await screen.findByTestId("packet-checkout-other-tab")).toHaveTextContent(
      PACKET_CHECKOUT_OTHER_TAB_COPY,
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(checkoutPosts()).toHaveLength(0);
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBe("1");
  });

  it("does not auto-start a second checkout after the resume marker is consumed", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockEntitlementThen(checkoutOk());

    const first = render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(1);
    });
    first.unmount();

    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(checkoutPosts()).toHaveLength(1);
  });

  it("leaves the resume marker when the session is not ready", async () => {
    (getSession as jest.Mock).mockResolvedValue(null);
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");

    render(<YearClosePacketPanel analysis={analysis} />);
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBe("1");
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
    expect(window.location.search).toContain("resume_checkout=1");
    expect(screen.queryByText(/Authentication required/i)).not.toBeInTheDocument();
  });

  it("does not auto-start checkout when the packet is already unlocked", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(
      <YearClosePacketPanel
        analysis={{ ...analysis, packet_unlocked: true, packet_session_id: "cs_test_year" }}
      />,
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBeUndefined();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
  });

  it("does not auto-start checkout for a paid storage flag", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store["optionstaxhub-packet-paid:analysis-1"] = "cs_test_paid";
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<YearClosePacketPanel analysis={analysis} />);
    await new Promise((resolve) => {
      setTimeout(resolve, 40);
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("does not auto-start checkout when packet_session is present", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_packet&packet_analysis=analysis-1&resume_checkout=1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ paid: true, analysis_id: "analysis-1" }),
    });
    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });
    expect(String(mockFetch.mock.calls[0][0])).toContain(
      "/api/year-close-packet/confirm",
    );
    expect(String(mockFetch.mock.calls[0][0])).not.toContain(
      "/api/year-close-packet/checkout",
    );
  });

  it("does not auto-start checkout when packet_canceled is present", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({ v: 1, sample: false, exp: Date.now() + 60_000 }),
    );
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_canceled=1&resume_checkout=1",
    );
    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(screen.getByTestId("packet-checkout-canceled")).toBeInTheDocument();
    });
    expect(mockFetch).not.toHaveBeenCalled();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
  });

  it("keeps Pay disabled and posts no checkout while entitlement is pending, then hides Pay when the year is paid", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    let resolveEntitlement: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      resolveEntitlement = resolve;
    });
    mockFetch.mockImplementation((url: RequestInfo | URL) => {
      if (String(url).includes("/api/year-close-packet/entitlement")) {
        return pending;
      }
      return Promise.resolve(checkoutOk());
    });

    render(<YearClosePacketPanel analysis={analysis} />);

    const pay = screen.getByRole("button", { name: /Pay \$49/i });
    expect(pay).toBeDisabled();
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });
    expect(checkoutPosts()).toHaveLength(0);
    expect(pay).toBeDisabled();

    await act(async () => {
      resolveEntitlement({
        ok: true,
        status: 200,
        json: async () => ({
          paid: true,
          tax_year: 2025,
          session_id: "cs_test_paid_year",
        }),
      });
    });

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Pay \$49/i })).not.toBeInTheDocument();
    });
    expect(screen.getByText(/Payment confirmed for tax year 2025/i)).toBeInTheDocument();
    expect(checkoutPosts()).toHaveLength(0);
    expect(screen.queryByText(PACKET_CHECKOUT_CANCELED_COPY)).not.toBeInTheDocument();
  });

  async function expectManualPayAfterFailedEntitlement() {
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    });
    expect(screen.getByTestId("packet-entitlement-unconfirmed")).toHaveTextContent(
      PACKET_ENTITLEMENT_UNCONFIRMED_COPY,
    );
    expect(checkoutPosts()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(1);
    });
  }

  it("leaves Pay enabled with a purchase-status message and no automatic checkout when entitlement returns 503", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockFetch.mockImplementation(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/year-close-packet/entitlement")) {
        return {
          ok: false,
          status: 503,
          json: async () => ({ detail: "Could not verify existing packet access." }),
        };
      }
      return checkoutOk();
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    await expectManualPayAfterFailedEntitlement();
  });

  it("leaves Pay enabled with a purchase-status message and no automatic checkout when the entitlement request fails", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockFetch.mockImplementation(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/year-close-packet/entitlement")) {
        throw new Error("network down");
      }
      return checkoutOk();
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    await expectManualPayAfterFailedEntitlement();
  });

  it("hides the closed-without-payment note when the year is paid and clears resume state", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_SAMPLE_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "analysis-1",
        taxYear: 2025,
      }),
    );
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now()));
    window.history.replaceState(null, "", "/dashboard?packet_canceled=1");
    render(
      <YearClosePacketPanel
        analysis={{
          ...analysis,
          packet_unlocked: true,
          packet_session_id: "cs_test_year",
        }}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/Payment confirmed for tax year 2025/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("packet-checkout-canceled")).not.toBeInTheDocument();
    expect(screen.queryByText(PACKET_CHECKOUT_CANCELED_COPY)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pay \$49/i })).not.toBeInTheDocument();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
    expect(store[PACKET_RESUME_CHECKOUT_KEY]).toBeUndefined();
    expect(store[PACKET_RESUME_SAMPLE_KEY]).toBeUndefined();
    expect(store[PACKET_RESUME_ANALYSIS_KEY]).toBeUndefined();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("does not show a stale paid flag on a guest or sample run", async () => {
    store["optionstaxhub-packet-paid:analysis-1"] = "cs_test_other_account";
    store["optionstaxhub-packet-canonical:analysis-1"] = "analysis-1";
    render(
      <YearClosePacketPanel
        guest
        analysis={{
          ...analysis,
          sample_run: true,
          packet_unlocked: true,
          packet_session_id: "cs_test_other_account",
        }}
      />,
    );
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    expect(screen.queryByText(/Paid for this tax year/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Payment confirmed for tax year/i)).not.toBeInTheDocument();
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(checkoutPosts()).toHaveLength(0);
  });

  it("does not auto-start checkout without a saved analysis id", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(
      <YearClosePacketPanel analysis={{ ...analysis, analysis_id: undefined }} />,
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("asks for one sample re-run when a guest snapshot 409 matches", async () => {
    const onSampleRerunForCheckout = jest.fn(() => true);
    const details = [
      "This guest snapshot must be restored from its matching analysis before checkout.",
      "No owner-scoped private snapshot for this analysis.",
      "Re-run it before checkout.",
    ];
    for (const detail of details) {
      mockFetch.mockReset();
      mockFetch.mockResolvedValueOnce(checkoutConflict(detail));
      const view = render(
        <YearClosePacketPanel
          analysis={{ ...analysis, sample_run: true }}
          onSampleRerunForCheckout={onSampleRerunForCheckout}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      });
      expect(screen.queryByText(detail)).not.toBeInTheDocument();
      expect(store[PACKET_CHECKOUT_INFLIGHT_KEY]).toBeUndefined();
      view.unmount();
    }
    expect(onSampleRerunForCheckout).toHaveBeenCalledTimes(3);

    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_SAMPLE_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "original-sample",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockEntitlementThen(checkoutOk());
    render(
      <YearClosePacketPanel
        analysis={{ ...analysis, sample_run: true }}
        onSampleRerunForCheckout={onSampleRerunForCheckout}
      />,
    );
    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(1);
    });
    expect(String(checkoutPosts()[0][0])).toContain(
      "/api/year-close-packet/checkout",
    );
  });

  it("shows the server detail when the sample re-run budget is spent", async () => {
    const detail =
      "This guest snapshot must be restored from its matching analysis before checkout.";
    mockFetch.mockResolvedValueOnce(checkoutConflict(detail));
    render(
      <YearClosePacketPanel
        analysis={{ ...analysis, sample_run: true }}
        onSampleRerunForCheckout={() => false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
  });

  it("does not re-run a non-sample CSV when checkout returns 409", async () => {
    const detail =
      "This guest snapshot must be restored from its matching analysis before checkout.";
    const onSampleRerunForCheckout = jest.fn(() => true);
    mockFetch.mockResolvedValueOnce(checkoutConflict(detail));
    render(
      <YearClosePacketPanel
        analysis={analysis}
        onSampleRerunForCheckout={onSampleRerunForCheckout}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    expect(onSampleRerunForCheckout).not.toHaveBeenCalled();
  });

  it("releases the checkout claim on a guest-snapshot 409 so the sample re-run posts once more", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_SAMPLE_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "original-sample",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const onSampleRerunForCheckout = jest.fn(() => {
      store[PACKET_RESUME_CHECKOUT_KEY] = "1";
      store[PACKET_RESUME_SAMPLE_KEY] = "1";
      window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
      return true;
    });
    const checkoutResponses = [
      checkoutConflict(
        "This guest snapshot must be restored from its matching analysis before checkout.",
      ),
      checkoutOk(),
    ];
    mockFetch.mockImplementation(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/year-close-packet/entitlement")) {
        return unpaidEntitlement();
      }
      return checkoutResponses.shift();
    });
    const view = render(
      <YearClosePacketPanel
        analysis={{
          ...analysis,
          analysis_id: "original-sample",
          sample_run: true,
        }}
        onSampleRerunForCheckout={onSampleRerunForCheckout}
      />,
    );

    await waitFor(() => {
      expect(onSampleRerunForCheckout).toHaveBeenCalledTimes(1);
    });
    expect(checkoutPosts()).toHaveLength(1);
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
    expect(screen.queryByTestId("packet-checkout-other-tab")).not.toBeInTheDocument();

    view.rerender(
      <YearClosePacketPanel
        analysis={{
          ...analysis,
          analysis_id: "rerun-sample",
          sample_run: true,
        }}
        onSampleRerunForCheckout={onSampleRerunForCheckout}
      />,
    );
    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(2);
    });
    const postedIds = checkoutPosts().map((call) => {
      const init = call[1] as { body?: string };
      return JSON.parse(init.body || "{}").analysis_id;
    });
    expect(postedIds).toEqual(["original-sample", "rerun-sample"]);
    expect(screen.queryByText(PACKET_CHECKOUT_OTHER_TAB_COPY)).not.toBeInTheDocument();
  });

  it("releases the checkout claim when checkout returns 500", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockEntitlementThen({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ detail: "Stripe is down" }),
    });
    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(checkoutPosts()).toHaveLength(1);
    });
    await waitFor(() => {
      expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Stripe is down");
    expect(screen.queryByTestId("packet-checkout-other-tab")).not.toBeInTheDocument();
  });

  it("still shows the auth error when confirm runs without a session", async () => {
    (getSession as jest.Mock).mockResolvedValue(null);
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_packet&packet_analysis=analysis-1",
    );
    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Authentication required. Please sign in.",
      );
    });
    expect(mockPush).not.toHaveBeenCalled();
    expect(mockFetch).not.toHaveBeenCalled();
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
    const downloadUrl = String(mockFetch.mock.calls[1][0]);
    const downloadInit = mockFetch.mock.calls[1][1];
    expect(downloadInit.method).toBe("GET");
    expect(downloadInit.body).toBeUndefined();
    expect(downloadUrl).toContain("/api/year-close-packet/download?");
    expect(downloadUrl).toContain(`analysis_id=${encodeURIComponent(canonicalId)}`);
    expect(downloadUrl).toContain("session_id=cs_test_packet");
  });

  it("shows missing-source detail on a fresh download with no session", async () => {
    const detail =
      "Payment was received, but this analysis has no source document. Run a new analysis for this tax year.";
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-paid:analysis-1"]).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      /Pay \$49 to download the year-close packet/i,
    );
    const downloadUrl = String(mockFetch.mock.calls[0][0]);
    expect(downloadUrl).toContain("/api/year-close-packet/download?");
    expect(downloadUrl).toContain("analysis_id=analysis-1");
    expect(downloadUrl).not.toContain("session_id=");
    expect(mockFetch.mock.calls[0][1].method).toBe("GET");
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
  });

  it("shows Download failed when a fresh 409 download has no detail", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: null }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Download failed.");
    });
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      /Pay \$49 to download the year-close packet/i,
    );
    expect(String(mockFetch.mock.calls[0][0])).not.toContain("session_id=");
  });

  it("shows Download failed when a fresh 409 download body cannot be read", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.reject(new Error("bad json")),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Download failed.");
    });
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      /Pay \$49 to download the year-close packet/i,
    );
  });

  it("does not keep a non-missing-source 409 across a later 403", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: "Payment was received." }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        json: () =>
          Promise.resolve({ detail: "Year-close packet download requires payment." }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Payment was received.");
    });

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Pay \$49 to download the year-close packet/i,
      );
    });
  });

  it("shows a recoverable error when the paid snapshot is missing", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () =>
        Promise.resolve({
          detail:
            "Payment was received, but this analysis has no source document. Run a new analysis for this tax year.",
        }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/no source document/i);
    });
  });

  it("keeps the missing-source alert on a 409 download and replaces it with Pay $49 on a 403", async () => {
    const detail =
      "Payment was received, but this analysis has no source document. Run a new analysis for this tax year.";
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        json: () =>
          Promise.resolve({ detail: "Year-close packet download requires payment." }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      /Pay \$49 to download the year-close packet/i,
    );

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Pay \$49 to download the year-close packet/i,
      );
    });
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/no source document/i);
  });

  it("clears the missing-source alert when download is ok, before the PDF starts", async () => {
    const detail =
      "Payment was received, but this analysis has no source document. Run a new analysis for this tax year.";
    let releaseBlob: (blob: Blob) => void = () => {};
    const blobGate = new Promise<Blob>((resolve) => {
      releaseBlob = resolve;
    });
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        blob: () => blobGate,
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
    expect(globalThis.URL.createObjectURL).not.toHaveBeenCalled();

    releaseBlob!(new Blob(["pdf"]));
    await waitFor(() => {
      expect(globalThis.URL.createObjectURL).toHaveBeenCalled();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
  });

  it("sends only the bearer header on download and keeps JSON content type on checkout", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(new Blob(["pdf"])),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            checkout_url: "https://checkout.stripe.com/c/pay/cs_test_packet",
            session_id: "cs_test_packet",
          }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    const downloadHeaders = mockFetch.mock.calls[0][1].headers;
    expect(downloadHeaders.Authorization).toBe("Bearer mock-jwt-token");
    expect(downloadHeaders["Content-Type"]).toBeUndefined();
    expect(mockFetch.mock.calls[0][1].method).toBe("GET");
    expect(mockFetch.mock.calls[0][1].body).toBeUndefined();

    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const checkoutHeaders = mockFetch.mock.calls[1][1].headers;
    expect(checkoutHeaders.Authorization).toBe("Bearer mock-jwt-token");
    expect(checkoutHeaders["Content-Type"]).toBe("application/json");
    expect(mockFetch.mock.calls[1][1].method).toBe("POST");
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
      expect(screen.getByText(/Payment confirmed for tax year/i)).toBeInTheDocument(),
    );
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Pay \$49/i })).not.toBeInTheDocument();
    expect(store["optionstaxhub-packet-paid:analysis-1"]).toBe(
      "cs_test_existing_year",
    );
  });

  it("releases the checkout claim when resume checkout is already paid", async () => {
    store[PACKET_RESUME_CHECKOUT_KEY] = "1";
    store[PACKET_RESUME_ANALYSIS_KEY] = JSON.stringify({
      analysisId: "analysis-1",
      taxYear: 2025,
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockFetch.mockImplementation(async (url: RequestInfo | URL) => {
      if (String(url).includes("/api/year-close-packet/entitlement")) {
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              paid: true,
              tax_year: 2025,
              session_id: "cs_test_existing_year",
            }),
        };
      }
      return {
        ok: true,
        json: () =>
          Promise.resolve({
            already_paid: true,
            session_id: "cs_test_existing_year",
            analysis_id: "analysis-1",
          }),
      };
    });

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() =>
      expect(screen.getByText(/Payment confirmed for tax year/i)).toBeInTheDocument(),
    );
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
    expect(checkoutPosts()).toHaveLength(0);
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

  it("releases the checkout claim when Stripe sends the shopper back canceled", async () => {
    const started = String(Date.now());
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, started);
    window.history.replaceState(null, "", "/dashboard?packet_canceled=1");
    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByTestId("packet-checkout-canceled")).toBeInTheDocument();
    });
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
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

  it("stops polling confirm when the paid source document is missing", async () => {
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
    expect(window.location.search).not.toContain("packet_session");
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBeUndefined();
  });

  it("stops confirming when the source document is missing", async () => {
    const detail =
      "Payment was received, but this analysis has no source document. Run a new analysis for this tax year.";
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_missing_source&packet_analysis=analysis-1",
    );
    const checkoutUrl = "https://checkout.stripe.com/c/pay/cs_test_new";
    const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);

    try {
      await waitFor(() => {
        expect(screen.getByRole("alert")).toHaveTextContent(detail);
      });
      expect(window.location.search).not.toContain("packet_session");
      expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBeUndefined();
      expect(mockFetch).toHaveBeenCalledTimes(1);

      fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledTimes(2);
        expect(screen.getByRole("alert")).toHaveTextContent(detail);
        expect(screen.getByRole("button", { name: /Download/i })).toBeEnabled();
      });
      expect(String(mockFetch.mock.calls[1][0])).toContain(
        "/api/year-close-packet/checkout",
      );
      expect(String(mockFetch.mock.calls[1][0])).not.toContain("/confirm");
      expect(window.location.href).not.toContain(checkoutUrl);
      expect(window.location.href).not.toContain("checkout.stripe.com");
      expect(
        consoleError.mock.calls.some((call) =>
          call.some((arg) => String(arg?.message || arg).includes("Not implemented: navigation")),
        ),
      ).toBe(false);
      expect(screen.getByRole("alert")).not.toHaveTextContent(
        /Pay \$49 to download the year-close packet/i,
      );

      fireEvent.click(screen.getByRole("button", { name: /Download/i }));
      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(3));
      expect(String(mockFetch.mock.calls[2][0])).toContain("/api/year-close-packet/download");
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
      expect(screen.getByRole("alert")).not.toHaveTextContent(
        /Pay \$49 to download the year-close packet/i,
      );
      expect(window.location.href).not.toContain("checkout.stripe.com");
    } finally {
      consoleError.mockRestore();
    }
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

  async function expectExistingPacketDownload(detail: string) {
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_other&packet_analysis=analysis-1",
    );
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(new Blob(["%PDF"])),
      });

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Download/i })).toBeEnabled();
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-paid:analysis-1"]).toBeUndefined();
    expect(window.location.href).not.toContain("checkout.stripe.com");

    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    expect(mockFetch).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /Download/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const downloadUrl = String(mockFetch.mock.calls[1][0]);
    expect(mockFetch.mock.calls[1][1].method).toBe("GET");
    expect(downloadUrl).toContain("/api/year-close-packet/download?");
    expect(downloadUrl).toContain("analysis_id=analysis-1");
    expect(downloadUrl).not.toContain("session_id=");
    expect(window.location.href).not.toContain("checkout.stripe.com");
  }

  it("offers a session-less download instead of Pay when the packet is already paid", async () => {
    await expectExistingPacketDownload(
      "This tax year is already paid. This extra charge was recorded for a refund. Download your existing packet.",
    );
  });

  async function expectExtraChargeRefund(detail: string) {
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_other&packet_analysis=analysis-1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail }),
    });

    render(<YearClosePacketPanel analysis={analysis} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    const pay = screen.getByRole("button", { name: /Pay \$49/i });
    expect(pay).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Download/i })).not.toBeInTheDocument();
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-paid:analysis-1"]).toBeUndefined();
    expect(window.location.href).not.toContain("checkout.stripe.com");

    fireEvent.click(pay);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(window.location.href).not.toContain("checkout.stripe.com");
  }

  it("records a same-year duplicate as a terminal extra charge without a download offer", async () => {
    await expectExtraChargeRefund(
      "PACKET_GRANT_SAME_YEAR_DUPLICATE: this tax year was already paid under another checkout session. The new charge is recorded for a refund.",
    );
  });

  it("records a conflict receipt as a terminal extra charge without a download offer", async () => {
    await expectExtraChargeRefund(
      "This checkout session was already recorded as a refund and does not grant a packet.",
    );
  });

  it("offers a session-less download when the webhook lands first and confirm's first response is the verified already-paid wording", async () => {
    await expectExistingPacketDownload(
      "This tax year is already paid. This extra charge was recorded for a refund. Download your existing packet.",
    );
  });

  it("offers a download when a later same-year confirm returns the verified already-paid wording", async () => {
    const duplicate =
      "PACKET_GRANT_SAME_YEAR_DUPLICATE: this tax year was already paid under another checkout session. The new charge is recorded for a refund.";
    const verified =
      "This tax year is already paid. This extra charge was recorded for a refund. Download your existing packet.";
    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_other&packet_analysis=analysis-1",
    );
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail: duplicate }),
    });

    const firstVisit = render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(duplicate);
    });
    expect(screen.queryByRole("button", { name: /Download/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
    expect(mockFetch).toHaveBeenCalledTimes(1);
    firstVisit.unmount();

    window.history.replaceState(
      null,
      "",
      "/dashboard?packet_session=cs_test_other&packet_analysis=analysis-1",
    );
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: verified }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(new Blob(["%PDF"])),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(verified);
    });
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
    const download = screen.getByRole("button", { name: /Download/i });
    expect(download).toBeEnabled();
    expect(download.className).toContain("MuiButton-contained");

    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    expect(mockFetch).toHaveBeenCalledTimes(2);

    fireEvent.click(download);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(3));
    expect(String(mockFetch.mock.calls[1][0])).toContain("/api/year-close-packet/confirm");
    expect(JSON.parse(mockFetch.mock.calls[1][1].body).session_id).toBe("cs_test_other");
    const downloadUrl = String(mockFetch.mock.calls[2][0]);
    expect(mockFetch.mock.calls[2][1].method).toBe("GET");
    expect(downloadUrl).toContain("/api/year-close-packet/download?");
    expect(downloadUrl).toContain("analysis_id=analysis-1");
    expect(downloadUrl).not.toContain("session_id=");
    expect(window.location.href).not.toContain("checkout.stripe.com");
  });

  it("retries confirm and does not open Stripe while the packet is still being finalized", async () => {
    const detail =
      "Payment is on file and the packet is still being finalized. Retry confirming the payment.";
    store["optionstaxhub-packet-pending:analysis-1"] = "cs_test_finalizing";
    store["optionstaxhub-packet-pending-url:analysis-1"] =
      "https://checkout.stripe.com/c/pay/cs_test_finalizing";
    store["optionstaxhub-packet-pending-open:analysis-1"] = "1";
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    expect(String(mockFetch.mock.calls[0][0])).toContain("/api/year-close-packet/download");
    expect(String(mockFetch.mock.calls[0][0])).toContain("session_id=cs_test_finalizing");
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBe("cs_test_finalizing");
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBe("1");
    expect(store["optionstaxhub-packet-pending-open:analysis-1"]).toBeUndefined();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();

    const consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
    try {
      fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      expect(String(mockFetch.mock.calls[1][0])).toContain("/api/year-close-packet/confirm");
      expect(String(mockFetch.mock.calls[1][0])).not.toContain("/checkout");
      expect(JSON.parse(mockFetch.mock.calls[1][1].body).session_id).toBe(
        "cs_test_finalizing",
      );
      expect(window.location.href).not.toContain("checkout.stripe.com");
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
    } finally {
      consoleError.mockRestore();
    }
  });

  it("starts checkout after a session-less finalizing download", async () => {
    const detail =
      "Payment is on file and the packet is still being finalized. Retry confirming the payment.";
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            already_paid: true,
            session_id: "cs_test_finalizing_checkout",
            analysis_id: "analysis-1",
          }),
      });

    render(<YearClosePacketPanel analysis={analysis} />);
    fireEvent.click(screen.getByRole("button", { name: /Download/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(detail);
    });
    expect(String(mockFetch.mock.calls[0][0])).toContain("/api/year-close-packet/download");
    expect(String(mockFetch.mock.calls[0][0])).not.toContain("session_id=");
    expect(store["optionstaxhub-packet-pending:analysis-1"]).toBeUndefined();
    expect(store["optionstaxhub-packet-pending-confirm:analysis-1"]).toBeUndefined();
    expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    expect(String(mockFetch.mock.calls[1][0])).toContain("/api/year-close-packet/checkout");
    expect(mockFetch.mock.calls[1][1].method).toBe("POST");
    expect(String(mockFetch.mock.calls[1][0])).not.toContain("/confirm");
    await waitFor(() =>
      expect(screen.getByText(/Payment confirmed for tax year/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /Pay \$49/i })).not.toBeInTheDocument();
    expect(store["optionstaxhub-packet-paid:analysis-1"]).toBe(
      "cs_test_finalizing_checkout",
    );
    expect(window.location.href).not.toContain("checkout.stripe.com");
  });
});
