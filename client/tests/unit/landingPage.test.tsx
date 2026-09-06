import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPush = jest.fn();
const mockUseAuth = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

import LandingPage from "../../app/page";

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
    expect(screen.getByRole("link", { name: "Open desk" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.getByRole("link", { name: "Desk" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
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
      screen.getByText(/Broker 1099 uses settlement date/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/your export uses trade date/i)).toBeInTheDocument();
    expect(screen.getAllByText(/SPX 12\/31/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/not a software bug/i).length).toBeGreaterThan(0);
    expect(
      screen.queryByText(/Keep more of what you trade/),
    ).not.toBeInTheDocument();
  });

  it("renders navigation with Sign In and Open desk", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getAllByText("Sign In").length).toBeGreaterThan(0);
    expect(screen.getByText("Open desk")).toBeInTheDocument();
  });

  it("renders feature columns", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("Harvest queue")).toBeInTheDocument();
    expect(screen.getByText("Wash-sale radar")).toBeInTheDocument();
    expect(screen.getByText("Lot ledger")).toBeInTheDocument();
    expect(screen.getByText(/Robinhood whole-portfolio tax desk/i)).toBeInTheDocument();
    expect(screen.getByText(/stocks and options — ranked by federal savings/i)).toBeInTheDocument();
    expect(screen.getByText(/Whole book, not options-only/i)).toBeInTheDocument();
  });

  it("renders How the desk works with 3 steps", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("How the desk works")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: /Robinhood whole-portfolio tax desk/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Drop a CSV")).toBeInTheDocument();
    expect(screen.getByText("Read the desk")).toBeInTheDocument();
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
    ).toHaveAttribute("href", "/auth/signin");
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

  it("uses real links for Sign In and Open desk", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);

    const signIn = screen.getAllByRole("link", { name: /Sign In/i });
    expect(signIn.length).toBeGreaterThan(0);
    expect(signIn[0]).toHaveAttribute("href", "/auth/signin");
    expect(screen.getByRole("link", { name: "Open desk" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    const wordmarks = screen.getAllByRole("link", { name: /OptionsTaxHub/i });
    expect(wordmarks.length).toBeGreaterThan(0);
    expect(wordmarks[0]).toHaveAttribute("href", "/");
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

  it("claims lot-matched 1099-B on the packet and does not claim Form 8949 filing", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    renderWithClient(<LandingPage />);

    expect(screen.getAllByText(/lot-matched 1099-B/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/not a filed Form 8949/i)).toBeInTheDocument();
    expect(screen.queryByText(/file your Form 8949/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/full lot rebuild from the PDF/i),
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
