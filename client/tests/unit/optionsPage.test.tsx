import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import OptionsPage from "../../app/options/page";
import type { Position } from "../../lib/types";

const mockPush = jest.fn();
const mockAuth = {
  user: null as null | {
    id: string;
    email_confirmed_at?: string | null;
  },
  loading: false,
  signOut: jest.fn(),
};

jest.mock("next/link", () => {
  return ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  );
});

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/options",
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockAuth,
}));

function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

jest.mock("../../lib/api", () => ({
  useRhChainMutation: () => ({
    mutateAsync: jest.fn(),
    isPending: false,
    reset: jest.fn(),
  }),
  fetchRhStatus: jest.fn().mockResolvedValue({ connected: false }),
}));

const nvdaStock = {
  position_id: "NVDA:stock",
  symbol: "NVDA",
  display_label: "NVDA",
  quantity: 52,
  avg_cost_basis: 280,
  total_cost_basis: 14560,
  current_price: 250,
  market_value: 13000,
  unrealized_pnl: -1560,
  unrealized_pnl_pct: -10,
  earliest_purchase_date: "2026-01-15",
  holding_period_days: 200,
  is_long_term: false,
  asset_type: "stock",
  tax_lots: [],
  wash_sale_risk: false,
} as Position;

describe("Options page", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockAuth.user = null;
    mockAuth.loading = false;
    sessionStorage.clear();
  });

  it("renders the what-if desk and a guest sign-in that returns to Options", async () => {
    render(<OptionsPage />);

    expect(
      await screen.findByRole("heading", { name: /LEAP ranking and what-if/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("entry-analysis-panel")).toBeInTheDocument();
    expect(screen.getByTestId("entry-rank-leaps")).toBeInTheDocument();
    expect(screen.getByTestId("entry-guest-signin")).toHaveAttribute(
      "href",
      "/auth/signin?next=/options",
    );
    expect(
      screen.queryByText(/Connect Robinhood after sign-in/i),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tax" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.getByRole("link", { name: "Options" })).toHaveAttribute(
      "href",
      "/options",
    );
    expect(screen.getByTestId("header-upload")).toBeInTheDocument();
    expect(screen.getByTestId("header-saved-runs")).toBeInTheDocument();
    expect(screen.getByTestId("header-sign-in")).toHaveAttribute(
      "href",
      "/auth/signin?next=/options",
    );
    expect(
      screen.queryByRole("button", { name: /^Settings$/ }),
    ).not.toBeInTheDocument();

    const premium = screen.getByTestId("entry-premium");
    const hint = screen.getByTestId("entry-empty");
    expect(hint).toHaveTextContent(/Enter premium/i);
    expect(premium).toHaveAttribute("aria-describedby", hint.getAttribute("id"));
  });

  it("uses stored tax positions for the one-line context", async () => {
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify({ positions: [nvdaStock] }),
    );
    render(<OptionsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("entry-analysis-panel")).toBeInTheDocument();
    });

    const future = new Date();
    future.setFullYear(future.getFullYear() + 1);
    const expiration = `${future.getFullYear()}-${String(future.getMonth() + 1).padStart(2, "0")}-${String(future.getDate()).padStart(2, "0")}`;
    fireEvent.change(screen.getByTestId("entry-symbol"), {
      target: { value: "NVDA" },
    });
    fireEvent.change(screen.getByTestId("entry-strike"), {
      target: { value: "250" },
    });
    fireEvent.change(screen.getByTestId("entry-expiration"), {
      target: { value: expiration },
    });
    fireEvent.change(screen.getByTestId("entry-premium"), {
      target: { value: "4.20" },
    });

    expect(screen.getByTestId("entry-context")).toHaveTextContent(
      "Open NVDA: 52 sh",
    );
  });

  it("sends unconfirmed users to confirm-email", async () => {
    mockAuth.user = { id: "user-1", email_confirmed_at: null };
    render(<OptionsPage />);
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/auth/confirm-email");
    });
  });
});
