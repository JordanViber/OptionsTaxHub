import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { LeapRankResponse } from "../../lib/types";
import type { Position } from "../../lib/types";

const mockLeapRankMutateAsync = jest.fn();
const mockLeapRankPending = { value: false };
const mockRhChainMutateAsync = jest.fn();
const mockRhChainPending = { value: false };
const mockUser: { value: { id: string } | null } = { value: null };
const mockRhConnected = { value: false };

jest.mock("../../lib/api", () => ({
  useLeapRankMutation: () => ({
    mutateAsync: mockLeapRankMutateAsync,
    isPending: mockLeapRankPending.value,
    reset: jest.fn(),
  }),
  useRhChainMutation: () => ({
    mutateAsync: mockRhChainMutateAsync,
    isPending: mockRhChainPending.value,
    reset: jest.fn(),
  }),
  fetchRhStatus: () =>
    Promise.resolve({ connected: mockRhConnected.value }),
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => ({ user: mockUser.value, loading: false }),
}));

import EntryAnalysisPanel from "../../app/components/EntryAnalysisPanel";
import { RH_CONNECTION_REQUIRED_COPY } from "../../lib/types";

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

function mockRankSuccess(): LeapRankResponse {
  return {
    ok: true,
    symbol: "NVDA",
    right: "call",
    spot: 100,
    as_of: "2026-09-06",
    expiry_from: "2027-09-06",
    expiry_to: "2028-09-06",
    expirations_used: ["2027-09-17"],
    candidates_considered: 3,
    warnings: [],
    ranks: [
      {
        rank: 1,
        contract_label: "NVDA 9/17/2027 Call $90.00",
        symbol: "NVDA",
        right: "call",
        strike: 90,
        expiration: "2027-09-17",
        premium: 4.2,
        premium_source: "mid",
        bid: 4.1,
        ask: 4.3,
        last: 4.2,
        dte: 376,
        implied_cagr: 0.042,
        leverage: 23.81,
        intrinsic: 10,
        extrinsic: 0,
        extrinsic_yield: 0,
        breakeven: 94.2,
        why_vs_stock:
          "Needs a 4.2% annualized move in NVDA to break even vs owning shares at $100.00. This LEAP costs $420.00 vs $10,000.00 for 100 shares (23.8× less capital). Time value is $0.00 (0.0% per year).",
        why_vs_richer:
          "Less annualized move to break even than the NVDA 9/17/2027 Call $95.00 (4.2% vs 6.1%) because a lower strike.",
      },
      {
        rank: 2,
        contract_label: "NVDA 9/17/2027 Call $95.00",
        symbol: "NVDA",
        right: "call",
        strike: 95,
        expiration: "2027-09-17",
        premium: 5,
        premium_source: "mid",
        bid: 4.9,
        ask: 5.1,
        last: 5,
        dte: 376,
        implied_cagr: 0.061,
        leverage: 20,
        intrinsic: 5,
        extrinsic: 0,
        extrinsic_yield: 0,
        breakeven: 100,
        why_vs_stock:
          "Needs a 6.1% annualized move in NVDA to break even vs owning shares at $100.00.",
        why_vs_richer:
          "Less annualized move to break even than the NVDA 9/17/2027 Call $100.00 (6.1% vs 8.0%) because a lower strike.",
      },
      {
        rank: 3,
        contract_label: "NVDA 9/17/2027 Call $100.00",
        symbol: "NVDA",
        right: "call",
        strike: 100,
        expiration: "2027-09-17",
        premium: 8,
        premium_source: "mid",
        bid: 7.9,
        ask: 8.1,
        last: 8,
        dte: 376,
        implied_cagr: 0.08,
        leverage: 12.5,
        intrinsic: 0,
        extrinsic: 8,
        extrinsic_yield: 0.08,
        breakeven: 108,
        why_vs_stock:
          "Needs a 8.0% annualized move in NVDA to break even vs owning shares at $100.00.",
        why_vs_richer: null,
      },
    ],
  };
}

describe("EntryAnalysisPanel", () => {
  beforeEach(() => {
    mockLeapRankMutateAsync.mockReset();
    mockLeapRankPending.value = false;
    mockRhChainMutateAsync.mockReset();
    mockRhChainPending.value = false;
    mockUser.value = null;
    mockRhConnected.value = false;
  });

  it("renders Analyze a new option with no analysis and no packet unlock", () => {
    render(<EntryAnalysisPanel />);

    expect(screen.getByTestId("entry-analysis-panel")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Analyze a new option/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/What-if · single-leg/i)).toBeInTheDocument();
    expect(screen.getByTestId("entry-rank-find")).toBeInTheDocument();
    expect(screen.getByTestId("entry-rank-leaps")).toHaveTextContent(/Rank LEAPs/i);
    expect(screen.getByTestId("entry-rh-status")).toHaveTextContent(
      /Sign in to connect Robinhood/i,
    );
    expect(
      screen.queryByRole("button", { name: /connect robinhood/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reconnect/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /disconnect/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/oauth/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/manage account/i)).not.toBeInTheDocument();
    expect(screen.getByTestId("entry-rank-empty")).toHaveTextContent(
      /smallest annualized move to break even vs owning the stock/i,
    );
    expect(screen.getByTestId("entry-empty")).toHaveTextContent(
      /Enter premium to see max gain, max loss, and breakeven/i,
    );
    expect(screen.queryByTestId("entry-results")).not.toBeInTheDocument();
    expect(screen.queryByTestId("entry-context")).not.toBeInTheDocument();
    expect(screen.queryByTestId("entry-rank-1")).not.toBeInTheDocument();
    expect(
      screen.getByText(/not a filed Form 8949/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/not the year-close packet/i)).toBeInTheDocument();
    expect(screen.queryByText(/\$49/)).not.toBeInTheDocument();
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

  it("ranks three long calls and fills v1 payoff when #1 is selected", async () => {
    mockLeapRankMutateAsync.mockResolvedValue(mockRankSuccess());
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-find"));

    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-list")).toBeInTheDocument();
    });
    expect(screen.getByTestId("entry-rank-1")).toHaveTextContent(
      /4\.2% annualized to break even/,
    );
    expect(screen.getByTestId("entry-rank-1")).toHaveTextContent(
      /annualized move in NVDA to break even vs owning shares/,
    );
    expect(screen.getByTestId("entry-rank-1")).not.toHaveTextContent(
      /higher CAGR/i,
    );
    expect(screen.getByTestId("entry-rank-2")).toBeInTheDocument();
    expect(screen.getByTestId("entry-rank-3")).toBeInTheDocument();
    expect(mockLeapRankMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: "NVDA",
        right: "call",
      }),
    );

    fireEvent.click(screen.getByTestId("entry-rank-1"));
    expect(screen.getByTestId("entry-results")).toBeInTheDocument();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
    expect(screen.getByTestId("entry-max-gain")).toHaveTextContent("Unlimited");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$94.20");
    expect(
      screen.getByTestId("entry-analysis-panel").querySelector("table"),
    ).toBeNull();
  });

  it("ranks long puts with annualized decline copy, not call-style return", async () => {
    mockLeapRankMutateAsync.mockResolvedValue({
      ok: true,
      symbol: "SPY",
      right: "put",
      spot: 100,
      as_of: "2026-09-06",
      expiry_from: "2027-09-06",
      expiry_to: "2028-09-06",
      expirations_used: ["2027-09-17"],
      candidates_considered: 1,
      warnings: [],
      ranks: [
        {
          rank: 1,
          contract_label: "SPY 9/17/2027 Put $110.00",
          symbol: "SPY",
          right: "put",
          strike: 110,
          expiration: "2027-09-17",
          premium: 3,
          premium_source: "mid",
          bid: 2.9,
          ask: 3.1,
          last: 3,
          dte: 376,
          implied_cagr: 0.02,
          leverage: 33.33,
          intrinsic: 10,
          extrinsic: 0,
          extrinsic_yield: 0,
          breakeven: 107,
          why_vs_stock:
            "Needs a 2.0% annualized decline in SPY to break even vs spot $100.00. This put costs $300.00 vs $10,000.00 for 100 shares (33.3× less capital). Time value is $0.00 (0.0% per year).",
          why_vs_richer:
            "Less annualized move to break even than the SPY 9/17/2027 Put $105.00 (2.0% vs 3.0%) because a higher strike.",
        },
      ],
    });
    render(<EntryAnalysisPanel />);
    fireEvent.click(screen.getByTestId("entry-right-put"));
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "SPY" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-find"));

    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-1")).toBeInTheDocument();
    });
    expect(mockLeapRankMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: "SPY", right: "put" }),
    );
    expect(screen.getByTestId("entry-rank-1")).toHaveTextContent(
      /2\.0% annualized decline to break even/,
    );
    expect(screen.getByTestId("entry-rank-1")).toHaveTextContent(
      /annualized decline in SPY to break even vs spot/,
    );
    expect(screen.getByTestId("entry-rank-1")).toHaveTextContent(
      /Less annualized move to break even/,
    );
    expect(screen.getByTestId("entry-rank-1")).not.toHaveTextContent(
      /higher CAGR/i,
    );

    fireEvent.click(screen.getByTestId("entry-rank-1"));
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$300.00");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$107.00");
  });

  it("shows an honest empty rank list when quotes fail", async () => {
    mockLeapRankMutateAsync.mockResolvedValue({
      ok: false,
      reason: "no_quote",
      message:
        "Could not fetch a live NVDA quote. Rankings are hidden so we do not invent prices.",
      ranks: [],
    });
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-find"));

    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-error")).toHaveTextContent(
        /do not invent prices/i,
      );
    });
    expect(screen.queryByTestId("entry-rank-1")).not.toBeInTheDocument();
  });

  it("shows the 429 lookup message without fake ranks", async () => {
    mockLeapRankMutateAsync.mockRejectedValue(
      new Error(
        "Too many LEAP lookups from this network. Sign in or try again later.",
      ),
    );
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "SPY" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-find"));

    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-error")).toHaveTextContent(
        /too many leap lookups/i,
      );
    });
    expect(screen.queryByTestId("entry-rank-1")).not.toBeInTheDocument();
  });

  it("does not require a loaded book to rank", async () => {
    mockLeapRankMutateAsync.mockResolvedValue(mockRankSuccess());
    render(<EntryAnalysisPanel positions={[]} />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-find"));
    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-1")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("entry-context")).not.toBeInTheDocument();
  });

  it("shows honest empty RH ranking for guests", async () => {
    mockRhChainMutateAsync.mockResolvedValue({
      ok: false,
      code: "RH_CONNECTION_REQUIRED",
      message: RH_CONNECTION_REQUIRED_COPY,
      ranks: [],
    });
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-leaps"));
    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-error")).toHaveTextContent(
        RH_CONNECTION_REQUIRED_COPY,
      );
    });
    expect(screen.queryByTestId("entry-rank-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("entry-empty")).toBeInTheDocument();
    expect(mockLeapRankMutateAsync).not.toHaveBeenCalled();
    expect(mockRhChainMutateAsync).toHaveBeenCalled();
  });

  it("empty-desk what-if and manual premium stay after guest Rank LEAPs", async () => {
    mockRhChainMutateAsync.mockResolvedValue({
      ok: false,
      code: "RH_CONNECTION_REQUIRED",
      message: RH_CONNECTION_REQUIRED_COPY,
      ranks: [],
    });
    render(<EntryAnalysisPanel />);
    fillLongCall();
    expect(screen.getByTestId("entry-results")).toBeInTheDocument();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$254.20");
    fireEvent.click(screen.getByTestId("entry-rank-leaps"));
    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-error")).toHaveTextContent(
        RH_CONNECTION_REQUIRED_COPY,
      );
    });
    expect(screen.getByTestId("entry-results")).toBeInTheDocument();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
    expect(screen.getByTestId("entry-max-gain")).toHaveTextContent("Unlimited");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$254.20");
    expect(screen.getByTestId("entry-premium")).toHaveValue("4.20");
    expect(mockLeapRankMutateAsync).not.toHaveBeenCalled();
  });

  it("signed-in without RH does not fall back to Yahoo ranking", async () => {
    mockUser.value = { id: "oth-user-b" };
    mockRhConnected.value = false;
    mockRhChainMutateAsync.mockResolvedValue({
      ok: false,
      code: "RH_CONNECTION_REQUIRED",
      message: RH_CONNECTION_REQUIRED_COPY,
      ranks: [],
    });
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-leaps"));
    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-error")).toHaveTextContent(
        RH_CONNECTION_REQUIRED_COPY,
      );
    });
    expect(mockLeapRankMutateAsync).not.toHaveBeenCalled();
    expect(screen.queryByTestId("entry-rank-1")).not.toBeInTheDocument();
  });

  it("RH SaaS wall is an honest empty, not a Connect UI", async () => {
    mockUser.value = { id: "oth-user-a" };
    mockRhConnected.value = true;
    mockRhChainMutateAsync.mockResolvedValue({
      ok: false,
      code: "RH_SAAS_WALL",
      message: "Robinhood market-data SaaS is not available for this spike.",
      ranks: [],
    });
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-leaps"));
    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-error")).toHaveTextContent(
        /not available for this spike/i,
      );
    });
    expect(screen.queryByTestId("entry-rank-1")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /connect robinhood/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reconnect/i })).not.toBeInTheDocument();
  });

  it("signed-in harness is status plus Rank LEAPs, not Connect UI", async () => {
    mockUser.value = { id: "oth-user-a" };
    mockRhConnected.value = true;
    render(<EntryAnalysisPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("entry-rh-status")).toHaveTextContent(
        /Robinhood connected/i,
      );
    });
    expect(screen.getByTestId("entry-rank-leaps")).toBeInTheDocument();
    expect(screen.getByTestId("entry-symbol")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /connect robinhood/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reconnect/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/oauth/i)).not.toBeInTheDocument();
  });

  it("maps a selected RH top result into v1 payoff", async () => {
    mockUser.value = { id: "oth-user-a" };
    mockRhConnected.value = true;
    mockRhChainMutateAsync.mockResolvedValue({
      ...mockRankSuccess(),
      provider: "robinhood",
      code: "ok",
    });
    render(<EntryAnalysisPanel />);
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.click(screen.getByTestId("entry-rank-leaps"));
    await waitFor(() => {
      expect(screen.getByTestId("entry-rank-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("entry-rank-1"));
    expect(screen.getByTestId("entry-results")).toBeInTheDocument();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
    expect(screen.getByTestId("entry-breakeven")).toHaveTextContent("$94.20");
  });
});
