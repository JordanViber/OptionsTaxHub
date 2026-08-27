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

  it("renders nothing while auth is loading", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: true });

    const { container } = renderWithClient(<LandingPage />);

    expect(container.firstChild).toBeNull();
  });

  it("redirects authenticated users to dashboard", () => {
    mockUseAuth.mockReturnValue({
      user: { email: "test@example.com" },
      loading: false,
    });

    renderWithClient(<LandingPage />);

    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("renders hero section for unauthenticated users", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByText(/Keep more of what you trade/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/year-end tax desk/i),
    ).toBeInTheDocument();
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
  });

  it("renders How the desk works with 3 steps", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("How the desk works")).toBeInTheDocument();
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
  });

  it("renders footer with disclaimer", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByText(/educational and informational purposes only/),
    ).toBeInTheDocument();
  });

  it("preview shows the 2026 sample harvest and wash-sale counts", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("$2,086")).toBeInTheDocument();
    expect(screen.getByText("3 wash sales")).toBeInTheDocument();
    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.getByText("META")).toBeInTheDocument();
    expect(screen.getByText(/Federal harvest still on the table/i)).toBeInTheDocument();
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
