import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import {
  useAnalyzePortfolio,
  usePortfolioHistory,
  useUploadPortfolio,
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
  const { data, error } = usePortfolioHistory(true);
  return (
    <div>
      <span>{getHistoryStatus(data, error)}</span>
    </div>
  );
}

function HistoryDisabledComponent() {
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

describe("api hooks", () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    jest.resetAllMocks();
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

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

    // Successfully uploaded without console logging
    expect(globalThis.fetch).toHaveBeenCalled();
  });

  it("handles upload errors", async () => {
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

    // Error handled gracefully without console logging
    expect(globalThis.fetch).toHaveBeenCalled();
  });

  it("fetches portfolio history successfully", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ symbol: "AAPL", qty: 1, price: 100 }],
    } as Response);

    render(<HistoryComponent />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("history")).toBeInTheDocument();
    });
  });

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

  it("does not fetch history when disabled", async () => {
    const fetchSpy = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    } as Response);

    globalThis.fetch = fetchSpy;

    render(<HistoryDisabledComponent />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("idle")).toBeInTheDocument();
    });

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("analyzes portfolio successfully", async () => {
    const file = new File(["content"], "test.csv", { type: "text/csv" });

    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        positions: [{ symbol: "AAPL" }],
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

    expect(globalThis.fetch).toHaveBeenCalled();
  });

  it("handles analyze portfolio errors", async () => {
    const file = new File(["content"], "test.csv", { type: "text/csv" });

    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: false,
      statusText: "Bad Request",
    } as Response);

    render(<AnalyzeComponent file={file} />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Analyze"));

    await waitFor(() => {
      expect(screen.getByText("error")).toBeInTheDocument();
    });

    expect(globalThis.fetch).toHaveBeenCalled();
  });
});
