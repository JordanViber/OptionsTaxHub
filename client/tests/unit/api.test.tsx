import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import {
  usePortfolioHistory,
  usePushNotificationSubscription,
  useUploadPortfolio,
} from "../../lib/api";

type WrapperProps = { children: React.ReactNode };

interface SubscriptionPayload {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

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

function UploadComponent({ file }: { file: File }) {
  const { mutate, data, error } = useUploadPortfolio();
  return (
    <div>
      <button onClick={() => mutate(file)}>Upload</button>
      <span>{data ? "success" : error ? "error" : "idle"}</span>
    </div>
  );
}

function HistoryComponent() {
  const { data, error } = usePortfolioHistory(true);
  return (
    <div>
      <span>{data ? "history" : error ? "error" : "idle"}</span>
    </div>
  );
}

function HistoryDisabledComponent() {
  const { data, error } = usePortfolioHistory();
  return (
    <div>
      <span>{data ? "history" : error ? "error" : "idle"}</span>
    </div>
  );
}

function PushComponent({
  subscription,
}: {
  subscription: SubscriptionPayload;
}) {
  const { mutate, data, error } = usePushNotificationSubscription();
  return (
    <div>
      <button onClick={() => mutate(subscription)}>Subscribe</button>
      <span>{data ? "success" : error ? "error" : "idle"}</span>
    </div>
  );
}

describe("api hooks", () => {
  beforeEach(() => {
    jest.resetAllMocks();
  });

  it("uploads portfolio successfully", async () => {
    const logSpy = jest.spyOn(console, "log").mockImplementation(() => {});
    const file = new File(["content"], "test.csv", { type: "text/csv" });

    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ symbol: "AAPL", qty: 1, price: 100 }],
    } as Response);

    render(<UploadComponent file={file} />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() => {
      expect(screen.getByText("success")).toBeInTheDocument();
    });

    expect(logSpy).toHaveBeenCalledWith("Portfolio uploaded successfully:", [
      { symbol: "AAPL", qty: 1, price: 100 },
    ]);

    logSpy.mockRestore();
  });

  it("handles upload errors", async () => {
    const errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
    const file = new File(["content"], "test.csv", { type: "text/csv" });

    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      statusText: "Bad Request",
    } as Response);

    render(<UploadComponent file={file} />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() => {
      expect(screen.getByText("error")).toBeInTheDocument();
    });

    expect(errorSpy).toHaveBeenCalled();

    errorSpy.mockRestore();
  });

  it("fetches portfolio history successfully", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ symbol: "AAPL", qty: 1, price: 100 }],
    } as Response);

    render(<HistoryComponent />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("history")).toBeInTheDocument();
    });
  });

  it("handles portfolio history errors", async () => {
    global.fetch = jest.fn().mockResolvedValue({
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

    global.fetch = fetchSpy;

    render(<HistoryDisabledComponent />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("idle")).toBeInTheDocument();
    });

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("subscribes to push notifications successfully", async () => {
    const logSpy = jest.spyOn(console, "log").mockImplementation(() => {});
    const subscription = {
      endpoint: "https://example.com/endpoint",
      keys: { p256dh: "key", auth: "auth" },
    };

    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, message: "ok" }),
    } as Response);

    render(<PushComponent subscription={subscription} />, {
      wrapper: createWrapper(),
    });

    fireEvent.click(screen.getByText("Subscribe"));

    await waitFor(() => {
      expect(screen.getByText("success")).toBeInTheDocument();
    });

    expect(logSpy).toHaveBeenCalledWith("Push subscription successful:", {
      success: true,
      message: "ok",
    });

    logSpy.mockRestore();
  });

  it("handles push notification subscription errors", async () => {
    const errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
    const subscription = {
      endpoint: "https://example.com/endpoint",
      keys: { p256dh: "key", auth: "auth" },
    };

    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      statusText: "Forbidden",
    } as Response);

    render(<PushComponent subscription={subscription} />, {
      wrapper: createWrapper(),
    });

    fireEvent.click(screen.getByText("Subscribe"));

    await waitFor(() => {
      expect(screen.getByText("error")).toBeInTheDocument();
    });

    expect(errorSpy).toHaveBeenCalled();

    errorSpy.mockRestore();
  });
});
