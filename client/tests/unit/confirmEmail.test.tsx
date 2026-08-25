import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ConfirmEmailPage from "../../app/auth/confirm-email/page";
import { useRouter } from "next/navigation";

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
}));

const mockPush = jest.fn();
const mockConsume = jest.fn();
const mockResend = jest.fn();

jest.mock("@/lib/supabase", () => ({
  consumeEmailConfirmLink: (...args: unknown[]) => mockConsume(...args),
  resendSignupConfirmation: (...args: unknown[]) => mockResend(...args),
  isEmailConfirmed: (user: { email_confirmed_at?: string | null } | null) =>
    Boolean(user?.email_confirmed_at),
}));

describe("Confirm Email Page", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (useRouter as jest.Mock).mockReturnValue({
      push: mockPush,
      replace: jest.fn(),
    });
    mockConsume.mockResolvedValue({ session: null, consumedLink: false });
    mockResend.mockResolvedValue(undefined);
  });

  it("shows a confirmed state after a valid confirmation link", async () => {
    mockConsume.mockResolvedValue({
      session: {
        user: {
          email: "mira@example.com",
          email_confirmed_at: "2026-01-01T00:00:00Z",
        },
      },
      consumedLink: true,
    });

    render(<ConfirmEmailPage />);
    expect(screen.getByText(/Confirming your email/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /Email confirmed/i }),
      ).toBeInTheDocument();
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Continue to dashboard/i }),
    );
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("shows check-email when visiting without a confirmation link", async () => {
    render(<ConfirmEmailPage />);

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /Check your email/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText(/confirm your account before you sign in/i),
    ).toBeInTheDocument();
  });

  it("maps an expired confirmation link instead of dumping the raw error", async () => {
    mockConsume.mockRejectedValue(new Error("otp_expired"));
    render(<ConfirmEmailPage />);

    await waitFor(() => {
      expect(
        screen.getByText(
          /This confirmation link is invalid or has expired\. Request a new one from sign-in\./,
        ),
      ).toBeInTheDocument();
    });
  });

  it("links back to home and shows the tax disclaimer", async () => {
    render(<ConfirmEmailPage />);
    await waitFor(() => {
      expect(
        screen.getByRole("link", { name: /OptionsTaxHub/i }),
      ).toHaveAttribute("href", "/");
    });
    expect(
      screen.getByText(/For educational and simulation purposes only/),
    ).toBeInTheDocument();
  });
});
