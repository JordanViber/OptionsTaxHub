import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockPush = jest.fn();
const mockUseAuth = jest.fn();
const mockUseTaxProfile = jest.fn();
const mockUseSaveTaxProfile = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

jest.mock("../../lib/api", () => ({
  useTaxProfile: () => mockUseTaxProfile(),
  useSaveTaxProfile: () => mockUseSaveTaxProfile(),
}));

jest.mock("../../app/components/TaxDisclaimer", () => () => (
  <div data-testid="tax-disclaimer">Disclaimer</div>
));

import SettingsPage from "../../app/settings/page";

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

describe("SettingsPage", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseAuth.mockReset();
    mockUseTaxProfile.mockReset();
    mockUseSaveTaxProfile.mockReset();
  });

  it("shows loading spinner when auth is loading", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: true });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("redirects to signin when not authenticated", () => {
    mockUseAuth.mockReturnValue({ user: null, loading: false });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    expect(mockPush).toHaveBeenCalledWith("/auth/signin");
  });

  it("renders settings form for authenticated user", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "user-1", email: "test@example.com" },
      loading: false,
    });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    expect(screen.getByText("Tax Profile Settings")).toBeInTheDocument();
    expect(screen.getByText("Your Tax Profile")).toBeInTheDocument();
    expect(screen.getByLabelText("Filing Status")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Estimated Annual Income"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("State")).toBeInTheDocument();
    expect(screen.getByLabelText("Tax Year")).toBeInTheDocument();
  });

  it("renders save button", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "user-1", email: "test@example.com" },
      loading: false,
    });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    expect(
      screen.getByRole("button", { name: /Save Tax Profile/ }),
    ).toBeInTheDocument();
  });

  it("shows saving text when mutation is pending", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "user-1", email: "test@example.com" },
      loading: false,
    });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: true,
    });

    renderWithClient(<SettingsPage />);

    expect(screen.getByRole("button", { name: /Saving/ })).toBeInTheDocument();
  });

  it("renders tax disclaimer", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "user-1", email: "test@example.com" },
      loading: false,
    });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    expect(screen.getByTestId("tax-disclaimer")).toBeInTheDocument();
  });

  it("renders dashboard back button", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "user-1", email: "test@example.com" },
      loading: false,
    });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    expect(
      screen.getByRole("button", { name: /Dashboard/ }),
    ).toBeInTheDocument();
  });

  it("shows profile loading spinner while fetching profile", () => {
    mockUseAuth.mockReturnValue({
      user: { id: "user-1", email: "test@example.com" },
      loading: false,
    });
    mockUseTaxProfile.mockReturnValue({ data: null, isLoading: true });
    mockUseSaveTaxProfile.mockReturnValue({
      mutate: jest.fn(),
      isPending: false,
    });

    renderWithClient(<SettingsPage />);

    // Both auth spinner and profile spinner use progressbar
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });
});
