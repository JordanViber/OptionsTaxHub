import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Home, { SAMPLE_FETCH_TIMEOUT_MS } from "../../app/dashboard/page";
import { persistGuestAnalysis } from "../../lib/api";
import { resetGuestPersistInFlight } from "../../lib/guest-persist-lock";

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

jest.mock("next/link", () => {
  return ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>;
});

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
jest.mock("../../app/components/YearClosePacketPanel", () => {
  const Mock = () => (
    <div data-testid="year-close-packet-panel">Year-close packet — $49</div>
  );
  return {
    __esModule: true,
    default: Mock,
    isYearClosePacketPaid: jest.fn(() => false),
    rememberYearClosePacketPaid: jest.fn(),
  };
});

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

jest.mock("../../lib/api", () => ({
  useAnalyzePortfolio: () => mockUseAnalyzePortfolio(),
  useTaxProfile: () => mockUseTaxProfile(),
  usePortfolioHistory: () => mockUsePortfolioHistory(),
  useBackendHealth: () => ({ isError: false, isFetched: true }),
  fetchAnalysisById: jest.fn().mockResolvedValue(null),
  cleanupOrphanHistory: jest.fn().mockResolvedValue(0),
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

// Helper to create default auth mock
const createAuthMock = (
  user: any = null,
  loading: boolean = false,
  signOut?: any,
) => ({
  user: user
    ? { email_confirmed_at: "2025-01-01T00:00:00Z", ...user }
    : null,
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

const sampleAnalysis = {
  analysis_id: "guest-sample-1",
  positions: [{ symbol: "AAPL" }],
  tax_lots: [],
  suggestions: [],
  wash_sale_flags: [],
  summary: { total_market_value: 1000, positions_count: 1 },
  tax_profile: {
    filing_status: "single",
    estimated_annual_income: 75000,
    tax_year: 2026,
  },
  disclaimer: "",
  warnings: [],
  errors: [],
  sample_run: true,
};

function mockSampleCsvFetch() {
  globalThis.fetch = jest.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("sample-robinhood-1099-2026.pdf")) {
      return {
        ok: true,
        blob: async () =>
          new Blob(["%PDF-1.4 sample"], { type: "application/pdf" }),
      };
    }
    return {
      ok: true,
      blob: async () => new Blob(["symbol,qty\nAAPL,1"], { type: "text/csv" }),
    };
  }) as typeof fetch;
}

describe("Home page", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    mockPush.mockClear();
    mockUseAuth.mockReset();
    mockUseAnalyzePortfolio.mockReset();
    mockUseTaxProfile.mockReset();
    mockUsePortfolioHistory.mockReset();
    (persistGuestAnalysis as jest.Mock).mockClear();
    (persistGuestAnalysis as jest.Mock).mockResolvedValue(true);
    resetGuestPersistInFlight();
    sessionStorage.clear();
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    jest.useRealTimers();
    globalThis.fetch = originalFetch;
  });

  it("renders loading state when auth is loading", () => {
    setupMocks(createAuthMock(null, true));

    renderWithClient(<Home />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("renders the desk for guests instead of bouncing to sign-in", () => {
    setupMocks(createAuthMock(null, false));

    renderWithClient(<Home />);

    expect(mockPush).not.toHaveBeenCalledWith("/auth/signin");
    expect(screen.getByText("Portfolio Analysis")).toBeInTheDocument();
    expect(screen.getByTestId("entry-analysis-panel")).toBeInTheDocument();
    expect(screen.getByTestId("entry-rank-leaps")).toBeInTheDocument();
    expect(screen.queryByTestId("entry-rank-find")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Analyze a new option/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign In" })).toHaveAttribute(
      "href",
      "/auth/signin",
    );
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

  it("associates the upload label with the hidden CSV input", () => {
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

    expect(fileInput).toHaveAttribute("id", "desk-csv-input");
    expect(screen.getByText("Click to upload CSV").closest("label")).toHaveAttribute(
      "for",
      "desk-csv-input",
    );
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
      summary: { total_market_value: 1000, positions_count: 1 },
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
    // Suggestions tab is now default (tab 0), so HarvestingSuggestions is rendered with 1 item
    expect(screen.getByText("1 suggestions")).toBeInTheDocument();
    expect(screen.getByText("Suggestions (1)")).toBeInTheDocument();
  });

  it("renders wash-sale warnings when present", async () => {
    const mockAnalysis = {
      positions: [{ symbol: "TSLA" }],
      tax_lots: [],
      suggestions: [],
      wash_sale_flags: [{ symbol: "TSLA" }],
      summary: { total_market_value: 0 },
      tax_profile: null,
      disclaimer: "",
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

    await waitFor(() => {
      expect(screen.getByTestId("wash-sale-warning")).toBeInTheDocument();
    });
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

  it("does not start the landing sample while auth is still loading", async () => {
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, true), createAnalyzeMock({ mutate }));
    sessionStorage.setItem("oth-load-sample", "1");
    mockSampleCsvFetch();

    renderWithClient(<Home />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 30);
      });
    });
    expect(mutate).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("oth-load-sample")).toBe("1");
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
  });

  it("loads the landing sample through analyze under Strict Mode", async () => {
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    sessionStorage.setItem("oth-load-sample", "1");
    mockSampleCsvFetch();

    render(
      <StrictMode>
        <Home />
      </StrictMode>,
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(mutate).toHaveBeenCalled();
    });
    expect(mutate.mock.calls[0][0]).toMatchObject({
      file: expect.objectContaining({
        name: "sample-robinhood-transactions.csv",
      }),
      supplemental1099File: expect.objectContaining({
        name: "sample-robinhood-1099-2026.pdf",
      }),
    });
    expect(sessionStorage.getItem("oth-load-sample")).toBe("1");
  });

  it("clears Analyzing when the sample CSV fetch never returns", async () => {
    jest.useFakeTimers();
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    sessionStorage.setItem("oth-load-sample", "1");
    globalThis.fetch = jest.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(
              new DOMException("The operation was aborted.", "AbortError"),
            );
          });
        }),
    ) as typeof fetch;

    renderWithClient(<Home />);

    expect(screen.getByText("Analyzing portfolio...")).toBeInTheDocument();

    await act(async () => {
      jest.advanceTimersByTime(SAMPLE_FETCH_TIMEOUT_MS);
    });

    expect(screen.getByText("Analysis Failed")).toBeInTheDocument();
    expect(
      screen.getByText("Could not load the 2026 sample."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Open the 2026 sample" }),
    ).not.toBeInTheDocument();
    expect(SAMPLE_FETCH_TIMEOUT_MS).toBe(8000);
  });

  it("empty-desk Open sample aborts a hung fetch and does not stack the empty CTA", async () => {
    jest.useFakeTimers();
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    globalThis.fetch = jest.fn(
      (_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(
              new DOMException("The operation was aborted.", "AbortError"),
            );
          });
        }),
    ) as typeof fetch;

    renderWithClient(<Home />);
    fireEvent.click(
      screen.getByRole("button", { name: "Open the 2026 sample" }),
    );
    expect(screen.getByText("Analyzing portfolio...")).toBeInTheDocument();
    expect(screen.queryByText("Analysis Failed")).not.toBeInTheDocument();

    await act(async () => {
      jest.advanceTimersByTime(SAMPLE_FETCH_TIMEOUT_MS);
    });

    expect(screen.getByText("Analysis Failed")).toBeInTheDocument();
    expect(
      screen.getByText("Could not load the 2026 sample."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open the 2026 sample" }),
    ).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("restores the sample under Strict Mode remount without leave/return", async () => {
    const mutate = jest.fn(
      (
        _params: unknown,
        options?: { onSuccess?: (data: typeof sampleAnalysis) => void },
      ) => {
        options?.onSuccess?.(sampleAnalysis);
      },
    );
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    sessionStorage.setItem("oth-load-sample", "1");
    mockSampleCsvFetch();

    render(
      <StrictMode>
        <Home />
      </StrictMode>,
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(screen.getByText("Tax Year: 2026")).toBeInTheDocument();
    });
    expect(screen.getByText(/Positions \(1\)/)).toBeInTheDocument();
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("oth-load-sample")).toBeNull();
  });

  it("finishes the landing sample on the first visit once auth is ready", async () => {
    const mutate = jest.fn((_params: unknown, options?: { onSuccess?: (data: typeof sampleAnalysis) => void }) => {
      options?.onSuccess?.(sampleAnalysis);
    });
    let loading = true;
    mockUseAuth.mockImplementation(() => createAuthMock(null, loading));
    mockUseAnalyzePortfolio.mockReturnValue(createAnalyzeMock({ mutate }));
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUsePortfolioHistory.mockReturnValue({ data: [], isLoading: false });
    sessionStorage.setItem("oth-load-sample", "1");
    mockSampleCsvFetch();

    const wrapper = createWrapper();
    const { rerender } = render(<Home />, { wrapper });

    expect(mutate).not.toHaveBeenCalled();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();

    loading = false;
    rerender(<Home />);

    await waitFor(() => {
      expect(mutate).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText("Tax Year: 2026")).toBeInTheDocument();
    expect(screen.getByText(/Positions \(1\)/)).toBeInTheDocument();
    expect(screen.getByTestId("summary-cards")).toBeInTheDocument();
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("oth-load-sample")).toBeNull();

    const taxHeading = screen.getByText("Portfolio Analysis");
    const optionsHeading = screen.getByRole("heading", {
      name: /Analyze a new option/i,
    });
    expect(
      taxHeading.compareDocumentPosition(optionsHeading) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getByTestId("entry-rank-leaps")).toBeInTheDocument();
    expect(screen.queryByTestId("entry-rank-find")).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.change(screen.getByTestId("entry-strike"), {
      target: { value: "250" },
    });
    fireEvent.change(screen.getByTestId("entry-expiration"), {
      target: { value: "2027-12-17" },
    });
    fireEvent.change(screen.getByTestId("entry-premium"), {
      target: { value: "4.20" },
    });
    expect(screen.getByTestId("entry-results")).toBeInTheDocument();
    expect(screen.getByTestId("entry-max-loss")).toHaveTextContent("$420.00");
    expect(
      screen.queryByRole("button", { name: /connect robinhood/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reconnect/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^tax desk$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /options desk/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("year-close-packet-panel")).toHaveTextContent(
      "$49",
    );
  });

  it("shows the analyze error string and clears Analyzing after a failed sample", async () => {
    const mutate = jest.fn(
      (
        _params: unknown,
        options?: { onError?: (error: Error) => void },
      ) => {
        options?.onError?.(new Error("Could not parse any positions from the CSV file."));
      },
    );
    setupMocks(
      createAuthMock(null, false),
      createAnalyzeMock({
        mutate,
        error: new Error(
          "Could not parse any positions from the CSV file.",
        ),
        isPending: false,
      }),
    );
    sessionStorage.setItem("oth-load-sample", "1");
    mockSampleCsvFetch();

    renderWithClient(<Home />);

    await waitFor(() => {
      expect(mutate).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByText("Analysis Failed")).toBeInTheDocument();
    expect(
      screen.getByText("Could not parse any positions from the CSV file."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
  });

  it("shows a real sample-fetch error and never leaves Analyzing up", async () => {
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    sessionStorage.setItem("oth-load-sample", "1");
    globalThis.fetch = jest.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("sample-robinhood-transactions.csv")) {
        return { ok: false, blob: async () => new Blob([]) };
      }
      return {
        ok: true,
        blob: async () =>
          new Blob(["%PDF-1.4 sample"], { type: "application/pdf" }),
      };
    }) as typeof fetch;

    renderWithClient(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Analysis Failed")).toBeInTheDocument();
    });
    expect(screen.getByText("Could not load the sample CSV.")).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
    expect(screen.queryByText("Analyzing portfolio...")).not.toBeInTheDocument();
  });

  it("restores a finished sample after remount without a second analyze", async () => {
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify({
        ...sampleAnalysis,
        tax_profile: {
          filing_status: "single",
          estimated_annual_income: 75000,
          tax_year: 2026,
        },
      }),
    );

    renderWithClient(<Home />);

    await waitFor(() => {
      expect(screen.getByText("Tax Year: 2026")).toBeInTheDocument();
    });
    expect(screen.getByText(/Positions \(1\)/)).toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("sets lastUploadedCsv after the landing sample so a 1099 rerun is not null", async () => {
    const mutate = jest.fn();
    setupMocks(createAuthMock(null, false), createAnalyzeMock({ mutate }));
    sessionStorage.setItem("oth-load-sample", "1");
    mockSampleCsvFetch();

    const { container } = renderWithClient(<Home />);

    await waitFor(() => {
      expect(mutate).toHaveBeenCalledTimes(1);
    });
    expect(mutate.mock.calls[0][0]).toMatchObject({
      file: expect.objectContaining({
        name: "sample-robinhood-transactions.csv",
      }),
      supplemental1099File: expect.objectContaining({
        name: "sample-robinhood-1099-2026.pdf",
      }),
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
          new File(["pdf"], "supplement.pdf", { type: "application/pdf" }),
        ],
      },
    });

    await waitFor(() => {
      expect(mutate).toHaveBeenCalledTimes(2);
    });
    expect(mutate.mock.calls[1][0]).toMatchObject({
      file: expect.objectContaining({
        name: "sample-robinhood-transactions.csv",
      }),
      supplemental1099File: expect.objectContaining({
        name: "supplement.pdf",
      }),
    });
  });

  it("lets keyboard users Tab/Enter/Space to upload CSV", () => {
    setupMocks(createAuthMock(null, false));
    const clickSpy = jest
      .spyOn(HTMLInputElement.prototype, "click")
      .mockImplementation(() => {});

    const { container } = renderWithClient(<Home />);
    const dropzone = screen.getByTestId("csv-dropzone");

    expect(dropzone).toHaveAttribute("tabindex", "0");
    expect(dropzone).toHaveAttribute("role", "button");
    expect(container.querySelector("#desk-csv-input")).toHaveAttribute(
      "tabindex",
      "-1",
    );

    dropzone.focus();
    fireEvent.keyDown(dropzone, { key: "Enter" });
    fireEvent.keyDown(dropzone, { key: " " });

    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it("persists a guest run after sign-in", async () => {
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify(sampleAnalysis),
    );
    sessionStorage.setItem("oth-guest-unsaved", "1");
    sessionStorage.setItem(
      "oth-guest-unsaved-filename",
      "sample-robinhood-transactions.csv",
    );
    setupMocks(
      createAuthMock(
        {
          id: "user-1",
          email: "signed-in@example.com",
          email_confirmed_at: "2025-01-01T00:00:00Z",
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    await waitFor(() => {
      expect(persistGuestAnalysis).toHaveBeenCalledWith(
        expect.objectContaining({ analysis_id: "guest-sample-1" }),
        "sample-robinhood-transactions.csv",
      );
    });
  });

  it("retries guest persist even if a leftover sessionStorage lock is present", async () => {
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify(sampleAnalysis),
    );
    sessionStorage.setItem("oth-guest-unsaved", "1");
    sessionStorage.setItem(
      "oth-guest-unsaved-filename",
      "sample-robinhood-transactions.csv",
    );
    sessionStorage.setItem("oth-guest-persist-lock", "guest-sample-1");
    setupMocks(
      createAuthMock(
        {
          id: "user-1",
          email: "signed-in@example.com",
          email_confirmed_at: "2025-01-01T00:00:00Z",
        },
        false,
      ),
    );

    renderWithClient(<Home />);

    await waitFor(() => {
      expect(persistGuestAnalysis).toHaveBeenCalledWith(
        expect.objectContaining({ analysis_id: "guest-sample-1" }),
        "sample-robinhood-transactions.csv",
      );
    });
  });
});
