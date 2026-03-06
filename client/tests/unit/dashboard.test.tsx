import { render, screen, waitFor } from "@testing-library/react";
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

jest.mock("../../lib/api", () => ({
  useAnalyzePortfolio: () => ({
    mutate: jest.fn(),
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
    data: [],
    error: null,
    isPending: false,
  }),
  useBackendHealth: () => ({
    isError: false,
    isFetched: true,
  }),
  fetchAnalysisById: jest.fn(),
  cleanupOrphanHistory: jest.fn(() => Promise.resolve()),
  deleteAnalysis: jest.fn(() => Promise.resolve(true)),
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
});
