import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PortfolioAnalysis } from "../../lib/types";

// Mock next/navigation
jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: jest.fn(),
  }),
}));

// Mock auth context
const mockSignOut = jest.fn();
jest.mock("../../app/context/auth", () => ({
  useAuth: () => ({
    user: { id: "test-user", email: "test@example.com" },
    loading: false,
    signOut: mockSignOut,
  }),
}));

// Mock API hooks
let mockAnalyzeData: PortfolioAnalysis | null = null;
let mockHistoryData: Array<{
  id: string;
  filename: string;
  uploaded_at: string;
  positions_count?: number;
  total_market_value?: number;
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
        getItem: jest.fn(() => mockSessionValue),
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
        screen.getByText(/Previous year's Robinhood 1099 PDF/i),
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
        source_filename: "2024-robinhood-1099.pdf",
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
        screen.getByText(/Upload your previous year’s Robinhood 1099 PDF/i),
      ).toBeInTheDocument();
    });
  });

  it("shows supplemental 1099 insights when the analysis includes them", async () => {
    mockAnalyzeData = {
      ...baseAnalysis,
      supplemental_1099: {
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
      },
    };

    render(<DashboardPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("Previous-year 1099 supplement applied"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("Included in current analysis"),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Using Robinhood 1099 PDF for tax year 2024/i),
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          /Matched prior-year 1099 activity to 1 current symbol/i,
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
      supplemental_1099: {
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
        insights: ["Matched prior-year 1099 activity to 1 current symbol(s): CLSK."],
      },
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

    fireEvent.click(screen.getAllByRole("button", { name: /History/i })[0]);
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
});
