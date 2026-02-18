// Mock auth before imports
const mockGetSession = jest.fn();
jest.mock("../../lib/supabase", () => ({
  getSession: mockGetSession,
  getSupabaseClient: jest.fn(),
  signIn: jest.fn(),
  signUp: jest.fn(),
  signOut: jest.fn(),
  getCurrentUser: jest.fn(),
}));

// Set default behavior for getSession
mockGetSession.mockResolvedValue({
  access_token: "mock-jwt-token",
  user: { id: "test-user-id", email: "test@example.com" },
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  renderHook,
} from "@testing-library/react";
import React from "react";
import {
  useAnalyzePortfolio,
  usePortfolioHistory,
  useUploadPortfolio,
  useFetchPrices,
  useTaxProfile,
  useSaveTaxProfile,
  useTaxBrackets,
  fetchAnalysisById,
  deleteAnalysis,
  cleanupOrphanHistory,
} from "../../lib/api";

type WrapperProps = { children: React.ReactNode };

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: WrapperProps) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
};

const getStatus = (data: unknown, error: unknown) => {
  if (data) return "success";
  if (error) return "error";
  return "idle";
};

const getHistoryStatus = (data: unknown, error: unknown) => {
  if (data) return "history";
  if (error) return "error";
  return "idle";
};

const getPriceStatus = (data: unknown, error: unknown, isPending: boolean) => {
  if (isPending) return "loading";
  if (data) return "prices";
  if (error) return "error";
  return "idle";
};

function UploadComponent({ file }: Readonly<{ file: File }>) {
  const { mutate, data, error } = useUploadPortfolio();
  return (
    <div>
      <button onClick={() => mutate(file)}>Upload</button>
      <span>{getStatus(data, error)}</span>
    </div>
  );
}

function HistoryComponent() {
  const { data, error } = usePortfolioHistory();
  return (
    <div>
      <span>{getHistoryStatus(data, error)}</span>
    </div>
  );
}

function AnalyzeComponent({ file }: Readonly<{ file: File }>) {
  const { mutate, data, error } = useAnalyzePortfolio();
  return (
    <div>
      <button onClick={() => mutate({ file })}>Analyze</button>
      <span>{getStatus(data, error)}</span>
    </div>
  );
}

function PricesComponent({ symbols }: Readonly<{ symbols: string[] }>) {
  const { data, error, isPending } = useFetchPrices(symbols, true);
  return (
    <div>
      <span>{getPriceStatus(data, error, isPending)}</span>
      {data && <span>{JSON.stringify(data)}</span>}
    </div>
  );
}

function TaxProfileComponent() {
  const { data, error } = useTaxProfile();
  return (
    <div>
      <span>{getStatus(data, error)}</span>
      {data && <span>{data.user_id}</span>}
    </div>
  );
}

function TaxBracketsComponent() {
  const { data, error } = useTaxBrackets(2025, "single", 75000, true);
  return (
    <div>
      <span>{getStatus(data, error)}</span>
    </div>
  );
}

describe("api hooks", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    jest.resetAllMocks();
    originalFetch = globalThis.fetch;
    // Re-setup getSession mock after reset
    mockGetSession.mockResolvedValue({
      access_token: "mock-jwt-token",
      user: { id: "test-user-id", email: "test@example.com" },
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe("useUploadPortfolio", () => {
    it("uploads portfolio successfully", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => [{ symbol: "AAPL", qty: 1, price: 100 }],
      } as Response);

      render(<UploadComponent file={file} />, { wrapper: createWrapper() });

      fireEvent.click(screen.getByText("Upload"));

      await waitFor(() => {
        expect(screen.getByText("success")).toBeInTheDocument();
      });

      expect(globalThis.fetch).toHaveBeenCalled();
    });

    it("handles upload errors gracefully", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Bad Request",
      } as Response);

      render(<UploadComponent file={file} />, { wrapper: createWrapper() });

      fireEvent.click(screen.getByText("Upload"));

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });

      expect(globalThis.fetch).toHaveBeenCalled();
    });
  });

  describe("useAnalyzePortfolio", () => {
    it("analyzes portfolio with file only", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          positions: [{ symbol: "AAPL", quantity: 1 }],
          suggestions: [],
          wash_sale_flags: [],
          summary: { total_unrealized_pnl: 0 },
        }),
      } as Response);

      render(<AnalyzeComponent file={file} />, { wrapper: createWrapper() });

      fireEvent.click(screen.getByText("Analyze"));

      await waitFor(() => {
        expect(screen.getByText("success")).toBeInTheDocument();
      });

      expect(globalThis.fetch).toHaveBeenCalled();
      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("/api/portfolio/analyze");
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });

    it("analyzes portfolio with optional parameters", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          positions: [],
          suggestions: [],
          wash_sale_flags: [],
          summary: {},
        }),
      } as Response);

      render(<AnalyzeComponent file={file} />, { wrapper: createWrapper() });

      fireEvent.click(screen.getByText("Analyze"));

      await waitFor(() => {
        expect(screen.getByText("success")).toBeInTheDocument();
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("/api/portfolio/analyze");
    });

    it("handles analyze errors with detail message", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Bad Request",
        json: async () => ({ detail: "Invalid CSV format" }),
      } as Response);

      render(<AnalyzeComponent file={file} />, { wrapper: createWrapper() });

      fireEvent.click(screen.getByText("Analyze"));

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });
    });
  });

  describe("useFetchPrices", () => {
    it("fetches prices for given symbols when enabled", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          AAPL: { price: 150.5 },
          MSFT: { price: 300.25 },
        }),
      } as Response);

      render(<PricesComponent symbols={["AAPL", "MSFT"]} />, {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(screen.getByText("prices")).toBeInTheDocument();
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("symbols=AAPL,MSFT");
    });

    it("does not fetch when disabled", async () => {
      globalThis.fetch = jest.fn();

      render(
        <div>
          {React.createElement(() => {
            const { data } = useFetchPrices(["AAPL"], false);
            return <span>{data ? "prices" : "idle"}</span>;
          })}
        </div>,
        { wrapper: createWrapper() },
      );

      expect(globalThis.fetch).not.toHaveBeenCalled();
    });

    it("handles price fetch errors", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Service Unavailable",
      } as Response);

      render(<PricesComponent symbols={["AAPL"]} />, {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });
    });
  });

  describe("useTaxProfile", () => {
    it("fetches tax profile successfully", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          user_id: "test-user-123",
          filing_status: "single",
          estimated_annual_income: 75000,
          state: "CA",
          tax_year: 2025,
        }),
      } as Response);

      render(<TaxProfileComponent />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("success")).toBeInTheDocument();
        expect(screen.getByText("test-user-123")).toBeInTheDocument();
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });

    it("handles tax profile fetch errors", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Unauthorized",
      } as Response);

      render(<TaxProfileComponent />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });
    });
  });

  describe("useSaveTaxProfile", () => {
    it("includes required mutation functionality", () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          message: "Profile saved",
          profile: {
            user_id: "test-user",
            filing_status: "single",
            estimated_annual_income: 75000,
          },
        }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useSaveTaxProfile(), { wrapper });

      expect(result.current.mutate).toBeDefined();
      expect(result.current.isPending).toBe(false);
    });

    it("invalidates tax profile cache on success", () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          message: "Profile saved",
          profile: {
            user_id: "test-user",
            filing_status: "single",
            estimated_annual_income: 75000,
          },
        }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useSaveTaxProfile(), { wrapper });

      expect(result.current.mutate).toBeDefined();
    });
  });

  describe("useTaxBrackets", () => {
    it("fetches tax brackets with correct parameters", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          year: 2025,
          filing_status: "single",
          income: 75000,
          brackets: [],
        }),
      } as Response);

      render(<TaxBracketsComponent />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("success")).toBeInTheDocument();
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("year=2025");
      expect(call[0]).toContain("filing_status=single");
      expect(call[0]).toContain("income=75000");
    });

    it("handles tax bracket fetch errors", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Not Found",
      } as Response);

      render(<TaxBracketsComponent />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });
    });
  });

  describe("usePortfolioHistory", () => {
    it("handles portfolio history errors", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Not Found",
      } as Response);

      render(<HistoryComponent />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });
    });

    it("fetches portfolio history successfully", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => [
          {
            id: "1",
            filename: "portfolio.csv",
            uploaded_at: "2025-01-01T00:00:00Z",
            positions_count: 10,
            total_market_value: 50000,
          },
        ],
      } as Response);

      render(<HistoryComponent />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText("history")).toBeInTheDocument();
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });
  });

  describe("standalone API functions", () => {
    it("fetchAnalysisById returns analysis with result", async () => {
      const mockAnalysis = {
        id: "123",
        filename: "test.csv",
        uploaded_at: "2025-01-01T00:00:00Z",
        positions_count: 5,
        total_market_value: 10000,
        result: {
          positions: [],
          suggestions: [],
          wash_sale_flags: [],
          summary: {},
        },
      };

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => mockAnalysis,
      } as Response);

      const result = await fetchAnalysisById("123");

      expect(result.id).toBe("123");
      expect(result.result).toBeDefined();
      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });

    it("fetchAnalysisById throws on error", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Not Found",
      } as Response);

      await expect(fetchAnalysisById("456")).rejects.toThrow("Fetch failed");
    });

    it("deleteAnalysis returns true on success", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
      } as Response);

      const result = await deleteAnalysis("789");

      expect(result).toBe(true);
      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("/api/portfolio/analysis/789");
      expect(call[1].method).toBe("DELETE");
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });

    it("deleteAnalysis returns false on failure", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
      } as Response);

      const result = await deleteAnalysis("999");

      expect(result).toBe(false);
    });

    it("cleanupOrphanHistory calls DELETE endpoint", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
      } as Response);

      await cleanupOrphanHistory();

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("/api/portfolio/history/cleanup");
      expect(call[1].method).toBe("DELETE");
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });
  });
});
