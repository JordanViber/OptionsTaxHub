import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPush = jest.fn();
const mockUseAuth = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/",
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

import LandingPage from "../../app/page";
import {
  SCHEDULE_D_RECONCILE_COPY,
  WASH_ACCOUNT_SCOPE_COPY,
} from "../../lib/supplemental1099";

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

describe("LandingPage", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseAuth.mockReset();
    sessionStorage.clear();
  });

  it("still shows the product page while auth is loading", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: true });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByRole("heading", {
        name: /Your 1099 and your export will disagree/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open the 2026 sample" })).toBeInTheDocument();
  });

  it("does not bounce signed-in users off the product page", () => {
    mockUseAuth.mockReturnValue({
      user: { email: "test@example.com" },
      loading: false,
    });

    renderWithClient(<LandingPage />);

    expect(mockPush).not.toHaveBeenCalled();
    expect(
      screen.getByRole("heading", {
        name: /Your 1099 and your export will disagree/,
      }),
    ).toBeInTheDocument();
    const header = screen.getByTestId("app-header");
    expect(within(header).getByRole("link", { name: "Tax" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(
      within(header).getByRole("link", { name: "Options" }),
    ).toHaveAttribute("href", "/options");
    expect(screen.queryByText("Open Tax")).not.toBeInTheDocument();
    expect(
      within(header).getByRole("button", { name: "Account" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "See what's kept" }),
    ).toHaveAttribute("href", "/dashboard");
    expect(
      screen.queryByRole("link", { name: /Sign In/i }),
    ).not.toBeInTheDocument();
  });

  it("renders hero section for unauthenticated users", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByRole("heading", {
        name: /Your 1099 and your export will disagree/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(SCHEDULE_D_RECONCILE_COPY, { exact: false }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Broker 1099 uses settlement date/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/your export uses trade date/i)).toBeInTheDocument();
    expect(screen.getByText(/CSV export plus the 1099/i)).toBeInTheDocument();
    expect(screen.getByText(/not a Form 8949 rebuild/i)).toBeInTheDocument();
    expect(screen.queryByText(/SPX 12\/31/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/not a software bug/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(WASH_ACCOUNT_SCOPE_COPY, { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/toward Schedule D/i).length).toBeGreaterThan(0);
    expect(
      screen.queryByText(/Keep more of what you trade/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/options traders only/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/we file Schedule D/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/CPA advice/i)).not.toBeInTheDocument();
  });

  it("renders header CTAs for the sample, Tax, Options, and Sign in", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    const header = screen.getByTestId("app-header");
    expect(
      within(header).getByRole("button", { name: "Try the sample" }),
    ).toBeInTheDocument();
    expect(within(header).getByRole("link", { name: "Tax" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(
      within(header).getByRole("link", { name: "Options" }),
    ).toHaveAttribute("href", "/options");
    expect(
      within(header).getByRole("link", { name: "Sign in" }),
    ).toHaveAttribute("href", "/auth/signin?next=/");
    expect(screen.queryByText("Open Tax")).not.toBeInTheDocument();
  });

  it("renders feature columns", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("Harvest queue")).toBeInTheDocument();
    expect(screen.getByText("Wash-sale radar")).toBeInTheDocument();
    expect(screen.getByText("Lot ledger")).toBeInTheDocument();
    expect(screen.getByText(/Robinhood whole-portfolio tax/i)).toBeInTheDocument();
    expect(screen.getByText(/stocks and options — ranked by federal savings/i)).toBeInTheDocument();
    expect(screen.getByText(/Whole book, not options-only/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        /substantially identical security, including options or contracts to acquire it, bought within 30 days before or after the sale \(a 61-day window\)/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Same-symbol buys inside 30 days,/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/Wash flags on a 1099 are often scoped to that one account/i),
    ).toBeInTheDocument();
  });

  it("renders How it works with 3 steps", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("How it works")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: /Robinhood whole-portfolio tax/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Drop a CSV")).toBeInTheDocument();
    expect(screen.getByText("Read the compare")).toBeInTheDocument();
    expect(
      screen.getByText(/1099 vs export first — toward Schedule D/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Take the packet")).toBeInTheDocument();
  });

  it("renders optional account section", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByText(/Sign in for the year that follows you/),
    ).toBeInTheDocument();
    expect(screen.getByText("Saved runs")).toBeInTheDocument();
    expect(screen.getByText("Update the book")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "See what's kept" }),
    ).toHaveAttribute("href", "/auth/signin?next=/");
  });

  it("sends signed-in See what's kept to the desk, not sign-in", () => {
    mockUseAuth.mockReturnValue({
      user: { email: "test@example.com" },
      loading: false,
    });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByRole("link", { name: "See what's kept" }),
    ).toHaveAttribute("href", "/dashboard");
    expect(
      screen.getByRole("link", { name: "See what's kept" }),
    ).not.toHaveAttribute("href", "/auth/signin");
  });

  it("renders one disclaimer only — TaxDisclaimer, not a second footer legal", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getAllByText(/For educational and simulation purposes only/),
    ).toHaveLength(1);
    expect(
      screen.queryByText(/educational and informational purposes only/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("preview shows the 2026 sample 1099 vs export totals", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByTestId("landing-1099-broker")).toHaveTextContent("$2,699");
    expect(screen.getByTestId("landing-1099-broker")).toHaveTextContent("Wash $924");
    expect(screen.getByTestId("landing-1099-export")).toHaveTextContent("$0");
    expect(screen.getByTestId("landing-1099-export")).toHaveTextContent("Wash $924");
    expect(screen.getAllByText(/1099 vs your export/i).length).toBeGreaterThan(0);
    expect(
      screen.queryByText(/Federal harvest still on the table/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("$2,086")).not.toBeInTheDocument();
  });

  it("sample and CSV CTAs go to the desk with the right intent", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    fireEvent.click(screen.getByRole("button", { name: "Open the 2026 sample" }));
    expect(sessionStorage.getItem("oth-load-sample")).toBe("1");
    expect(mockPush).toHaveBeenCalledWith("/dashboard");

    mockPush.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "I have a CSV" }));
    expect(sessionStorage.getItem("oth-upload-intent")).toBe("1");
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("uses real links for Sign in, Tax, Options, and the wordmark", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);

    const header = screen.getByTestId("app-header");
    expect(within(header).getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/auth/signin?next=/",
    );
    expect(within(header).getByRole("link", { name: "Tax" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(
      within(header).getByRole("link", { name: "Options" }),
    ).toHaveAttribute("href", "/options");
    expect(
      within(header).getByRole("link", { name: /OptionsTaxHub/i }),
    ).toHaveAttribute("href", "/");
    fireEvent.click(within(header).getByRole("button", { name: "Try the sample" }));
    expect(sessionStorage.getItem("oth-load-sample")).toBe("1");
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("does not claim in-memory-only storage or state tax savings", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);

    expect(screen.queryByText(/in-memory only/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/never stored permanently/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/federal and state tax savings/i)).not.toBeInTheDocument();
    expect(screen.getByText(/State tax is not included/i)).toBeInTheDocument();
    expect(
      screen.getAllByText(/saved to your account history/i).length,
    ).toBeGreaterThan(0);
  });

  it("describes the $49 PDF as a packaged year-close document", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);

    expect(
      screen.getAllByText(
        /packages the reconciliation, wash chains, and harvest notes into a downloadable year-close document/i,
      ).length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByText(/Lot-matched 1099-B is in that PDF/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/lot-level 1099 reconciliation is in the \$49 PDF/i),
    ).not.toBeInTheDocument();
    expect(screen.getAllByText(/not a filed Form 8949/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/not a Form 8949 rebuild/i)).toBeInTheDocument();
    expect(screen.queryByText(/file your Form 8949/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/full lot rebuild from the PDF/i),
    ).not.toBeInTheDocument();
  });

  it("does not render the options what-if on the tax-first landing page", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);
    expect(screen.queryByTestId("entry-analysis-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("entry-rank-find")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: /Your 1099 and your export will disagree/,
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /connect robinhood/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /options desk/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("landing-options-tools")).toHaveTextContent(
      /Rank long LEAPs/i,
    );
    expect(screen.getByRole("link", { name: "Open Options" })).toHaveAttribute(
      "href",
      "/options",
    );
    expect(
      screen.getByRole("heading", {
        name: /Robinhood whole-portfolio tax/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^tax desk$/i }),
    ).not.toBeInTheDocument();
  });

  it("does not render a card field on the marketing home page", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    const { container } = renderWithClient(<LandingPage />);
    expect(container.querySelector("input[autocomplete='cc-number']")).toBeNull();
    expect(container.querySelector("input[autocomplete='cc-csc']")).toBeNull();
    expect(container.querySelector("input[name='cardNumber']")).toBeNull();
    expect(screen.queryByLabelText(/card number/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Year-close packet — $49")).not.toBeInTheDocument();
  });

  it("links to the privacy page", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);
    expect(screen.getByRole("link", { name: "Privacy" })).toHaveAttribute(
      "href",
      "/privacy",
    );
  });

  it("has no card field on the marketing home page", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    const { container } = renderWithClient(<LandingPage />);
    expect(container.querySelector("input[type='password']")).toBeNull();
    expect(container.querySelector("input[name='cardnumber']")).toBeNull();
    expect(container.querySelector("input[autocomplete='cc-number']")).toBeNull();
    expect(screen.queryByLabelText(/card number/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Year-close packet — $49")).not.toBeInTheDocument();
  });
});
