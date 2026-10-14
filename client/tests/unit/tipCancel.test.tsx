import { render as rtlRender, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPush = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/tips/cancel",
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

import TipCancelPage from "../../app/tips/cancel/page";

describe("TipCancelPage", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("renders No Worries heading", () => {
    render(<TipCancelPage />);

    expect(screen.getByText("No Worries!")).toBeInTheDocument();
  });

  it("renders reassuring message", () => {
    render(<TipCancelPage />);

    expect(
      screen.getByText(/Tips are totally optional/),
    ).toBeInTheDocument();
  });

  it("renders Back to Dashboard button", () => {
    render(<TipCancelPage />);

    expect(
      screen.getByRole("button", { name: /Back to Dashboard/ }),
    ).toBeInTheDocument();
  });

  it("navigates to dashboard when button is clicked", () => {
    render(<TipCancelPage />);

    fireEvent.click(
      screen.getByRole("button", { name: /Back to Dashboard/ }),
    );

    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("displays wave emoji", () => {
    render(<TipCancelPage />);

    expect(screen.getByText("👋")).toBeInTheDocument();
  });
});
