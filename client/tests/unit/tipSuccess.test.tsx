import { render as rtlRender, screen, act, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPush = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/tips/success",
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => ({ user: null, loading: false, signOut: jest.fn() }),
}));

function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

import TipSuccessPage from "../../app/tips/success/page";

describe("TipSuccessPage", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockPush.mockClear();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("renders thank you message", () => {
    render(<TipSuccessPage />);

    expect(screen.getByText(/Thank You/)).toBeInTheDocument();
  });

  it("renders gratitude message", () => {
    render(<TipSuccessPage />);

    expect(
      screen.getByText(/Your generosity means the world to us/),
    ).toBeInTheDocument();
  });

  it("renders Back to Dashboard button", () => {
    render(<TipSuccessPage />);

    expect(
      screen.getByRole("button", { name: /Back to Dashboard/ }),
    ).toBeInTheDocument();
  });

  it("navigates to dashboard when button is clicked", () => {
    render(<TipSuccessPage />);

    fireEvent.click(
      screen.getByRole("button", { name: /Back to Dashboard/ }),
    );

    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("shows initial countdown of 8 seconds", () => {
    render(<TipSuccessPage />);

    expect(screen.getByText("Redirecting in 8 seconds...")).toBeInTheDocument();
  });

  it("decrements countdown each second", () => {
    render(<TipSuccessPage />);

    act(() => {
      jest.advanceTimersByTime(1000);
    });

    expect(screen.getByText("Redirecting in 7 seconds...")).toBeInTheDocument();

    act(() => {
      jest.advanceTimersByTime(1000);
    });

    expect(screen.getByText("Redirecting in 6 seconds...")).toBeInTheDocument();
  });

  it("auto-redirects to dashboard after 8 seconds", () => {
    render(<TipSuccessPage />);

    act(() => {
      jest.advanceTimersByTime(8000);
    });

    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });
});
