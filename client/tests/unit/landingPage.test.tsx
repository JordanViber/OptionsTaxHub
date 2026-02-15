import { render, screen } from "@testing-library/react";
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
  });

  it("renders nothing while auth is loading", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: true });

    const { container } = renderWithClient(<LandingPage />);

    // Should render null during loading
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
      screen.getByText(/Smart Tax Optimization/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/for Options Traders/),
    ).toBeInTheDocument();
  });

  it("renders navigation with Sign In and Get Started buttons", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    // There are two Sign In buttons (nav + hero), so use getAllByText
    expect(screen.getAllByText("Sign In")).toHaveLength(2);
    expect(screen.getByText("Get Started")).toBeInTheDocument();
  });

  it("renders all six feature cards", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("CSV Upload & Analysis")).toBeInTheDocument();
    expect(screen.getByText("Tax-Loss Harvesting")).toBeInTheDocument();
    expect(screen.getByText("Wash-Sale Detection")).toBeInTheDocument();
    expect(screen.getByText("Tax Savings Estimates")).toBeInTheDocument();
    expect(screen.getByText("Instant Results")).toBeInTheDocument();
    expect(screen.getByText("Privacy First")).toBeInTheDocument();
  });

  it("renders How It Works section with 3 steps", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(screen.getByText("How It Works")).toBeInTheDocument();
    expect(screen.getByText("Create your free account")).toBeInTheDocument();
    expect(screen.getByText("Upload your CSV export")).toBeInTheDocument();
    expect(screen.getByText("Get instant tax insights")).toBeInTheDocument();
  });

  it("renders CTA section", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByText("Ready to Optimize Your Taxes?"),
    ).toBeInTheDocument();
  });

  it("renders footer with disclaimer", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByText(/educational and informational purposes only/),
    ).toBeInTheDocument();
  });

  it("renders free badge chip", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });

    renderWithClient(<LandingPage />);

    expect(
      screen.getByText("Free to use — No credit card required"),
    ).toBeInTheDocument();
  });
});
