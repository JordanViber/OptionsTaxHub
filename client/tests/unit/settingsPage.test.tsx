import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  describe("Authentication", () => {
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
  });

  describe("Rendering", () => {
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

  describe("Form State Management", () => {
    it("populates form with existing profile data", async () => {
      mockUseAuth.mockReturnValue({
        user: { id: "user-1", email: "test@example.com" },
        loading: false,
      });
      mockUseTaxProfile.mockReturnValue({
        data: {
          user_id: "user-1",
          filing_status: "married_filing_jointly",
          estimated_annual_income: 150000,
          state: "NY",
          tax_year: 2025,
        },
        isLoading: false,
      });
      mockUseSaveTaxProfile.mockReturnValue({
        mutate: jest.fn(),
        isPending: false,
      });

      renderWithClient(<SettingsPage />);

      await screen.findByLabelText("Filing Status");

      const incomeInput = screen.getByLabelText("Estimated Annual Income");
      expect((incomeInput as HTMLInputElement).value).toBe("150000");

      const taxYearSelect = screen.getByLabelText("Tax Year");
      expect(taxYearSelect).toBeInTheDocument();
    });

    it("handles default values when profile has missing fields", () => {
      mockUseAuth.mockReturnValue({
        user: { id: "user-1", email: "test@example.com" },
        loading: false,
      });
      mockUseTaxProfile.mockReturnValue({
        data: {
          user_id: "user-1",
          filing_status: "single",
          estimated_annual_income: null,
          state: null,
          tax_year: 2025,
        },
        isLoading: false,
      });
      mockUseSaveTaxProfile.mockReturnValue({
        mutate: jest.fn(),
        isPending: false,
      });

      renderWithClient(<SettingsPage />);

      const incomeInput = screen.getByLabelText("Estimated Annual Income");
      expect((incomeInput as HTMLInputElement).value).toBe("75000"); // default
    });
  });

  describe("Form Interactions", () => {
    it("allows changing estimated income", async () => {
      const saveMutate = jest.fn();
      mockUseAuth.mockReturnValue({
        user: { id: "user-1", email: "test@example.com" },
        loading: false,
      });
      mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
      mockUseSaveTaxProfile.mockReturnValue({
        mutate: saveMutate,
        isPending: false,
      });

      renderWithClient(<SettingsPage />);

      const incomeInput = screen.getByLabelText("Estimated Annual Income");
      await userEvent.clear(incomeInput);
      await userEvent.type(incomeInput, "100000");

      const saveButton = screen.getByRole("button", {
        name: /Save Tax Profile/,
      });
      await userEvent.click(saveButton);

      expect(saveMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          estimated_annual_income: 100000,
        }),
        expect.any(Object),
      );
    });

    it("renders form fields correctly", () => {
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

      // Verify all form fields are present
      expect(screen.getByLabelText("Filing Status")).toBeInTheDocument();
      expect(
        screen.getByLabelText("Estimated Annual Income"),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("State")).toBeInTheDocument();
      expect(screen.getByLabelText("Tax Year")).toBeInTheDocument();
    });

    it("has correct default values for new profiles", () => {
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

      const incomeInput = screen.getByLabelText("Estimated Annual Income");
      expect((incomeInput as HTMLInputElement).value).toBe("75000");
    });
  });

  describe("Save State", () => {
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

      expect(
        screen.getByRole("button", { name: /Saving/ }),
      ).toBeInTheDocument();
    });

    it("disables save button while saving", () => {
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

      const saveButton = screen.getByRole("button", { name: /Saving/ });
      expect(saveButton).toBeDisabled();
    });

    it("calls mutate function when save button clicked", async () => {
      const saveMutate = jest.fn();
      mockUseAuth.mockReturnValue({
        user: { id: "user-1", email: "test@example.com" },
        loading: false,
      });
      mockUseTaxProfile.mockReturnValue({ data: null, isLoading: false });
      mockUseSaveTaxProfile.mockReturnValue({
        mutate: saveMutate,
        isPending: false,
      });

      renderWithClient(<SettingsPage />);

      const saveButton = screen.getByRole("button", {
        name: /Save Tax Profile/,
      });
      await userEvent.click(saveButton);

      expect(saveMutate).toHaveBeenCalled();
      expect(saveMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          user_id: "user-1",
          filing_status: "single",
          estimated_annual_income: 75000,
          state: "",
          tax_year: 2025,
        }),
        expect.any(Object),
      );
    });
  });

  describe("Navigation", () => {
    it("navigates to dashboard when back button is clicked", async () => {
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

      const backButton = screen.getByRole("button", { name: /Dashboard/ });
      await userEvent.click(backButton);

      expect(mockPush).toHaveBeenCalledWith("/dashboard");
    });

    it("includes all form fields with proper labels", () => {
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
        screen.getByText(
          /These settings help estimate your tax savings from harvesting losses/,
        ),
      ).toBeInTheDocument();
    });
  });

  describe("Edge Cases", () => {
    it("handles empty state gracefully", () => {
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

      const stateSelect = screen.getByLabelText("State");
      expect(stateSelect).toBeInTheDocument();
    });

    it("includes dollar sign in income field", () => {
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

      const incomeField = screen.getByLabelText("Estimated Annual Income");
      expect(incomeField).toBeInTheDocument();

      // The dollar sign is in the InputAdornment, visible in the UI
      expect(screen.getByText("$", { selector: "p" })).toBeInTheDocument();
    });
  });
});
