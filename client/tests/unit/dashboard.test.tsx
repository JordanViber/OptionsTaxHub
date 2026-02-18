import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
jest.mock("../../lib/api", () => ({
  useAnalyzePortfolio: () => ({
    mutate: jest.fn(),
    isPending: false,
    error: null,
    data: null,
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
  beforeEach(() => {
    jest.clearAllMocks();
    // Mock sessionStorage
    Object.defineProperty(globalThis, "sessionStorage", {
      value: {
        getItem: jest.fn(() => null),
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
});
