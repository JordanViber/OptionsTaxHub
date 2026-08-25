import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ResetPasswordPage from "../../app/auth/reset-password/page";
import { useRouter } from "next/navigation";

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
}));

const mockReplace = jest.fn();
const mockEstablishRecoverySession = jest.fn();
const mockUpdatePassword = jest.fn();
const mockSignOut = jest.fn();

jest.mock("@/lib/supabase", () => ({
  establishRecoverySession: (...args: unknown[]) =>
    mockEstablishRecoverySession(...args),
  updatePassword: (...args: unknown[]) => mockUpdatePassword(...args),
  signOut: (...args: unknown[]) => mockSignOut(...args),
}));

const getForm = (container: HTMLElement) => {
  const element = container.querySelector("form");
  if (!(element instanceof HTMLFormElement)) {
    throw new TypeError("Expected form element");
  }
  return element;
};

describe("Reset Password Page", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (useRouter as jest.Mock).mockReturnValue({
      replace: mockReplace,
      push: jest.fn(),
    });
    mockEstablishRecoverySession.mockResolvedValue(undefined);
    mockUpdatePassword.mockResolvedValue(undefined);
    mockSignOut.mockResolvedValue(undefined);
  });

  it("shows the form after a recovery session is established", async () => {
    render(<ResetPasswordPage />);

    expect(screen.getByText(/Checking reset link/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /Set a new password/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Confirm password/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Update password/i }),
    ).toBeInTheDocument();
  });

  it("links back to home and shows the tax disclaimer", async () => {
    render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /OptionsTaxHub/i })).toHaveAttribute(
        "href",
        "/",
      );
    });
    expect(
      screen.getByText(/For educational and simulation purposes only/),
    ).toBeInTheDocument();
  });

  it("maps an expired recovery link instead of dumping the raw error", async () => {
    mockEstablishRecoverySession.mockRejectedValue(new Error("otp_expired"));
    render(<ResetPasswordPage />);

    await waitFor(() => {
      expect(
        screen.getByText(
          /This reset link is invalid or has expired\. Request a new one from the sign-in page\./,
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText("otp_expired")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Update password/i }),
    ).not.toBeInTheDocument();
  });

  it("rejects mismatched passwords without calling updateUser", async () => {
    const { container } = render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/^New password/i), {
      target: { value: "abcdef" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/i), {
      target: { value: "uvwxyz" },
    });
    fireEvent.submit(getForm(container));

    expect(await screen.findByText("Passwords do not match")).toBeInTheDocument();
    expect(mockUpdatePassword).not.toHaveBeenCalled();
  });

  it("rejects short passwords without calling updateUser", async () => {
    const { container } = render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/^New password/i), {
      target: { value: "123" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/i), {
      target: { value: "123" },
    });
    fireEvent.submit(getForm(container));

    expect(
      await screen.findByText("Password must be at least 6 characters"),
    ).toBeInTheDocument();
    expect(mockUpdatePassword).not.toHaveBeenCalled();
  });

  it("updates the password and sends the user to sign-in", async () => {
    const { container } = render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/^New password/i), {
      target: { value: "new-pass-123" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/i), {
      target: { value: "new-pass-123" },
    });
    fireEvent.submit(getForm(container));

    await waitFor(() => {
      expect(mockUpdatePassword).toHaveBeenCalledWith("new-pass-123");
    });
    expect(mockSignOut).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith("/auth/signin?reset=success");
  });

  it("maps updateUser failures to a friendly message", async () => {
    mockUpdatePassword.mockRejectedValue(
      new Error('{"message":"Auth session missing!","status":403}'),
    );
    const { container } = render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/^New password/i), {
      target: { value: "new-pass-123" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/i), {
      target: { value: "new-pass-123" },
    });
    fireEvent.submit(getForm(container));

    await waitFor(() => {
      expect(
        screen.getByText(
          /This reset link is invalid or has expired\. Request a new one from the sign-in page\./,
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/Auth session missing/i)).not.toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("maps a weak-password API error", async () => {
    mockUpdatePassword.mockRejectedValue(
      new Error("Password is known to be weak and pwned"),
    );
    const { container } = render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/^New password/i), {
      target: { value: "password" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/i), {
      target: { value: "password" },
    });
    fireEvent.submit(getForm(container));

    await waitFor(() => {
      expect(
        screen.getByText(
          "That password is too easy to guess. Choose a stronger password.",
        ),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/pwned/i)).not.toBeInTheDocument();
  });

  it("still redirects if sign-out after update fails", async () => {
    mockSignOut.mockRejectedValue(new Error("Sign out failed"));
    const { container } = render(<ResetPasswordPage />);
    await waitFor(() => {
      expect(screen.getByLabelText(/^New password/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/^New password/i), {
      target: { value: "new-pass-123" },
    });
    fireEvent.change(screen.getByLabelText(/Confirm password/i), {
      target: { value: "new-pass-123" },
    });
    fireEvent.submit(getForm(container));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/auth/signin?reset=success");
    });
  });
});
