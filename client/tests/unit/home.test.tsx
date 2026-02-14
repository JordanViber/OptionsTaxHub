import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Home from "../../app/page";

const mockPush = jest.fn();
const mockUseAuth = jest.fn();
const mockUseAnalyzePortfolio = jest.fn();
const mockUseTaxProfile = jest.fn();
const mockUsePortfolioHistory = jest.fn();

const getFileInput = (container: HTMLElement) => {
  const element = container.querySelector('input[type="file"]');
  if (!(element instanceof HTMLInputElement)) {
    throw new TypeError("Expected file input element");
  }
  return element;
};

jest.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}));

jest.mock("../../app/components/ServiceWorkerRegistration", () => () => null);
jest.mock("../../app/components/TaxDisclaimer", () => () => (
  <div data-testid="tax-disclaimer">Disclaimer</div>
));
jest.mock(
  "../../app/components/PortfolioSummaryCards",
  () =>
    ({ summary }: any) => (
      <div data-testid="summary-cards">{JSON.stringify(summary)}</div>
    ),
);
jest.mock("../../app/components/PositionsTable", () => ({ positions }: any) => (
  <div data-testid="positions-table">{positions.length} positions</div>
));
jest.mock(
  "../../app/components/HarvestingSuggestions",
  () =>
    ({ suggestions }: any) => (
      <div data-testid="suggestions">{suggestions.length} suggestions</div>
    ),
);
jest.mock("../../app/components/WashSaleWarning", () => ({ flags }: any) => (
  <div data-testid="wash-sale-warning">{flags.length} flags</div>
));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

jest.mock("../../lib/api", () => ({
  useAnalyzePortfolio: () => mockUseAnalyzePortfolio(),
  useTaxProfile: () => mockUseTaxProfile(),
  usePortfolioHistory: () => mockUsePortfolioHistory(),
}));

// Helper to create default auth mock
const createAuthMock = (
  user: any = null,
  loading: boolean = false,
  signOut?: any,
) => ({
  user,
  loading,
  signOut: signOut || jest.fn(),
});

// Helper to create default analyze portfolio mock
const createAnalyzeMock = (overrides: any = {}) => ({
  mutate: jest.fn(),
  isPending: false,
  error: null,
  data: null,
  ...overrides,
});

// Helper to set up mocks with defaults
const setupMocks = (auth: any = {}, analyze: any = {}) => {
  mockUseAuth.mockReturnValue(
    Object.keys(auth).length ? auth : createAuthMock(),
  );
  mockUseAnalyzePortfolio.mockReturnValue(
    Object.keys(analyze).length ? analyze : createAnalyzeMock(),
  );
  mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
  mockUsePortfolioHistory.mockReturnValue({ data: [], isLoading: false });
};

// Wrapper with QueryClientProvider for rendering
const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

const renderWithClient = (ui: React.ReactElement) =>
  render(ui, { wrapper: createWrapper() });

describe("Home page", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseAuth.mockReset();
    mockUseAnalyzePortfolio.mockReset();
    mockUseTaxProfile.mockReset();
    mockUsePortfolioHistory.mockReset();
  });

  it("renders loading state when auth is loading", () => {
    setupMocks(createAuthMock(null, true));

    renderWithClient(<Home />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("redirects to sign-in when unauthenticated", () => {
    setupMocks(createAuthMock(null, false));

    renderWithClient(<Home />);

    expect(mockPush).toHaveBeenCalledWith("/auth/signin");
  });

  it("uses display_name when available", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: {
            display_name: "Display Name",
            first_name: "First",
            last_name: "Last",
          },
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("Display Name")).toBeInTheDocument();
  });

  it("falls back to full name when display_name is missing", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { first_name: "First", last_name: "Last" },
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("First Last")).toBeInTheDocument();
  });

  it("uses full_name when available in metadata", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { full_name: "Full Name" },
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("Full Name")).toBeInTheDocument();
  });

  it("falls back to email when profile names are missing", () => {
    setupMocks(
      createAuthMock(
        { email: "email-only@example.com", user_metadata: {} },
        false,
      ),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("email-only@example.com")).toBeInTheDocument();
  });

  it("handles missing user metadata gracefully", () => {
    setupMocks(
      createAuthMock({ email: "metadata-missing@example.com" }, false),
    );

    renderWithClient(<Home />);

    expect(
      screen.getByText("metadata-missing@example.com"),
    ).toBeInTheDocument();
  });

  it("falls back to Account when email is missing", () => {
    setupMocks(createAuthMock({ user_metadata: {} }, false));

    renderWithClient(<Home />);

    expect(screen.getByText("Account")).toBeInTheDocument();
  });

  it("triggers file input click when upload area is clicked", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
    );

    const { container } = renderWithClient(<Home />);
    const fileInput = getFileInput(container);
    const clickSpy = jest.spyOn(fileInput, "click");

    // Click on the text in the upload area
    fireEvent.click(screen.getByText("Click to upload CSV"));

    expect(clickSpy).toHaveBeenCalled();
  });

  it("calls analyzePortfolio when file input changes", () => {
    const mutate = jest.fn();
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ mutate }),
    );

    const { container } = renderWithClient(<Home />);
    const fileInput = getFileInput(container);

    const file = new File(["content"], "test.csv", { type: "text/csv" });
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ file }),
      expect.anything(),
    );
  });

  it("does not upload when no file is selected", () => {
    const mutate = jest.fn();
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ mutate }),
    );

    const { container } = renderWithClient(<Home />);
    const fileInput = getFileInput(container);

    fireEvent.change(fileInput, { target: { files: [] } });

    expect(mutate).not.toHaveBeenCalled();
  });

  it("shows analyzing state when mutation is pending", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ isPending: true }),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("Analyzing portfolio...")).toBeInTheDocument();
  });

  it("renders error state when analysis fails", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ error: new Error("Analysis failed") }),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("Analysis Failed")).toBeInTheDocument();
    expect(screen.getByText("Analysis failed")).toBeInTheDocument();
  });

  it("renders generic error message when error is not an Error", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ error: "Something broke" }),
    );

    renderWithClient(<Home />);

    expect(screen.getByText("An error occurred")).toBeInTheDocument();
  });

  it("renders analysis results when data is available", () => {
    const mockAnalysis = {
      positions: [{ symbol: "AAPL" }],
      suggestions: [{ symbol: "TSLA" }],
      wash_sale_flags: [],
      summary: { total_market_value: 1000 },
      warnings: [],
      errors: [],
    };

    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ data: mockAnalysis }),
    );

    renderWithClient(<Home />);

    expect(screen.getByTestId("summary-cards")).toBeInTheDocument();
    expect(screen.getByText("1 positions")).toBeInTheDocument();
    expect(screen.getByText("Suggestions (1)")).toBeInTheDocument();
  });

  it("renders wash-sale warnings when present", () => {
    const mockAnalysis = {
      positions: [],
      suggestions: [],
      wash_sale_flags: [{ symbol: "TSLA" }],
      summary: { total_market_value: 0 },
      warnings: [],
      errors: [],
    };

    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
      createAnalyzeMock({ data: mockAnalysis }),
    );

    renderWithClient(<Home />);

    expect(screen.getByTestId("wash-sale-warning")).toBeInTheDocument();
  });

  it("signs out and redirects when menu action is clicked", async () => {
    const signOut = jest.fn(() => Promise.resolve());
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
        signOut,
      ),
    );

    renderWithClient(<Home />);

    fireEvent.click(screen.getByText("Test User"));

    await waitFor(() => {
      expect(screen.getByText("Sign Out")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Sign Out"));

    await waitFor(() => {
      expect(signOut).toHaveBeenCalled();
      expect(mockPush).toHaveBeenCalledWith("/auth/signin");
    });
  });

  it("navigates to settings when Settings button is clicked", () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    fireEvent.click(screen.getByText("Settings"));

    expect(mockPush).toHaveBeenCalledWith("/settings");
  });

  it("closes the menu on backdrop click", async () => {
    setupMocks(
      createAuthMock(
        {
          email: "test@example.com",
          user_metadata: { display_name: "Test User" },
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    fireEvent.click(screen.getByText("Test User"));

    await waitFor(() => {
      expect(screen.getByText("Sign Out")).toBeInTheDocument();
    });

    const backdrop = document.querySelector(".MuiBackdrop-root");
    if (backdrop) {
      fireEvent.click(backdrop);
    }

    await waitFor(() => {
      expect(screen.queryByText("Sign Out")).not.toBeInTheDocument();
    });
  });
});
