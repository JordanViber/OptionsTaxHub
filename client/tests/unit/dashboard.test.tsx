import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PortfolioAnalysis, Supplemental1099Summary } from "../../lib/types";

// Mock next/navigation
jest.mock("next/link", () => {
  return ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  );
});

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
  usePathname: () => "/dashboard",
}));

// Mock auth context
const mockSignOut = jest.fn();
const mockAuthState = {
  user: {
    id: "test-user",
    email: "test@example.com",
    email_confirmed_at: "2025-01-01T00:00:00Z" as string | null,
  },
  loading: false,
  signOut: mockSignOut,
};
jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockAuthState,
}));

// Mock API hooks
let mockAnalyzeData: PortfolioAnalysis | null = null;
let mockHistoryData: Array<{
  id: string;
  filename: string;
  uploaded_at: string;
  positions_count?: number;
  total_market_value?: number;
  summary?: {
    total_market_value: number;
    total_cost_basis: number;
    total_unrealized_pnl: number;
    total_unrealized_pnl_pct: number;
    total_harvestable_losses: number;
    estimated_tax_savings: number;
    positions_count: number;
    lots_with_losses: number;
    lots_with_gains: number;
    wash_sale_flags_count: number;
    activity_first_date?: string | null;
    activity_last_date?: string | null;
    activity_transaction_count?: number;
  };
}> = [];
const mockAnalyzeMutate = jest.fn();
const mockFetchAnalysisById = jest.fn();
const mockRefetchHistory = jest.fn(async () => ({ data: mockHistoryData }));
let mockHistoryFetched = true;
let mockHistoryError: Error | null = null;
const mockFetchWashSaleChains = jest.fn(() =>
  Promise.resolve({ chains: [], hint: null }),
);
const mockCleanupOrphanHistory = jest.fn(() => Promise.resolve());
const mockDeleteAnalysis = jest.fn(() => Promise.resolve(true));

jest.mock("../../lib/api", () => ({
  useAnalyzePortfolio: () => ({
    mutate: mockAnalyzeMutate,
    isPending: false,
    error: null,
    data: mockAnalyzeData,
  }),
  useTaxProfile: () => ({
    data: null,
    error: null,
    isPending: false,
  }),
  usePortfolioHistory: () => ({
    data: mockHistoryData,
    error: mockHistoryError,
    isPending: false,
    isFetched: mockHistoryFetched,
    refetch: mockRefetchHistory,
  }),
  useBackendHealth: () => ({
    isError: false,
    isFetched: true,
  }),
  fetchAnalysisById: mockFetchAnalysisById,
  fetchWashSaleChains: mockFetchWashSaleChains,
  WASH_SALE_CHAIN_REANALYZE_HINT: "Re-analyze to see wash-sale chains",
  cleanupOrphanHistory: mockCleanupOrphanHistory,
  deleteAnalysis: mockDeleteAnalysis,
  persistGuestAnalysis: jest.fn(() => Promise.resolve(true)),
  useLeapRankMutation: () => ({
    mutateAsync: jest.fn(),
    mutate: jest.fn(),
    isPending: false,
    reset: jest.fn(),
  }),
  useRhChainMutation: () => ({
    mutateAsync: jest.fn(),
    mutate: jest.fn(),
    isPending: false,
    reset: jest.fn(),
  }),
  fetchRhStatus: jest.fn().mockResolvedValue({ connected: false }),
  getAnalysisErrorMessage: (error: unknown) =>
    error instanceof Error ? error.message : "An error occurred",
  getBackendUnreachableMessage: () =>
    "The analysis service is not responding. Please try again in a few minutes.",
}));

// Mock components
jest.mock("../../app/components/ServiceWorkerRegistration", () => ({
  __esModule: true,
  default: () => <div data-testid="service-worker-registration" />,
}));

jest.mock("../../app/components/TaxDisclaimer", () => ({
  __esModule: true,
  default: () => <div data-testid="tax-disclaimer" />,
}));

jest.mock("../../app/components/PortfolioSummaryCards", () => ({
  __esModule: true,
  default: () => <div data-testid="portfolio-summary-cards" />,
}));

jest.mock("../../app/components/PositionsTable", () => ({
  __esModule: true,
  default: () => <div data-testid="positions-table" />,
}));

jest.mock("../../app/components/HarvestingSuggestions", () => ({
  __esModule: true,
  default: () => <div data-testid="harvesting-suggestions" />,
}));

jest.mock("../../app/components/WashSaleWarning", () => ({
  __esModule: true,
  default: () => <div data-testid="wash-sale-warning" />,
}));

jest.mock("../../app/components/TipJar", () => ({
  __esModule: true,
  default: () => <div data-testid="tip-jar-dialog" />,
}));

import DashboardPage from "../../app/dashboard/page";
import { resetAutomaticSampleRerunForTests } from "../../lib/automaticSampleRerun";
import { resetPacketResumeCheckoutLock } from "../../app/components/YearClosePacketPanel";
import { resetPacketResumeLocalForTests } from "../../lib/packetResumeLocal";
import {
  PACKET_CHECKOUT_STARTED_KEY,
  PACKET_RESUME_ANALYSIS_KEY,
  PACKET_RESUME_CHECKOUT_KEY,
  PACKET_RESUME_LOCAL_KEY,
  PACKET_RESUME_SAMPLE_KEY,
} from "../../lib/safeNextPath";

const baseAnalysis: PortfolioAnalysis = {
  analysis_id: "analysis-test-1",
  positions: [],
  tax_lots: [],
  suggestions: [
    {
      symbol: "TSLA",
      suggestion_id: "TSLA::stock::2025-01-01",
      display_label: "TSLA",
      lot_details: "Tax lot opened Jan 01, 2025 at $250.00/share",
      manual_review_required: false,
      manual_review_reason: "",
      action: "SELL",
      quantity: 1,
      current_price: 200,
      cost_basis_per_share: 250,
      estimated_loss: 50,
      tax_savings_estimate: 10,
      holding_period_days: 120,
      is_long_term: false,
      wash_sale_risk: false,
      wash_sale_explanation: "",
      replacement_candidates: [],
      ai_explanation: "",
      ai_generated: false,
      priority: 1,
    },
  ],
  wash_sale_flags: [],
  summary: {
    total_market_value: 10000,
    total_cost_basis: 9000,
    total_unrealized_pnl: 1000,
    total_unrealized_pnl_pct: 11.1,
    total_harvestable_losses: 0,
    estimated_tax_savings: 0,
    positions_count: 2,
    lots_with_losses: 0,
    lots_with_gains: 2,
    wash_sale_flags_count: 0,
  },
  tax_profile: {
    filing_status: "single",
    estimated_annual_income: 75000,
    state: "CA",
    tax_year: 2025,
  },
  supplemental_1099: null,
  disclaimer: "test",
  errors: [],
  warnings: [],
};

const baseSupplemental1099: Supplemental1099Summary = {
  source_filename: "2024-1099.pdf",
  broker_name: "Robinhood",
  tax_year: 2024,
  short_term_proceeds: 281823.83,
  short_term_cost_basis: 264439.89,
  short_term_wash_sale_disallowed: 17409.64,
  short_term_net_gain: 34793.58,
  long_term_proceeds: 108.56,
  long_term_cost_basis: 141.72,
  long_term_wash_sale_disallowed: 33.16,
  long_term_net_gain: 0,
  referenced_symbols: ["CLSK", "TSLL"],
  matched_symbols: ["CLSK"],
  insights: [
    "Matched prior-year 1099 activity to 1 current symbol(s): CLSK.",
  ],
};

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
};

describe("DashboardPage", () => {
  let mockSessionValue: string | null;

  beforeEach(() => {
    jest.clearAllMocks();
    mockAuthState.user.email_confirmed_at = "2025-01-01T00:00:00Z";
    mockAuthState.loading = false;
    mockAnalyzeData = null;
    mockHistoryData = [];
    mockHistoryFetched = true;
    mockHistoryError = null;
    mockRefetchHistory.mockReset();
    mockRefetchHistory.mockImplementation(async () => ({ data: mockHistoryData }));
    mockAnalyzeMutate.mockReset();
    mockFetchAnalysisById.mockReset();
    mockFetchWashSaleChains.mockReset();
    mockFetchWashSaleChains.mockResolvedValue({ chains: [], hint: null });
    mockCleanupOrphanHistory.mockClear();
    mockDeleteAnalysis.mockClear();
    mockSessionValue = null;
    resetPacketResumeLocalForTests();
    resetPacketResumeCheckoutLock();
    resetAutomaticSampleRerunForTests();
    localStorage.removeItem(PACKET_RESUME_LOCAL_KEY);
    localStorage.removeItem(PACKET_CHECKOUT_STARTED_KEY);
    window.history.replaceState(null, "", "/dashboard");
    // Mock sessionStorage
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn((key: string) => {
          if (
            typeof key === "string" &&
            key.startsWith("optionstaxhub-packet-paid:")
          ) {
            return "cs_test_dashboard";
          }
          return mockSessionValue;
        }),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
  });

  it("renders dashboard page for authenticated user", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByRole("link", { name: /OptionsTaxHub/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /OptionsTaxHub/i })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("renders portfolio upload section", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(
      () => {
        expect(screen.getByText(/Portfolio Analysis/i)).toBeInTheDocument();
        expect(
          screen.getByText(/Robinhood 1099 for the tax year you are closing/i),
        ).toBeInTheDocument();
      },
      { timeout: 8000 },
    );
  });

  it("renders navigation buttons", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      const buttons = screen.getAllByRole("button");
      expect(buttons.length).toBeGreaterThan(0);
    });
  });

  it("renders service worker registration component", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByTestId("service-worker-registration"),
      ).toBeInTheDocument();
    });
  });

  it("shows a fresh upload chip and partial confidence warning for significant data issues", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      warnings: [
        "47 sell transaction(s) across 24 ticker(s) had no open lots at all — likely trades before the CSV start date or short sales. These are excluded from gain/loss calculations.",
        "Corporate action activity may have changed the reported share count for ASST (2 events). Position totals for ASST may be inaccurate until the brokerage CSV fully reflects the change.",
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Fresh upload")).toBeInTheDocument();
      expect(screen.getAllByText("Partial confidence").length).toBeGreaterThan(
        0,
      );
      expect(
        screen.getByText(
          /Some sells could not be matched to complete tax lots/i,
        ),
      ).toBeInTheDocument();
    });
  });

  it("shows the wash-sale chain card when the analysis has chains", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: true,
      wash_sale_chains: [
        {
          chain_id: "AMD:2026-07-15:10:e0",
          symbol: "AMD",
          reconcile_status: "matches",
          difference: null,
          plain_english:
            "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, so $300 moved into the new lot's basis. This matches your 1099 wash adjustment of $300.",
          hops: [],
        },
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-chain-card")).toBeInTheDocument();
    });
    expect(screen.getByText("Wash-sale chains")).toBeInTheDocument();
    expect(screen.getByText(/You sold 10 AMD/)).toBeInTheDocument();
    expect(mockFetchWashSaleChains).not.toHaveBeenCalled();
  });

  it("shows wash-sale chains after packet confirm without a new upload", async () => {
    const store = new Map<string, string>();
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn((key: string) => store.get(key) ?? null),
        setItem: jest.fn((key: string, value: string) => {
          store.set(key, value);
        }),
        removeItem: jest.fn((key: string) => {
          store.delete(key);
        }),
        clear: jest.fn(() => {
          store.clear();
        }),
      },
      writable: true,
    });
    const paidChain = {
      chain_id: "AMD:2026-07-15:10:e0",
      symbol: "AMD",
      reconcile_status: "matches" as const,
      difference: null,
      plain_english:
        "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, so $300 moved into the new lot's basis. This matches your 1099 wash adjustment of $300.",
      hops: [],
    };
    mockFetchWashSaleChains.mockResolvedValue({
      chains: [paidChain],
      hint: null,
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = jest.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/year-close-packet/checkout")) {
        return {
          ok: true,
          json: async () => ({
            already_paid: true,
            session_id: "cs_test_confirm",
            analysis_id: "analysis-test-1",
          }),
        };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    }) as unknown as typeof fetch;
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      wash_sale_chains: [],
    };

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(
          screen.getByRole("button", { name: /Pay \$49/i }),
        ).toBeEnabled();
      });
      expect(screen.queryByTestId("wash-sale-chain-card")).not.toBeInTheDocument();
      expect(mockFetchWashSaleChains).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));

      await waitFor(() => {
        expect(screen.getByTestId("wash-sale-chain-card")).toBeInTheDocument();
      });
      expect(screen.getByText(/You sold 10 AMD/)).toBeInTheDocument();
      expect(mockFetchWashSaleChains).toHaveBeenCalledWith("analysis-test-1");
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("shows the re-analyze hint when a paid snapshot has no chains", async () => {
    mockFetchWashSaleChains.mockResolvedValue({
      chains: [],
      hint: "Re-analyze to see wash-sale chains",
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      wash_sale_chains: [],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-chain-hint")).toHaveTextContent(
        "Re-analyze to see wash-sale chains",
      );
    });
    expect(screen.queryByTestId("wash-sale-chain-card")).not.toBeInTheDocument();
    expect(mockFetchWashSaleChains).toHaveBeenCalledWith("analysis-test-1");
  });

  it("does not refetch wash-sale chains when the same analysis rerenders", async () => {
    mockFetchWashSaleChains.mockResolvedValue({
      chains: [],
      hint: "Re-analyze to see wash-sale chains",
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      wash_sale_chains: [],
    };

    const { rerender } = render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(mockFetchWashSaleChains).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByTestId("wash-sale-chain-hint")).toHaveTextContent(
      "Re-analyze to see wash-sale chains",
    );

    rerender(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-chain-hint")).toBeInTheDocument();
    });
    expect(mockFetchWashSaleChains).toHaveBeenCalledTimes(1);
  });

  it("fetches wash-sale chains again after a cancelled in-flight request", async () => {
    const resolvers: Array<
      (value: {
        chains: Array<{
          chain_id: string;
          symbol: string;
          reconcile_status: "matches";
          difference: null;
          plain_english: string;
          hops: [];
        }>;
        hint: string | null;
      }) => void
    > = [];
    mockFetchWashSaleChains.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        }),
    );
    const paidChain = {
      chain_id: "AMD:2026-07-15:10:e0",
      symbol: "AMD",
      reconcile_status: "matches" as const,
      difference: null,
      plain_english:
        "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, so $300 moved into the new lot's basis. This matches your 1099 wash adjustment of $300.",
      hops: [] as [],
    };
    const paid = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      wash_sale_chains: [],
    };
    const sample = {
      ...baseAnalysis,
      analysis_id: "sample-analysis",
      sample_run: true,
      wash_sale_chains: [],
    };
    mockAnalyzeData = paid;

    const { rerender } = render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(mockFetchWashSaleChains).toHaveBeenCalledTimes(1);
    });

    mockAnalyzeData = sample;
    rerender(<DashboardPage />);
    await waitFor(() => {
      expect(screen.queryByTestId("wash-sale-chain-card")).not.toBeInTheDocument();
    });

    await act(async () => {
      resolvers[0]?.({ chains: [paidChain], hint: null });
    });
    expect(screen.queryByText(/You sold 10 AMD/)).not.toBeInTheDocument();

    mockAnalyzeData = paid;
    rerender(<DashboardPage />);
    await waitFor(() => {
      expect(mockFetchWashSaleChains).toHaveBeenCalledTimes(2);
    });
    await act(async () => {
      resolvers[1]?.({ chains: [paidChain], hint: null });
    });
    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-chain-card")).toBeInTheDocument();
    });
    expect(screen.getByText(/You sold 10 AMD/)).toBeInTheDocument();
  });

  it("shows wash-sale-chain-hint when a paid packet's chain fetch is forbidden", async () => {
    mockFetchWashSaleChains.mockResolvedValue({
      chains: [],
      hint: "Re-analyze to see wash-sale chains",
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: true,
      wash_sale_chains: [],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-chain-hint")).toHaveTextContent(
        "Re-analyze to see wash-sale chains",
      );
    });
    expect(screen.queryByTestId("wash-sale-chain-card")).not.toBeInTheDocument();
    expect(mockFetchWashSaleChains).toHaveBeenCalledWith("analysis-test-1");
  });

  it("does not fetch wash-sale chains or show the hint while the packet is unpaid", async () => {
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn(() => null),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
    mockFetchWashSaleChains.mockResolvedValue({
      chains: [],
      hint: "Re-analyze to see wash-sale chains",
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      wash_sale_chains: [],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeInTheDocument();
    });
    expect(mockFetchWashSaleChains).not.toHaveBeenCalled();
    expect(screen.queryByTestId("wash-sale-chain-hint")).not.toBeInTheDocument();
    expect(screen.queryByTestId("wash-sale-chain-card")).not.toBeInTheDocument();
  });

  it("shows the re-analyze hint when the wash-sale chain refetch fails", async () => {
    mockFetchWashSaleChains.mockRejectedValue(new Error("network down"));
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      wash_sale_chains: [],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-chain-hint")).toHaveTextContent(
        "Re-analyze to see wash-sale chains",
      );
    });
    expect(screen.queryByTestId("wash-sale-chain-card")).not.toBeInTheDocument();
    expect(mockFetchWashSaleChains).toHaveBeenCalledWith("analysis-test-1");
  });

  it("shows restored-session messaging when analysis is recovered from browser storage", async () => {
    mockSessionValue = JSON.stringify(baseAnalysis);

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Restored from browser")).toBeInTheDocument();
      expect(
        screen.getByText(/This result was restored from browser storage/i),
      ).toBeInTheDocument();
      expect(screen.getAllByText("High confidence").length).toBeGreaterThan(0);
    });
  });

  it("keeps the 1099 panel in sync when a supplemented result is restored", async () => {
    mockSessionValue = JSON.stringify({
      ...baseAnalysis,
      supplemental_1099: {
        ...baseSupplemental1099,
        source_filename: "2024-robinhood-1099.pdf",
      },
    } satisfies PortfolioAnalysis);

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("Included in restored result"),
      ).toBeInTheDocument();
      expect(screen.getByText("2024-robinhood-1099.pdf")).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /Remove 1099 PDF/i }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByText(/Need help with edge-case reconciliation/i),
      ).not.toBeInTheDocument();
    });
  });

  it("queues a selected 1099 for the next CSV analysis when no CSV has been uploaded yet", async () => {
    const { container } = render(<DashboardPage />, {
      wrapper: createWrapper(),
    });

    const pdfInput = container.querySelector(
      'input[type="file"][accept=".pdf,application/pdf"]',
    );

    if (!(pdfInput instanceof HTMLInputElement)) {
      throw new TypeError("PDF input not found");
    }

    fireEvent.change(pdfInput, {
      target: {
        files: [
          new File(["pdf"], "queued-1099.pdf", { type: "application/pdf" }),
        ],
      },
    });

    await waitFor(() => {
      expect(screen.getByText("Ready for next analysis")).toBeInTheDocument();
      expect(screen.getByText("queued-1099.pdf")).toBeInTheDocument();
    });

    expect(mockAnalyzeMutate).not.toHaveBeenCalled();
  });

  it("shows recommended next steps above the detailed tabs", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      wash_sale_flags: [
        {
          symbol: "TSLA",
          sale_date: "2025-01-01",
          sale_quantity: 1,
          sale_loss: 100,
          repurchase_date: "2025-01-15",
          repurchase_quantity: 1,
          disallowed_loss: 100,
          adjusted_cost_basis: 300,
          explanation: "Repurchased within 30 days",
        },
      ],
      warnings: [
        "Live prices were unavailable for CEP, so the analysis used the CSV-provided price instead.",
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Recommended next steps")).toBeInTheDocument();
      expect(
        screen.getByText(/Start with the 1 lot in the harvest queue/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          /Review the wash-sale radar before relying on losses/i,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/check the data quality notes/i),
      ).toBeInTheDocument();
    });
  });

  it("does not count purchase-fill lots in the harvest next step", async () => {
    const note = "Purchase price from your CSV, no live quote";
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      suggestions: [
        ...baseAnalysis.suggestions,
        {
          ...baseAnalysis.suggestions[0],
          suggestion_id: "AAPL-fill-1",
          symbol: "AAPL",
          display_label: "AAPL",
          estimated_loss: 0,
          tax_savings_estimate: 0,
          price_note: note,
        },
        {
          ...baseAnalysis.suggestions[0],
          suggestion_id: "MSFT-fill-1",
          symbol: "MSFT",
          display_label: "MSFT",
          estimated_loss: 133,
          tax_savings_estimate: 0,
          price_note: note,
        },
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText(
          "2 lots use your CSV purchase price, no live quote, loss unknown.",
        ),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/3 lots/i),
    ).not.toBeInTheDocument();
  });

  it("explains when automated suggestions were skipped for split-affected symbols", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      warnings: [
        "Skipped automated harvesting suggestions for ASST stock lots because a stock split or corporate action changed the share count. Verify ASST manually before acting on any loss estimate.",
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Manual review needed")).toBeInTheDocument();
      expect(
        screen.getByText(/The harvest queue skipped ASST/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Upload your Robinhood 1099 PDF for the tax year you are closing/i),
      ).toBeInTheDocument();
    });
  });

  it("shows supplemental 1099 insights when the analysis includes them", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      supplemental_1099: baseSupplemental1099,
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("year-close-packet-panel")).toBeInTheDocument();
      expect(screen.getByTestId("year-close-packet-panel")).toHaveTextContent(
        "Year-close packet",
      );
      expect(screen.getByTestId("year-close-packet-panel")).toHaveTextContent(
        "$49",
      );
      expect(screen.getByText(/Lot ledger \(/i)).toBeInTheDocument();
      expect(
        screen.getByText("Previous-year 1099 supplement applied"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/previous-year supplement — not a same-year 1099 vs export compare/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText("Included in current analysis"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Using Robinhood 1099 PDF for tax year 2024/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/Short-term proceeds/i)).toBeInTheDocument();
      expect(screen.getByText("$281,823.83")).toBeInTheDocument();
      expect(screen.getByText(/Long-term proceeds/i)).toBeInTheDocument();
      expect(screen.getAllByText("$17,442.80").length).toBeGreaterThan(0);
      expect(
        screen.getByText(
          /Matched prior-year 1099 activity to 1 current symbol/i,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Robinhood 1099 uses settlement date/i),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Year-end short" })).toHaveAttribute(
        "title",
        expect.stringMatching(/SPX expiring 12\/31/i),
      );
      expect(
        screen.getByRole("button", { name: "Year-end short" }).getAttribute("title") ??
          "",
      ).not.toMatch(/2,699/);
      expect(
        screen.getByText(
          /Options and credit-spread wash-sale treatment can differ from the broker 1099/i,
        ),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/Need help with edge-case reconciliation/i),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/Re-run with 1099/i)).not.toBeInTheDocument();
    });
  });

  it("automatically re-analyzes the latest CSV when a 1099 is selected", async () => {
    const sessionAnalysis: PortfolioAnalysis = {
      ...baseAnalysis,
      warnings: [
        "Skipped automated harvesting suggestions for ASST stock lots because a stock split or corporate action changed the share count. Verify ASST manually before acting on any loss estimate.",
      ],
    };
    mockSessionValue = JSON.stringify(sessionAnalysis);

    const { container } = render(<DashboardPage />, {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(screen.getByText("Restored from browser")).toBeInTheDocument();
    });

    const csvInput = container.querySelector(
      'input[type="file"][accept=".csv"]',
    );

    if (!(csvInput instanceof HTMLInputElement)) {
      throw new TypeError("CSV input not found");
    }

    fireEvent.change(csvInput, {
      target: {
        files: [
          new File(["symbol,qty\nTSLA,1"], "portfolio.csv", {
            type: "text/csv",
          }),
        ],
      },
    });

    await waitFor(() => {
      expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
    });

    const uploadSummary = screen.queryByText("Upload CSV or 1099");
    if (uploadSummary) {
      fireEvent.click(uploadSummary);
    }
    const pdfInput = container.querySelector(
      'input[type="file"][accept=".pdf,application/pdf"]',
    );
    if (!(pdfInput instanceof HTMLInputElement)) {
      throw new TypeError("PDF input not found");
    }

    fireEvent.change(pdfInput, {
      target: {
        files: [
          new File(["pdf"], "supplement.pdf", { type: "application/pdf" }),
        ],
      },
    });

    await waitFor(() => {
      expect(mockAnalyzeMutate).toHaveBeenCalledTimes(2);
    });
    expect(screen.getByText("Auto-applied to latest CSV")).toBeInTheDocument();

    expect(mockAnalyzeMutate.mock.calls[1][0]).toMatchObject({
      file: expect.objectContaining({ name: "portfolio.csv" }),
      supplemental1099File: expect.objectContaining({ name: "supplement.pdf" }),
    });
    expect(screen.queryByText(/Re-run with 1099/i)).not.toBeInTheDocument();
  });

  it("removes an applied 1099 and refreshes the latest CSV analysis without it", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      supplemental_1099: baseSupplemental1099,
    };

    const { container } = render(<DashboardPage />, {
      wrapper: createWrapper(),
    });

    const csvInput = container.querySelector(
      'input[type="file"][accept=".csv"]',
    );

    if (!(csvInput instanceof HTMLInputElement)) {
      throw new TypeError("CSV input not found");
    }

    fireEvent.change(csvInput, {
      target: {
        files: [
          new File(["symbol,qty\nTSLA,1"], "portfolio.csv", {
            type: "text/csv",
          }),
        ],
      },
    });

    await waitFor(() => {
      expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(screen.getByText("Upload CSV or 1099"));
    expect(
      screen.getByRole("button", { name: /Remove 1099 PDF/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Remove 1099 PDF/i }));

    await waitFor(() => {
      expect(mockAnalyzeMutate).toHaveBeenCalledTimes(2);
    });

    expect(mockAnalyzeMutate.mock.calls[1][0]).toMatchObject({
      file: expect.objectContaining({ name: "portfolio.csv" }),
    });
    expect(mockAnalyzeMutate.mock.calls[1][0].supplemental1099File).toBeUndefined();
  });

  it("loads a saved analysis from history and shows saved-history messaging", async () => {
    mockHistoryData = [
      {
        id: "analysis-1",
        filename: "saved.csv",
        uploaded_at: "2026-03-08T12:00:00Z",
        positions_count: 2,
        total_market_value: 1000,
      },
    ];
    mockFetchAnalysisById.mockResolvedValue({
      id: "analysis-1",
      filename: "saved.csv",
      result: baseAnalysis,
    });

    render(<DashboardPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getAllByRole("button", { name: /Saved runs/i })[0]);
    fireEvent.click(await screen.findByText("saved.csv"));

    await waitFor(() => {
      expect(screen.getByText("Saved analysis")).toBeInTheDocument();
      expect(
        screen.getByText(/This result was loaded from saved history/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Loaded saved analysis: saved.csv/i),
      ).toBeInTheDocument();
    });
  });

  it("offers to merge new activity with a saved trade book", async () => {
    mockHistoryData = [
      {
        id: "analysis-1",
        filename: "full-history.csv",
        uploaded_at: "2026-01-02T12:00:00Z",
        positions_count: 2,
        total_market_value: 1000,
        summary: {
          total_market_value: 1000,
          total_cost_basis: 800,
          total_unrealized_pnl: 200,
          total_unrealized_pnl_pct: 25,
          total_harvestable_losses: 0,
          estimated_tax_savings: 0,
          positions_count: 2,
          lots_with_losses: 0,
          lots_with_gains: 2,
          wash_sale_flags_count: 0,
          activity_first_date: "2023-06-01",
          activity_last_date: "2026-01-01",
          activity_transaction_count: 40,
        },
      },
    ];

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText(/We'll add new activity to your book from full-history.csv/i),
      ).toBeInTheDocument();
      expect(
        screen.getByLabelText(/Start a new book instead/i),
      ).toBeInTheDocument();
    });
  });

  it("shows one guest state when a held run is waiting for a new upload", async () => {
    const previousUser = mockAuthState.user;
    mockAuthState.user = null;
    const saved = JSON.stringify({ ...baseAnalysis, sample_run: true });
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn((key: string) => {
          if (key === "oth-upload-intent") {
            return "1";
          }
          if (key === "optionstaxhub-analysis") {
            return saved;
          }
          return null;
        }),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(
          screen.getByRole("button", { name: "Keep current run" }),
        ).toBeInTheDocument();
      });
      expect(
        screen.queryByText(/Get started with your first analysis/i),
      ).not.toBeInTheDocument();
    } finally {
      mockAuthState.user = previousUser;
    }
  });

  it("shows first-run empty state before any analysis exists", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText(/Get started with your first analysis/i),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("entry-analysis-panel")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tax" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.getByRole("link", { name: "Options" })).toHaveAttribute(
      "href",
      "/options",
    );
    expect(screen.getByTestId("header-upload")).toBeInTheDocument();
    expect(screen.getByText(/Export from Robinhood/i)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Download sample CSV/i }),
    ).toHaveAttribute("href", "/sample-robinhood-transactions.csv");
    expect(
      screen.getByText(
        /attach the Robinhood 1099 PDF for the tax year you are closing next to the CSV/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Savings estimates use your tax profile/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/toward Schedule D/i)).toBeInTheDocument();
    expect(screen.getByText(/not a Form 8949 rebuild/i)).toBeInTheDocument();
    expect(screen.getByText(/free, no card/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        /Wash flags on a 1099 are often scoped to that one account/i,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/year-end clear around 12\/31/i)).toBeInTheDocument();
    expect(screen.queryByText(/options traders only/i)).not.toBeInTheDocument();
  });

  it("Open the 2026 sample attaches the CSV and the 2026 1099 together", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = jest.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("sample-robinhood-1099-2026.pdf")) {
        return {
          ok: true,
          blob: async () =>
            new Blob(["%PDF-1.4 sample"], { type: "application/pdf" }),
        };
      }
      if (url.includes("sample-robinhood-transactions.csv")) {
        return {
          ok: true,
          blob: async () =>
            new Blob(["symbol,qty\nAAPL,1"], { type: "text/csv" }),
        };
      }
      return { ok: false, blob: async () => new Blob([]) };
    }) as typeof fetch;

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });

      fireEvent.click(
        await screen.findByRole("button", { name: "Try the sample" }),
      );

      await waitFor(() => {
        expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
      });
      expect(mockAnalyzeMutate.mock.calls[0][0]).toMatchObject({
        file: expect.objectContaining({
          name: "sample-robinhood-transactions.csv",
        }),
        supplemental1099File: expect.objectContaining({
          name: "sample-robinhood-1099-2026.pdf",
        }),
      });
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/sample-robinhood-transactions.csv",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/sample-robinhood-1099-2026.pdf",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });


  it("prompts for an optional 1099 after first-run when none is attached", async () => {
    mockAnalyzeData = baseAnalysis;

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("Optional: 1099 for the tax year you are closing"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Upload your Robinhood 1099 PDF for the tax year you are closing/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Totals compare stays free/i),
      ).toBeInTheDocument();
    });
  });

  it("shows a clear 1099 warning while still displaying CSV results", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      warnings: [
        "Supplemental 1099 PDF could not be parsed and was ignored for this analysis.",
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 warning")).toBeInTheDocument();
      expect(
        screen.getAllByText(
          /Supplemental 1099 PDF could not be parsed and was ignored/i,
        ).length,
      ).toBeGreaterThan(0);
      expect(
        screen.getByText(/CSV analysis still completed/i),
      ).toBeInTheDocument();
      expect(screen.getByTestId("portfolio-summary-cards")).toBeInTheDocument();
    });
  });

  it("keeps a year-mismatched 1099 visible instead of treating it as the expected year", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: 2023,
        insights: [
          "The supplemental Robinhood 1099 is for tax year 2023, not the expected prior year (2024).",
        ],
      },
      warnings: [
        "The supplemental 1099 PDF was parsed successfully, but its tax year does not match the expected prior year for this analysis.",
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText(/Using Robinhood 1099 PDF for tax year 2023/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/not the expected prior year \(2024\)/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText("1099 warning"),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/Using Robinhood 1099 PDF for tax year 2024/i),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("1099 vs your export")).not.toBeInTheDocument();
    });
  });

  it("shows 1099 vs export when the 1099 tax year matches the dashboard year", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      wash_sale_flags: [
        {
          symbol: "AMD",
          sale_date: "2024-07-15",
          sale_quantity: 10,
          sale_loss: 300,
          repurchase_date: "2024-07-24",
          repurchase_quantity: 10,
          disallowed_loss: 300,
          adjusted_cost_basis: 1550,
          explanation: "Wash sale on AMD",
        },
      ],
      summary: {
        ...baseAnalysis.summary,
        realized_summary: {
          tax_year: 2024,
          st_gains: 0,
          st_losses: -300,
          lt_gains: 0,
          lt_losses: 0,
          net_st: -300,
          net_lt: 0,
          total_net: -300,
          transactions_count: 1,
        },
      },
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: 2024,
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 vs your export")).toBeInTheDocument();
      expect(screen.getByTestId("1099-broker-column")).toBeInTheDocument();
      expect(screen.getByTestId("1099-export-column")).toBeInTheDocument();
      expect(
        screen.getByText(/Broker 1099 \(settlement date\)/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/This export \(trade date\)/i)).toBeInTheDocument();
      const totalsHint = screen.getByRole("button", {
        name: "Why these totals disagree",
      });
      expect(totalsHint).toHaveAttribute(
        "title",
        expect.stringMatching(/not a software bug/i),
      );
      expect(totalsHint).toHaveAttribute(
        "title",
        expect.stringMatching(/settlement date/i),
      );
      expect(totalsHint).toHaveAttribute(
        "title",
        expect.stringMatching(/SPX expiring 12\/31/),
      );
      expect(totalsHint.getAttribute("title") ?? "").not.toMatch(/2,699/);
      expect(screen.queryByText(/r\/options/i)).not.toBeInTheDocument();
      expect(
        screen.queryByText("Previous-year 1099 supplement applied"),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("year-close-packet-panel")).toHaveTextContent(
        "$49",
      );
    });
  });

  it("keeps unpaid non-sample lot-match as counts teaser without a lot table", async () => {
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn(() => mockSessionValue),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: 2024,
        source_filename: "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf",
      },
      lot_match_report: {
        matched: [],
        gap: [],
        unmatched: [],
        matched_count: 0,
        gap_count: 2,
        unmatched_count: 4,
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 vs your export")).toBeInTheDocument();
    });
    expect(screen.getByTestId("lot-match-counts")).toHaveTextContent(
      "Matched 0 · Gap 2 · Unmatched 4",
    );
    expect(
      screen.getByText(/Pay \$49 for lot-matched 1099-B/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("lot-match-row-NVDA")).not.toBeInTheDocument();
    expect(screen.queryByText("1099_only")).not.toBeInTheDocument();
  });

  it("guest unpaid non-sample lot-match stays counts teaser without a lot table", async () => {
    const previousUser = mockAuthState.user;
    mockAuthState.user = null;
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn(() => mockSessionValue),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: false,
      packet_unlocked: false,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: 2024,
        source_filename: "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf",
      },
      lot_match_report: {
        matched: [],
        gap: [],
        unmatched: [],
        matched_count: 0,
        gap_count: 2,
        unmatched_count: 4,
      },
    };

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("1099 vs your export")).toBeInTheDocument();
      });
      expect(screen.getByTestId("lot-match-counts")).toHaveTextContent(
        "Matched 0 · Gap 2 · Unmatched 4",
      );
      expect(
        screen.getByText(/Pay \$49 for lot-matched 1099-B/i),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("lot-match-row-NVDA")).not.toBeInTheDocument();
      expect(screen.queryByText("1099_only")).not.toBeInTheDocument();
    } finally {
      mockAuthState.user = previousUser;
    }
  });

  it("sends guests from Saved runs and Settings to sign-in", async () => {
    const previousUser = mockAuthState.user;
    mockAuthState.user = null;
    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      fireEvent.click(screen.getAllByRole("button", { name: "Saved runs" })[0]);
      expect(mockPush).toHaveBeenCalledWith(
        "/auth/signin?next=/dashboard&reason=runs",
      );
      expect(screen.getByTestId("tax-settings-link")).toHaveAttribute(
        "href",
        "/auth/signin?next=/dashboard&reason=profile",
      );
    } finally {
      mockAuthState.user = previousUser;
    }
  });

  it("does not show fallback-price confidence on a fixed sample run", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: true,
      warnings: [
        "Live prices were unavailable for AAPL, so the analysis used the CSV-provided price instead.",
      ],
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getAllByText("High confidence").length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("Moderate confidence")).not.toBeInTheDocument();
    expect(screen.queryByText(/fallback pricing/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Live prices were unavailable/i),
    ).not.toBeInTheDocument();
  });

  it("labels unrealized P&L with the CSV date and does not call it live", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      price_basis: {
        quotes: [
          {
            symbol: "TSLA",
            price: 200,
            source: "csv",
            provider: null,
            as_of: "2026-08-12",
          },
        ],
        live: false,
        csv_as_of: "2026-08-12",
        label: "Prices as of your CSV (2026-08-12)",
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getAllByText("Moderate confidence").length).toBeGreaterThan(0);
    });
    expect(
      screen.getAllByText("Prices as of your CSV (2026-08-12)").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/prices on this page are marked with your CSV date/i)
        .length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/^Live /)).not.toBeInTheDocument();
    expect(screen.queryByText(/current price/i)).not.toBeInTheDocument();
  });

  it("does not treat a $300 loss + $300 disallowed as a settlement gap on the dashboard compare", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      wash_sale_flags: [
        {
          symbol: "AMD",
          sale_date: "2024-07-15",
          sale_quantity: 10,
          sale_loss: 300,
          repurchase_date: "2024-07-24",
          repurchase_quantity: 10,
          disallowed_loss: 300,
          adjusted_cost_basis: 1550,
          explanation: "Wash sale on AMD",
        },
      ],
      summary: {
        ...baseAnalysis.summary,
        realized_summary: {
          tax_year: 2024,
          st_gains: 0,
          st_losses: -300,
          lt_gains: 0,
          lt_losses: 0,
          net_st: -300,
          net_lt: 0,
          total_net: -300,
          transactions_count: 1,
        },
      },
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: 2024,
        short_term_proceeds: 1200,
        short_term_cost_basis: 1500,
        short_term_net_gain: 0,
        long_term_proceeds: 0,
        long_term_cost_basis: 0,
        long_term_net_gain: 0,
        short_term_wash_sale_disallowed: 300,
        long_term_wash_sale_disallowed: 0,
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 vs your export")).toBeInTheDocument();
      expect(screen.getByTestId("1099-broker-column")).toHaveTextContent("$0.00");
      expect(screen.getByTestId("1099-broker-column")).toHaveTextContent(
        "$300.00",
      );
      expect(screen.getByTestId("1099-export-column")).toHaveTextContent("$0.00");
      expect(screen.getByTestId("1099-export-column")).toHaveTextContent(
        "$300.00",
      );
      expect(screen.getByTestId("1099-export-column")).not.toHaveTextContent(
        "-$300.00",
      );
    });
  });

  it("shows unknown 1099 year as distinct copy, not previous-year mismatch or a same-year compare", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: null,
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 tax year unknown")).toBeInTheDocument();
      expect(
        screen.getByText(
          /tax year could not be determined\. That is not a year mismatch/i,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Using Robinhood 1099 PDF for tax year unknown/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          /tax year could not be determined, so this is not a same-year compare and not a previous-year mismatch/i,
        ),
      ).toBeInTheDocument();
      expect(screen.getByTestId("unknown-year-1099-supplement")).toBeInTheDocument();
      expect(
        screen.queryByText("Previous-year 1099 supplement applied"),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("1099 vs your export")).not.toBeInTheDocument();
      expect(
        screen.queryByText(/same year as this export/i),
      ).not.toBeInTheDocument();
    });
  });

  it("shows 2026 sample 1099 vs export as a same-year compare without unlocking download", async () => {
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn(() => mockSessionValue),
        setItem: jest.fn(),
        removeItem: jest.fn(),
        clear: jest.fn(),
      },
      writable: true,
    });
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: true,
      packet_unlocked: false,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2026 },
      wash_sale_flags: [
        {
          symbol: "NVDA",
          sale_date: "2026-02-18",
          sale_quantity: 12,
          sale_loss: 384,
          repurchase_date: "2026-03-04",
          repurchase_quantity: 12,
          disallowed_loss: 384,
          adjusted_cost_basis: 3408,
          explanation: "Wash sale on NVDA",
        },
        {
          symbol: "TSLA",
          sale_date: "2026-03-20",
          sale_quantity: 4,
          sale_loss: 240,
          repurchase_date: "2026-04-08",
          repurchase_quantity: 4,
          disallowed_loss: 240,
          adjusted_cost_basis: 1700,
          explanation: "Wash sale on TSLA",
        },
        {
          symbol: "AMD",
          sale_date: "2026-07-15",
          sale_quantity: 10,
          sale_loss: 300,
          repurchase_date: "2026-07-24",
          repurchase_quantity: 10,
          disallowed_loss: 300,
          adjusted_cost_basis: 1550,
          explanation: "Wash sale on AMD",
        },
      ],
      summary: {
        ...baseAnalysis.summary,
        realized_summary: {
          tax_year: 2026,
          st_gains: 0,
          st_losses: -924,
          lt_gains: 0,
          lt_losses: 0,
          net_st: -924,
          net_lt: 0,
          total_net: -924,
          transactions_count: 3,
        },
      },
      supplemental_1099: {
        ...baseSupplemental1099,
        source_filename: "sample-robinhood-1099-2026.pdf",
        tax_year: 2026,
        short_term_proceeds: 8315,
        short_term_cost_basis: 6540,
        short_term_wash_sale_disallowed: 924,
        short_term_net_gain: 2699,
        long_term_proceeds: 0,
        long_term_cost_basis: 0,
        long_term_wash_sale_disallowed: 0,
        long_term_net_gain: 0,
        referenced_symbols: ["AMD", "NVDA", "SPX", "TSLA"],
        matched_symbols: ["AMD", "NVDA", "TSLA"],
      },
      lot_match_report: {
        matched: [],
        gap: [
          {
            status: "matched_settlement_gap",
            symbol: "NVDA",
            quantity: 12,
            date_sold_1099: "2026-02-20",
            export_trade_date: "2026-02-18",
            proceeds_1099: 2976,
            proceeds_export: 2976,
          },
          {
            status: "matched_settlement_gap",
            symbol: "TSLA",
            quantity: 4,
            date_sold_1099: "2026-03-22",
            export_trade_date: "2026-03-20",
            proceeds_1099: 1460,
            proceeds_export: 1460,
          },
          {
            status: "matched_settlement_gap",
            symbol: "AMD",
            quantity: 10,
            date_sold_1099: "2026-07-17",
            export_trade_date: "2026-07-15",
            proceeds_1099: 1200,
            proceeds_export: 1200,
          },
        ],
        unmatched: [
          {
            status: "1099_only",
            symbol: "SPX",
            quantity: 1,
            date_sold_1099: "2027-01-02",
            export_trade_date: null,
            proceeds_1099: 2699,
            proceeds_export: 0,
          },
        ],
        matched_count: 0,
        gap_count: 3,
        unmatched_count: 1,
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 vs your export")).toBeInTheDocument();
    });
    expect(screen.getByTestId("1099-broker-column")).toHaveTextContent(
      "$2,699.00",
    );
    expect(screen.getByTestId("1099-export-column")).toHaveTextContent("$0.00");
    expect(screen.getByTestId("lot-match-counts")).toHaveTextContent(
      "Matched 0 · Gap 3 · Unmatched 1",
    );
    expect(screen.getByTestId("lot-match-row-NVDA")).toHaveTextContent(
      "matched_settlement_gap",
    );
    const spxRow = screen.getByTestId("lot-match-row-SPX");
    expect(spxRow).toHaveTextContent("1099_only");
    expect(spxRow).toHaveTextContent("$2,699.00");
    expect(
      screen.queryByText(/Pay \$49 for lot-matched 1099-B/i),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByText(/not a filed Form 8949/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByText("Previous-year 1099 supplement applied"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("year-close-packet-panel")).toHaveTextContent(
      "$49",
    );
    expect(screen.getByRole("button", { name: "Pay $49" })).toBeInTheDocument();
  });

  it("keeps 2026 sample + 2024 fixture as previous-year supplement, not a same-year compare", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2026 },
      supplemental_1099: {
        ...baseSupplemental1099,
        tax_year: 2024,
        source_filename: "c15f7458-e9d5-4dfb-a985-351df5a36cde.pdf",
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("Previous-year 1099 supplement applied"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Using Robinhood 1099 PDF for tax year 2024/i),
      ).toBeInTheDocument();
      expect(screen.queryByText("1099 vs your export")).not.toBeInTheDocument();
      expect(
        screen.queryByText(/same year as this export/i),
      ).not.toBeInTheDocument();
    });
  });

  it("keeps the options desk off the tax page and orders results around the answer", async () => {
    mockAnalyzeData = { ...baseAnalysis };
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("tax-section-kpis")).toBeInTheDocument();
    });
    const sectionOrder = [
      "tax-section-kpis",
      "tax-section-1099",
      "tax-section-wash",
      "tax-section-harvest",
      "tax-upload",
      "tax-section-packet",
    ];
    const sections = sectionOrder.map((id) => screen.getByTestId(id));
    for (let index = 0; index < sections.length - 1; index += 1) {
      expect(
        sections[index].compareDocumentPosition(sections[index + 1]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(screen.queryByTestId("entry-analysis-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("header-upload")).toBeInTheDocument();
    expect(screen.getByText("Upload CSV or 1099")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Harvest queue/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Lot ledger/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Options" })).toHaveAttribute(
      "href",
      "/options",
    );
  });

  it("signs out through the shared header menu", async () => {
    mockSignOut.mockResolvedValue(undefined);
    sessionStorage.setItem("optionstaxhub-analysis", "{\"ok\":true}");
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    queryClient.setQueryData(["portfolio-history", "test-user"], [{ id: "h1" }]);
    queryClient.setQueryData(["tax-profile", "test-user"], { tax_year: 2026 });

    render(
      <QueryClientProvider client={queryClient}>
        <DashboardPage />
      </QueryClientProvider>,
    );

    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now()));
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "analysis-test-1",
        taxYear: 2025,
      }),
    );
    fireEvent.click(await screen.findByTestId("header-account"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign Out" }));

    await waitFor(() => {
      expect(mockSignOut).toHaveBeenCalledTimes(1);
    });
    expect(sessionStorage.removeItem).toHaveBeenCalledWith("optionstaxhub-analysis");
    expect(sessionStorage.removeItem).toHaveBeenCalledWith(PACKET_RESUME_CHECKOUT_KEY);
    expect(sessionStorage.removeItem).toHaveBeenCalledWith(PACKET_RESUME_SAMPLE_KEY);
    expect(sessionStorage.removeItem).toHaveBeenCalledWith(PACKET_RESUME_ANALYSIS_KEY);
    expect(sessionStorage.getItem("optionstaxhub-analysis")).toBeNull();
    expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
    expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toBeNull();
    expect(
      queryClient.getQueryData(["portfolio-history", "test-user"]),
    ).toBeUndefined();
    expect(queryClient.getQueryData(["tax-profile", "test-user"])).toBeUndefined();
    expect(mockPush).toHaveBeenCalledWith("/auth/signin");
  });

  it("redirects unconfirmed users away from the dashboard", async () => {
    mockAuthState.user.email_confirmed_at = null;
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/auth/confirm-email");
    });
    expect(screen.queryByText(/Portfolio Analysis/i)).not.toBeInTheDocument();
  });

  it("sends an unconfirmed resume visit to confirm-email with next", async () => {
    window.history.pushState({}, "", "/dashboard?resume_checkout=1");
    mockAuthState.user.email_confirmed_at = null;
    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(mockPush).toHaveBeenCalledWith(
          `/auth/confirm-email?next=${encodeURIComponent("/dashboard?resume_checkout=1")}`,
        );
      });
    } finally {
      window.history.pushState({}, "", "/dashboard");
    }
  });

  it("re-runs the sample once when checkout says the guest snapshot is gone", async () => {
    resetAutomaticSampleRerunForTests();
    resetPacketResumeCheckoutLock();
    const store = new Map<string, string>();
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn((key: string) => store.get(key) ?? null),
        setItem: jest.fn((key: string, value: string) => {
          store.set(key, value);
        }),
        removeItem: jest.fn((key: string) => {
          store.delete(key);
        }),
        clear: jest.fn(() => {
          store.clear();
        }),
      },
      writable: true,
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = jest.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/year-close-packet/checkout")) {
        return {
          ok: false,
          status: 409,
          json: async () => ({
            detail:
              "This guest snapshot must be restored from its matching analysis before checkout.",
          }),
        };
      }
      if (url.includes("sample-robinhood")) {
        return {
          ok: true,
          blob: async () => new Blob(["sample"], { type: "text/csv" }),
        };
      }
      return { ok: true, json: async () => ({}) };
    }) as unknown as typeof fetch;
    mockAnalyzeData = {
      ...baseAnalysis,
      sample_run: true,
      packet_unlocked: false,
    };
    window.history.replaceState(null, "", "/dashboard");

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      });
      fireEvent.click(screen.getByRole("button", { name: /Pay \$49/i }));
      await waitFor(() => {
        expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
      });
      expect(store.get("optionstaxhub-packet-resume-checkout")).toBe("1");
      expect(store.get("optionstaxhub-packet-resume-sample")).toBe("1");
      expect(window.location.search).toContain("resume_checkout=1");
      expect(
        screen.queryByText(/guest snapshot must be restored/i),
      ).not.toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  function installSessionStore(): Map<string, string> {
    const store = new Map<string, string>();
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      value: {
        getItem: jest.fn((key: string) => store.get(key) ?? null),
        setItem: jest.fn((key: string, value: string) => {
          store.set(key, value);
        }),
        removeItem: jest.fn((key: string) => {
          store.delete(key);
        }),
        clear: jest.fn(() => {
          store.clear();
        }),
      },
    });
    return store;
  }

  function handoffFetch(checkoutCalls: Array<{ url: string; authorization: string; body: string }>) {
    return jest.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/year-close-packet/checkout")) {
        const headers = (init?.headers ?? {}) as Record<string, string>;
        checkoutCalls.push({
          url,
          authorization: headers.Authorization || "",
          body: typeof init?.body === "string" ? init.body : "",
        });
        return {
          ok: true,
          status: 200,
          json: async () => ({
            checkout_url: "https://checkout.stripe.com/c/pay/cs_test_resume",
            session_id: "cs_test_resume",
          }),
        };
      }
      if (url.includes("sample-robinhood")) {
        return {
          ok: true,
          blob: async () => new Blob(["sample"], { type: "text/csv" }),
        };
      }
      return { ok: true, json: async () => ({}) };
    }) as unknown as typeof fetch;
  }

  it("resumes a sample checkout once from localStorage when sessionStorage is empty", async () => {
    resetAutomaticSampleRerunForTests();
    resetPacketResumeCheckoutLock();
    installSessionStore();
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: true,
        exp: Date.now() + 60_000,
        analysisId: "original-sample",
        taxYear: 2025,
      }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    mockAnalyzeData = null;
    mockAnalyzeMutate.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        ...baseAnalysis,
        analysis_id: "analysis-resume-1",
        sample_run: true,
        packet_unlocked: false,
      });
    });
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(checkoutCalls).toHaveLength(1);
      });
      expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
      expect(checkoutCalls[0].url).toContain("/api/year-close-packet/checkout");
      expect(checkoutCalls[0].authorization).toBe("Bearer mock-jwt-token");
      expect(JSON.parse(checkoutCalls[0].body).analysis_id).toBe("analysis-resume-1");
      expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 30);
        });
      });
      expect(checkoutCalls).toHaveLength(1);
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("ignores an expired local resume key without a sample re-run or checkout", async () => {
    resetAutomaticSampleRerunForTests();
    resetPacketResumeCheckoutLock();
    const store = installSessionStore();
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({ v: 1, sample: true, exp: Date.now() - 1 }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
      });
      expect(checkoutCalls).toHaveLength(0);
      expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
      expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBeUndefined();
      expect(store.get(PACKET_RESUME_SAMPLE_KEY)).toBeUndefined();
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("deletes invalid resume handoffs without starting checkout", async () => {
    resetAutomaticSampleRerunForTests();
    resetPacketResumeCheckoutLock();
    const store = installSessionStore();
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);
    const invalid = [
      "{",
      JSON.stringify({ v: 3, sample: true, exp: Date.now() + 60_000 }),
      JSON.stringify({ v: 1, sample: "yes", exp: Date.now() + 60_000 }),
    ];

    try {
      for (const raw of invalid) {
        localStorage.setItem(PACKET_RESUME_LOCAL_KEY, raw);
        store.clear();
        mockAnalyzeMutate.mockClear();
        const view = render(<DashboardPage />, { wrapper: createWrapper() });
        await waitFor(() => {
          expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
        });
        expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBeUndefined();
        expect(store.get(PACKET_RESUME_SAMPLE_KEY)).toBeUndefined();
        await waitFor(() => {
          expect(mockAnalyzeMutate).toHaveBeenCalled();
        });
        view.unmount();
      }
      expect(checkoutCalls).toHaveLength(0);
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("asks for the CSV again when an uploaded resume has no stored analysis", async () => {
    resetAutomaticSampleRerunForTests();
    resetPacketResumeCheckoutLock();
    const store = installSessionStore();
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({ v: 1, sample: false, exp: Date.now() + 60_000 }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      const alert = await screen.findByTestId("packet-resume-reupload");
      expect(alert).toHaveTextContent("Re-upload your CSV to continue to checkout.");
      expect(alert.className).toEqual(expect.stringMatching(/MuiAlert-(standard|outlined|filled)Info/));
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(checkoutCalls).toHaveLength(0);
      expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
      expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBeUndefined();
      expect(store.get(PACKET_RESUME_SAMPLE_KEY)).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("resumes checkout once when an uploaded handoff still has a stored analysis", async () => {
    resetAutomaticSampleRerunForTests();
    resetPacketResumeCheckoutLock();
    const store = installSessionStore();
    store.set(
      "optionstaxhub-analysis",
      JSON.stringify({
        ...baseAnalysis,
        sample_run: false,
        packet_unlocked: false,
      }),
    );
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "analysis-test-1",
        taxYear: 2025,
      }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(checkoutCalls).toHaveLength(1);
      });
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(checkoutCalls[0].authorization).toBe("Bearer mock-jwt-token");
      expect(checkoutCalls[0].url).toContain("/api/year-close-packet/checkout");
      expect(JSON.parse(checkoutCalls[0].body).analysis_id).toBe("analysis-test-1");
      expect(localStorage.getItem(PACKET_RESUME_LOCAL_KEY)).toBeNull();
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 30);
        });
      });
      expect(checkoutCalls).toHaveLength(1);
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  function historyRow(
    id: string,
    filename: string,
    uploadedAt: string,
  ) {
    return {
      id,
      filename,
      uploaded_at: uploadedAt,
      positions_count: 2,
      total_market_value: 1000,
    };
  }

  it("loads the newest saved run on resume_checkout when markers and session analysis are gone", async () => {
    installSessionStore();
    mockHistoryData = [
      historyRow("older-run", "older.csv", "2025-05-10T14:30:00Z"),
      historyRow("newer-run", "newer.csv", "2025-06-15T09:00:00Z"),
    ];
    mockFetchAnalysisById.mockResolvedValue({
      id: "newer-run",
      filename: "newer.csv",
      result: {
        ...baseAnalysis,
        analysis_id: "newer-run",
        sample_run: false,
        packet_unlocked: false,
      },
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(screen.getByTestId("year-close-packet-panel")).toBeInTheDocument();
      });
      expect(mockFetchAnalysisById).toHaveBeenCalledTimes(1);
      expect(mockFetchAnalysisById).toHaveBeenCalledWith("newer-run");
      expect(screen.getByText("Saved analysis")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(checkoutCalls).toHaveLength(0);
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 30);
        });
      });
      expect(checkoutCalls).toHaveLength(0);
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("loads the sample when resume_checkout has no markers and history is empty", async () => {
    const store = installSessionStore();
    mockHistoryData = [];
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(mockAnalyzeMutate).toHaveBeenCalledTimes(1);
      });
      expect(checkoutCalls).toHaveLength(0);
      expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBeUndefined();
      expect(store.get(PACKET_RESUME_SAMPLE_KEY)).toBeUndefined();
      expect(store.get(PACKET_RESUME_ANALYSIS_KEY)).toBeUndefined();
      expect(mockFetchAnalysisById).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("shows the other-tab note and does not checkout after the saved run loads when checkout started recently", async () => {
    installSessionStore();
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now()));
    mockHistoryData = [
      historyRow("older-run", "older.csv", "2025-01-01T00:00:00Z"),
      historyRow("newer-run", "newer.csv", "2026-01-02T00:00:00Z"),
    ];
    mockFetchAnalysisById.mockResolvedValue({
      id: "newer-run",
      filename: "newer.csv",
      result: {
        ...baseAnalysis,
        analysis_id: "newer-run",
        sample_run: false,
        packet_unlocked: false,
      },
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      expect(await screen.findByTestId("packet-checkout-other-tab")).toHaveTextContent(
        "Checkout opened in your other tab",
      );
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      expect(mockFetchAnalysisById).toHaveBeenCalledWith("newer-run");
      expect(checkoutCalls).toHaveLength(0);
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 30);
        });
      });
      expect(checkoutCalls).toHaveLength(0);
      expect(localStorage.getItem(PACKET_CHECKOUT_STARTED_KEY)).toMatch(/^\d+$/);
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("does not load history or the sample on a normal dashboard", async () => {
    installSessionStore();
    mockHistoryData = [
      historyRow("newer-run", "newer.csv", "2026-01-02T00:00:00Z"),
    ];
    window.history.replaceState(null, "", "/dashboard");
    render(<DashboardPage />, { wrapper: createWrapper() });
    await screen.findByText(/Get started with your first analysis/i);
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 30);
      });
    });
    expect(mockFetchAnalysisById).not.toHaveBeenCalled();
    expect(mockAnalyzeMutate).not.toHaveBeenCalled();
  });

  it("keeps the re-upload alert instead of history when an uploaded handoff has no stored analysis", async () => {
    const store = installSessionStore();
    mockHistoryData = [
      historyRow("newer-run", "newer.csv", "2026-04-01T00:00:00Z"),
    ];
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({ v: 1, sample: false, exp: Date.now() + 60_000 }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      const alert = await screen.findByTestId("packet-resume-reupload");
      expect(alert).toHaveTextContent("Re-upload your CSV to continue to checkout.");
      expect(mockFetchAnalysisById).not.toHaveBeenCalled();
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(checkoutCalls).toHaveLength(0);
      expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBeUndefined();
      expect(screen.queryByText("Saved analysis")).not.toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("does not re-run the sample after a non-sample pay cleared the sample flag", async () => {
    const store = installSessionStore();
    store.set(PACKET_RESUME_CHECKOUT_KEY, "1");
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await screen.findByText(/Get started with your first analysis/i);
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 30);
        });
      });
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(mockFetchAnalysisById).not.toHaveBeenCalled();
      expect(checkoutCalls).toHaveLength(0);
      expect(store.get(PACKET_RESUME_SAMPLE_KEY)).toBeUndefined();
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("loads the origin saved run when the resume id does not match the open analysis", async () => {
    const store = installSessionStore();
    store.set(
      "optionstaxhub-analysis",
      JSON.stringify({
        ...baseAnalysis,
        analysis_id: "wrong-on-screen",
        sample_run: false,
        packet_unlocked: false,
        tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      }),
    );
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "origin-run",
        taxYear: 2025,
      }),
    );
    mockHistoryData = [
      historyRow("origin-run", "origin.csv", "2025-03-01T00:00:00Z"),
      historyRow("newer-other", "newer.csv", "2026-08-01T00:00:00Z"),
    ];
    mockFetchAnalysisById.mockResolvedValue({
      id: "origin-run",
      filename: "origin.csv",
      result: {
        ...baseAnalysis,
        analysis_id: "origin-run",
        sample_run: false,
        packet_unlocked: false,
      },
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await waitFor(() => {
        expect(store.get("optionstaxhub-analysis") ?? "").toContain("origin-run");
      });
      expect(mockFetchAnalysisById).toHaveBeenCalledTimes(1);
      expect(mockFetchAnalysisById).toHaveBeenCalledWith("origin-run");
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBeUndefined();
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(checkoutCalls).toHaveLength(0);
      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 40);
        });
      });
      expect(checkoutCalls).toHaveLength(0);
      expect(store.get("optionstaxhub-analysis")).not.toContain("wrong-on-screen");
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("holds Pay on a mismatched run until the expected origin id loads", async () => {
    const store = installSessionStore();
    store.set(
      "optionstaxhub-analysis",
      JSON.stringify({
        ...baseAnalysis,
        analysis_id: "wrong-on-screen",
        sample_run: false,
        packet_unlocked: false,
      }),
    );
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
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now()));
    mockHistoryData = [
      historyRow("origin-id", "origin.csv", "2026-02-01T00:00:00Z"),
    ];
    let releaseOrigin: (value: unknown) => void = () => {};
    mockFetchAnalysisById.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseOrigin = resolve;
        }),
    );
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      const pay = await screen.findByRole("button", { name: /Pay \$49/i });
      expect(pay).toBeDisabled();
      expect(store.get(PACKET_RESUME_ANALYSIS_KEY)).toContain("origin-id");
      expect(store.get(PACKET_RESUME_CHECKOUT_KEY)).toBe("1");
      expect(mockFetchAnalysisById).toHaveBeenCalledWith("origin-id");
      fireEvent.click(pay);
      expect(checkoutCalls).toHaveLength(0);
      expect(JSON.stringify(checkoutCalls)).not.toContain("wrong-on-screen");

      await act(async () => {
        releaseOrigin({
          id: "origin-id",
          filename: "origin.csv",
          result: {
            ...baseAnalysis,
            analysis_id: "origin-id",
            sample_run: false,
            packet_unlocked: false,
          },
        });
      });
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      });
      expect(checkoutCalls).toHaveLength(0);
      expect(JSON.stringify(checkoutCalls)).not.toContain("wrong-on-screen");
    } finally {
      globalThis.fetch = originalFetch;
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("clears a mismatched on-screen run when the origin id never appears so Pay cannot charge it", async () => {
    jest.useFakeTimers();
    const store = installSessionStore();
    store.set(
      "optionstaxhub-analysis",
      JSON.stringify({
        ...baseAnalysis,
        analysis_id: "wrong-on-screen",
        sample_run: false,
        packet_unlocked: false,
        tax_profile: { ...baseAnalysis.tax_profile, tax_year: 2024 },
      }),
    );
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
    mockHistoryData = [historyRow("other-run", "other.csv", "2026-01-01T00:00:00Z")];
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await act(async () => {
        await jest.advanceTimersByTimeAsync(9_000);
      });
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeDisabled();
      expect(store.get("optionstaxhub-analysis")).toContain("wrong-on-screen");

      await act(async () => {
        await jest.advanceTimersByTimeAsync(3_000);
      });

      expect(screen.getByTestId("packet-resume-reupload")).toHaveTextContent(
        "Re-upload your CSV to continue to checkout.",
      );
      expect(screen.getByTestId("csv-dropzone")).toBeInTheDocument();
      const pay = screen.queryByRole("button", { name: /Pay \$49/i });
      if (pay) {
        expect(pay).toBeDisabled();
        fireEvent.click(pay);
      }
      expect(checkoutCalls).toHaveLength(0);
      expect(JSON.stringify(checkoutCalls)).not.toContain("wrong-on-screen");
      expect(JSON.stringify(checkoutCalls)).not.toContain("origin-id");
      expect(store.get("optionstaxhub-analysis") ?? "").not.toContain("wrong-on-screen");
      expect(mockFetchAnalysisById).not.toHaveBeenCalled();
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = originalFetch;
      jest.useRealTimers();
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("asks for a re-upload when the origin run never appears in history", async () => {
    jest.useFakeTimers();
    installSessionStore();
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "gone-run",
        taxYear: 2025,
      }),
    );
    mockHistoryData = [historyRow("other-run", "other.csv", "2026-01-01T00:00:00Z")];
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await act(async () => {
        await jest.advanceTimersByTimeAsync(9_000);
      });
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
      await act(async () => {
        await jest.advanceTimersByTimeAsync(3_000);
      });
      expect(screen.getByTestId("packet-resume-reupload")).toHaveTextContent(
        "Re-upload your CSV to continue to checkout.",
      );
      expect(mockFetchAnalysisById).not.toHaveBeenCalled();
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
      expect(checkoutCalls).toHaveLength(0);
    } finally {
      globalThis.fetch = originalFetch;
      jest.useRealTimers();
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("loads the origin run when a later history refetch includes it", async () => {
    jest.useFakeTimers();
    installSessionStore();
    localStorage.setItem(
      PACKET_RESUME_LOCAL_KEY,
      JSON.stringify({
        v: 2,
        sample: false,
        exp: Date.now() + 60_000,
        analysisId: "origin-run",
        taxYear: 2025,
      }),
    );
    localStorage.setItem(PACKET_CHECKOUT_STARTED_KEY, String(Date.now()));
    mockHistoryData = [historyRow("other-run", "other.csv", "2026-01-01T00:00:00Z")];
    let historyReads = 0;
    mockRefetchHistory.mockImplementation(async () => {
      historyReads += 1;
      if (historyReads >= 2) {
        mockHistoryData = [
          historyRow("origin-run", "origin.csv", "2026-02-01T00:00:00Z"),
          historyRow("other-run", "other.csv", "2026-01-01T00:00:00Z"),
        ];
      }
      return { data: mockHistoryData };
    });
    mockFetchAnalysisById.mockResolvedValue({
      id: "origin-run",
      filename: "origin.csv",
      result: {
        ...baseAnalysis,
        analysis_id: "origin-run",
        sample_run: false,
        packet_unlocked: false,
      },
    });
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = handoffFetch(checkoutCalls);

    try {
      render(<DashboardPage />, { wrapper: createWrapper() });
      await act(async () => {
        await jest.advanceTimersByTimeAsync(0);
      });
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
      expect(screen.queryByText("Saved analysis")).not.toBeInTheDocument();
      await act(async () => {
        await jest.advanceTimersByTimeAsync(3_000);
      });
      expect(screen.queryByTestId("packet-resume-reupload")).not.toBeInTheDocument();
      expect(screen.getByText("Saved analysis")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Pay \$49/i })).toBeEnabled();
      expect(screen.getByTestId("packet-checkout-other-tab")).toHaveTextContent(
        "Checkout opened in your other tab",
      );
      expect(mockFetchAnalysisById).toHaveBeenCalledWith("origin-run");
      expect(checkoutCalls).toHaveLength(0);
      expect(mockAnalyzeMutate).not.toHaveBeenCalled();
    } finally {
      globalThis.fetch = originalFetch;
      jest.useRealTimers();
      window.history.replaceState(null, "", "/dashboard");
    }
  });

  it("does not load the sample when resume history fails", async () => {
    installSessionStore();
    mockHistoryError = new Error("history down");
    mockHistoryFetched = true;
    mockHistoryData = [];
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<DashboardPage />, { wrapper: createWrapper() });
    await screen.findByText(/Get started with your first analysis/i);
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 30);
      });
    });
    expect(mockAnalyzeMutate).not.toHaveBeenCalled();
    expect(mockFetchAnalysisById).not.toHaveBeenCalled();
  });

  it("does not treat an unfetched history as empty on resume", async () => {
    installSessionStore();
    mockHistoryFetched = false;
    mockHistoryData = [];
    window.history.replaceState(null, "", "/dashboard?resume_checkout=1");
    render(<DashboardPage />, { wrapper: createWrapper() });
    await screen.findByText(/Get started with your first analysis/i);
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 30);
      });
    });
    expect(mockAnalyzeMutate).not.toHaveBeenCalled();
    expect(mockFetchAnalysisById).not.toHaveBeenCalled();
  });

});
