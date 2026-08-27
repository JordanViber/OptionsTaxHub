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
  act,
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
  getAnalysisErrorMessage,
  getBackendUnreachableMessage,
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
  const { data, error } = usePortfolioHistory("test-user-id");
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
  const { data, error } = useTaxProfile({ userId: "test-user-id" });
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

    it("passes filingStatus and estimatedIncome as query params", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          positions: [],
          suggestions: [],
          wash_sale_flags: [],
          summary: {},
        }),
      } as Response);

      const file = new File(["content"], "test.csv", { type: "text/csv" });
      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({
          file,
          filingStatus: "single",
          estimatedIncome: 85000,
          taxYear: 2025,
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("filing_status=single");
      expect(call[0]).toContain("estimated_income=85000");
      expect(call[0]).toContain("tax_year=2025");
    });

    it("omits unusable taxYear and estimatedIncome so FastAPI does not 422", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          positions: [],
          suggestions: [],
          wash_sale_flags: [],
          summary: {},
        }),
      } as Response);

      const file = new File(["content"], "test.csv", { type: "text/csv" });
      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({
          file,
          filingStatus: "undefined" as never,
          estimatedIncome: Number.NaN,
          taxYear: "undefined" as never,
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const url = String((globalThis.fetch as jest.Mock).mock.calls[0][0]);
      expect(url).not.toContain("tax_year=undefined");
      expect(url).not.toContain("estimated_income=undefined");
      expect(url).not.toContain("filing_status=undefined");
      expect(url).not.toMatch(/tax_year=/);
    });

    it("attaches a supplemental 1099 PDF when provided", async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          positions: [],
          suggestions: [],
          wash_sale_flags: [],
          summary: {},
        }),
      } as Response);

      const file = new File(["content"], "test.csv", { type: "text/csv" });
      const supplemental1099File = new File(["pdf"], "2024-1099.pdf", {
        type: "application/pdf",
      });
      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({
          file,
          supplemental1099File,
        });
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      const body = call[1].body as FormData;
      expect(body.get("file")).toBe(file);
      expect(body.get("supplemental_1099")).toBe(supplemental1099File);
    });

    it("handles analyze errors with detail message", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Bad Request",
        json: async () => ({ detail: "Invalid CSV format" }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({ file });
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
      expect(result.current.error).toBeInstanceOf(Error);
      expect(result.current.error?.message).toBe("Invalid CSV format");
      expect(result.current.error?.message).not.toContain("[object Object]");
    });

    it("surfaces FastAPI validation arrays as a string, never [object Object]", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: async () => ({
          detail: [
            {
              type: "missing",
              loc: ["body", "file"],
              msg: "Field required",
              input: null,
            },
          ],
        }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({ file });
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
      const message = result.current.error?.message ?? "";
      expect(message).toBe("Field required");
      expect(message).not.toMatch(/\[object Object\]/i);
      expect(getAnalysisErrorMessage(result.current.error)).toBe("Field required");
    });

    it("uses detail.message when the 400 body is a FastAPI object", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 400,
        statusText: "Bad Request",
        json: async () => ({
          detail: {
            message: "Could not parse any positions from the CSV file.",
            errors: ["Unrecognized CSV format"],
          },
        }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({ file });
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
      expect(result.current.error?.message).toBe(
        "Could not parse any positions from the CSV file.",
      );
      expect(getAnalysisErrorMessage(result.current.error)).not.toContain(
        "[object Object]",
      );
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
          estimated_annual_income: "75000",
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
      expect(call[1].cache).toBe("no-store");
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
      const { result } = renderHook(() => useSaveTaxProfile("test-user"), {
        wrapper,
      });

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
      const { result } = renderHook(() => useSaveTaxProfile("test-user"), {
        wrapper,
      });

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

    it("throws Authentication error when session is null", async () => {
      mockGetSession.mockResolvedValueOnce(null);
      globalThis.fetch = jest.fn();

      await expect(deleteAnalysis("123")).rejects.toThrow(
        "Authentication required",
      );
      expect(globalThis.fetch).not.toHaveBeenCalled();
    });

    it("throws Authentication error when access_token is missing", async () => {
      mockGetSession.mockResolvedValueOnce({ user: { id: "test" } });
      globalThis.fetch = jest.fn();

      await expect(fetchAnalysisById("123")).rejects.toThrow(
        "Authentication required",
      );
    });
  });

  describe("analyzePortfolio error message paths", () => {
    it("uses detail.message when error body has nested message", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Bad Request",
        json: async () => ({ detail: { message: "Field-level error detail" } }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useAnalyzePortfolio(), { wrapper });

      await act(async () => {
        result.current.mutate({ file });
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
      expect(result.current.error?.message).toBe("Field-level error detail");
      expect(getAnalysisErrorMessage(result.current.error)).toBe(
        "Field-level error detail",
      );
    });

    it("falls back to statusText message when json parsing fails", async () => {
      const file = new File(["content"], "test.csv", { type: "text/csv" });

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Internal Server Error",
        json: async () => {
          throw new Error("not JSON");
        },
      } as unknown as Response);

      render(<AnalyzeComponent file={file} />, { wrapper: createWrapper() });
      fireEvent.click(screen.getByText("Analyze"));

      await waitFor(() => {
        expect(screen.getByText("error")).toBeInTheDocument();
      });
    });
  });

  describe("saveTaxProfile via useSaveTaxProfile", () => {
    it("saves tax profile successfully and invalidates cache", async () => {
      const profile = {
        user_id: "test-user",
        filing_status: "single" as const,
        estimated_annual_income: 75000,
        state: "CA",
        tax_year: 2025,
      };

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: "Saved", profile }),
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useSaveTaxProfile("test-user"), {
        wrapper,
      });

      await act(async () => {
        result.current.mutate(profile);
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toContain("/api/tax-profile");
      expect(call[1].method).toBe("POST");
      expect(call[1].headers.Authorization).toBe("Bearer mock-jwt-token");
    });

    it("throws when save fails", async () => {
      const profile = {
        user_id: "test-user",
        filing_status: "single" as const,
        estimated_annual_income: 75000,
        state: "CA",
        tax_year: 2025,
      };

      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        statusText: "Unauthorized",
      } as Response);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useSaveTaxProfile("test-user"), {
        wrapper,
      });

      await act(async () => {
        result.current.mutate(profile);
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
    });
  });

  describe("getAnalysisErrorMessage", () => {
    it("returns a generic message for non-Error values", () => {
      expect(getAnalysisErrorMessage("nope")).toBe("An error occurred");
    });

    it("hides local developer instructions from network errors", () => {
      const message = getAnalysisErrorMessage(
        new Error("Failed to fetch: ECONNREFUSED"),
      );
      expect(message).toMatch(/analysis service/i);
      expect(message.toLowerCase()).not.toContain("8011");
      expect(message.toLowerCase()).not.toContain("dev:server");
    });

    it("passes through analysis error messages", () => {
      expect(getAnalysisErrorMessage(new Error("CSV could not be parsed"))).toBe(
        "CSV could not be parsed",
      );
    });

    it("never surfaces [object Object] from a stringified error object", () => {
      const message = getAnalysisErrorMessage(new Error("[object Object]"));
      expect(message).toBe("An error occurred");
      expect(message).not.toMatch(/\[object Object\]/i);
    });

    it("extracts FastAPI validation detail arrays", () => {
      const message = getAnalysisErrorMessage({
        detail: [
          {
            type: "int_parsing",
            loc: ["query", "tax_year"],
            msg: "Input should be a valid integer, unable to parse string as an integer",
            input: "undefined",
          },
        ],
      });
      expect(message).toMatch(/valid integer/i);
      expect(message).not.toMatch(/\[object Object\]/i);
    });

    it("uses detail.errors when the body has no message field", () => {
      const message = getAnalysisErrorMessage({
        detail: { errors: ["Unrecognized CSV format"] },
      });
      expect(message).toBe("Unrecognized CSV format");
      expect(message).not.toMatch(/\[object Object\]/i);
    });
  });

  describe("getBackendUnreachableMessage", () => {
    it("is safe to show in production", () => {
      const message = getBackendUnreachableMessage();
      expect(message.toLowerCase()).not.toContain("8011");
      expect(message.toLowerCase()).not.toContain("dev:server");
    });
  });
});
