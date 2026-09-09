import { render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("next/link", () => {
  return ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>;
});

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/options",
}));

const mockUseAuth = jest.fn();
jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

jest.mock("../../lib/supabase", () => ({
  isEmailConfirmed: (user: { email_confirmed_at?: string | null }) =>
    Boolean(user?.email_confirmed_at),
}));

jest.mock("../../app/components/EntryAnalysisPanel", () => ({
  __esModule: true,
  default: ({ positions }: { positions: unknown[] }) => (
    <div data-testid="entry-analysis-panel">{positions.length} positions</div>
  ),
}));

jest.mock("../../app/components/DeskSwitcher", () => ({
  __esModule: true,
  default: () => <div data-testid="desk-switcher" />,
  rememberDesk: jest.fn(),
  readLastDesk: jest.fn(() => "options"),
}));

jest.mock("../../app/components/TaxDisclaimer", () => () => (
  <div data-testid="tax-disclaimer">Disclaimer</div>
));

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

import OptionsPage from "../../app/options/page";

describe("OptionsDeskPage", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseAuth.mockReset();
    sessionStorage.clear();
  });

  it("shows a loading spinner while auth is loading", async () => {
    mockUseAuth.mockReturnValue({ user: null, loading: true });
    renderWithClient(<OptionsPage />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("confirmed user sees the options desk and hides Sign In button", async () => {
    mockUseAuth.mockReturnValue({
      user: {
        id: "u1",
        email: "a@b.com",
        email_confirmed_at: "2025-01-01T00:00:00Z",
      },
      loading: false,
    });
    await act(async () => {
      renderWithClient(<OptionsPage />);
    });
    expect(screen.getByText(/Options desk/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Sign In/i })).not.toBeInTheDocument();
  });

  it("unconfirmed user is redirected to /auth/confirm-email", async () => {
    mockUseAuth.mockReturnValue({
      user: { id: "u2", email: "b@c.com", email_confirmed_at: null },
      loading: false,
    });
    await act(async () => {
      renderWithClient(<OptionsPage />);
    });
    expect(mockPush).toHaveBeenCalledWith("/auth/confirm-email");
  });

  it("shows empty book message when no sessionStorage analysis", async () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    await act(async () => {
      renderWithClient(<OptionsPage />);
    });
    expect(screen.getByTestId("options-book-status")).toHaveTextContent(
      /No book loaded/i,
    );
  });

  it("shows restored positions when sessionStorage has an analysis", async () => {
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify({
        positions: [
          {
            position_id: "AAPL:stock",
            symbol: "AAPL",
            display_label: "AAPL",
            quantity: 10,
            avg_cost_basis: 150,
            total_cost_basis: 1500,
            current_price: 175,
            market_value: 1750,
            unrealized_pnl: 250,
            unrealized_pnl_pct: 16.67,
            earliest_purchase_date: "2026-01-01",
            holding_period_days: 100,
            is_long_term: false,
            asset_type: "stock",
            tax_lots: [],
            wash_sale_risk: false,
          },
        ],
      }),
    );
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    await act(async () => {
      renderWithClient(<OptionsPage />);
    });
    expect(screen.getByTestId("options-book-status")).toHaveTextContent(
      /Using the book already loaded/i,
    );
  });

  it("handles corrupt sessionStorage JSON gracefully", async () => {
    sessionStorage.setItem("optionstaxhub-analysis", "NOT_JSON{{{");
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    await act(async () => {
      renderWithClient(<OptionsPage />);
    });
    expect(screen.getByTestId("options-book-status")).toHaveTextContent(
      /No book loaded/i,
    );
  });
});
