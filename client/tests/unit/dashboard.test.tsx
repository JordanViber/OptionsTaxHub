import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PortfolioAnalysis, Supplemental1099Summary } from "../../lib/types";

// Mock next/navigation
jest.mock("next/link", () => {
  return ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>;
});

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
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
    error: null,
    isPending: false,
  }),
  useBackendHealth: () => ({
    isError: false,
    isFetched: true,
  }),
  fetchAnalysisById: mockFetchAnalysisById,
  cleanupOrphanHistory: mockCleanupOrphanHistory,
  deleteAnalysis: mockDeleteAnalysis,
  persistGuestAnalysis: jest.fn(() => Promise.resolve(true)),
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
    mockAnalyzeMutate.mockReset();
    mockFetchAnalysisById.mockReset();
    mockCleanupOrphanHistory.mockClear();
    mockDeleteAnalysis.mockClear();
    mockSessionValue = null;
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
      expect(screen.getByText(/OptionsTaxHub/i)).toBeInTheDocument();
    });
  });

  it("renders portfolio upload section", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/Portfolio Analysis/i)).toBeInTheDocument();
      expect(
        screen.getByText(/Robinhood 1099 for the tax year you are closing/i),
      ).toBeInTheDocument();
    });
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
        screen.getByText(/Start with the 1 harvesting suggestion shown below/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          /Review the wash-sale panel before relying on losses/i,
        ),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/check the data quality notes/i),
      ).toBeInTheDocument();
    });
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
        screen.getByText(
          /Automated harvesting suggestions were skipped for ASST/i,
        ),
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
      expect(screen.getByText(/Positions \(/i)).toBeInTheDocument();
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
      expect(screen.getByText(/SPX 12\/31/i)).toBeInTheDocument();
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
    const pdfInput = container.querySelector(
      'input[type="file"][accept=".pdf,application/pdf"]',
    );

    if (!(csvInput instanceof HTMLInputElement)) {
      throw new TypeError("CSV input not found");
    }

    if (!(pdfInput instanceof HTMLInputElement)) {
      throw new TypeError("PDF input not found");
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

    fireEvent.change(pdfInput, {
      target: {
        files: [
          new File(["pdf"], "supplement.pdf", { type: "application/pdf" }),
        ],
      },
    });

    await waitFor(() => {
      expect(mockAnalyzeMutate).toHaveBeenCalledTimes(2);
      expect(screen.getByText("Auto-applied to latest CSV")).toBeInTheDocument();
    });

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
      expect(screen.getByRole("button", { name: /Remove 1099 PDF/i })).toBeInTheDocument();
    });

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

  it("shows first-run empty state before any analysis exists", async () => {
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText(/Get started with your first analysis/i),
      ).toBeInTheDocument();
    });
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
        await screen.findByRole("button", { name: "Open the 2026 sample" }),
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
      );
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/sample-robinhood-1099-2026.pdf",
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
        screen.getAllByText(/not lot history/i).length,
      ).toBeGreaterThan(0);
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
      expect(
        screen.getByText(/not a software bug/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/r\/options/i)).toBeInTheDocument();
      expect(screen.getByText(/\$2,699/)).toBeInTheDocument();
      expect(
        screen.queryByText("Previous-year 1099 supplement applied"),
      ).not.toBeInTheDocument();
      expect(screen.getByTestId("year-close-packet-panel")).toHaveTextContent(
        "$49",
      );
    });
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
    mockAnalyzeData = {
      ...baseAnalysis,
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
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("1099 vs your export")).toBeInTheDocument();
    });
    expect(screen.getByTestId("1099-broker-column")).toHaveTextContent(
      "$2,699.00",
    );
    expect(screen.getByTestId("1099-export-column")).toHaveTextContent("$0.00");
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

  it("redirects unconfirmed users away from the dashboard", async () => {
    mockAuthState.user.email_confirmed_at = null;
    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/auth/confirm-email");
    });
    expect(screen.queryByText(/Portfolio Analysis/i)).not.toBeInTheDocument();
  });

});
