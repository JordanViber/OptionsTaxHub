import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SigninPage from "../../app/auth/signin/page";
import { useRouter } from "next/navigation";
import { useAuth } from "../../app/context/auth";

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: jest.fn(),
}));

const mockPush = jest.fn();
const mockSignIn = jest.fn();

describe("Sign In Page", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (useRouter as jest.Mock).mockReturnValue({
      push: mockPush,
    });
    mockSignIn.mockResolvedValue(undefined);
    (useAuth as jest.Mock).mockReturnValue({
      signIn: mockSignIn,
      user: null,
      isLoading: false,
    });
  });

  it("renders sign in heading", () => {
    render(<SigninPage />);
    expect(
      screen.getByRole("heading", { name: /Sign In/i }),
    ).toBeInTheDocument();
  });

  it("displays welcome message", () => {
    render(<SigninPage />);
    expect(screen.getByText(/Welcome back/i)).toBeInTheDocument();
  });

  it("renders email input field", () => {
    render(<SigninPage />);
    expect(screen.getByLabelText(/Email/i)).toBeInTheDocument();
  });

  it("renders password input field", () => {
    const { container } = render(<SigninPage />);
    const passwordInput = container.querySelector('input[type="password"]');
    expect(passwordInput).toBeInTheDocument();
  });

  it("displays sign in button", () => {
    render(<SigninPage />);
    expect(
      screen.getByRole("button", { name: /Sign In/i }),
    ).toBeInTheDocument();
  });

  it("displays sign up link", () => {
    render(<SigninPage />);
    const signUpLink = screen.getByRole("link", { name: /Sign up/i });
    expect(signUpLink).toBeInTheDocument();
    expect(signUpLink).toHaveAttribute("href", "/auth/signup");
  });

  it("toggles password visibility", () => {
    const { container } = render(<SigninPage />);

    const passwordInput = container.querySelector(
      'input[type="password"]',
    ) as HTMLInputElement;
    expect(passwordInput.type).toBe("password");

    const toggleButton = screen.getByLabelText(/Show password/i);
    fireEvent.click(toggleButton);

    expect(passwordInput.type).toBe("text");

    const hideButton = screen.getByLabelText(/Hide password/i);
    fireEvent.click(hideButton);

    expect(passwordInput.type).toBe("password");
  });

  it("submits form and redirects on success", async () => {
    const { container } = render(<SigninPage />);

    const emailInput = screen.getByLabelText(/Email/i);
    const passwordInput = container.querySelector(
      'input[type="password"]',
    ) as HTMLInputElement;

    fireEvent.change(emailInput, { target: { value: "test@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "password123" } });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(mockSignIn).toHaveBeenCalledWith(
        "test@example.com",
        "password123",
      );
    });

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/");
    });
  });

  it("displays error message on sign in failure with Error object", async () => {
    mockSignIn.mockRejectedValue(new Error("Invalid credentials"));

    const { container } = render(<SigninPage />);

    const emailInput = screen.getByLabelText(/Email/i);
    const passwordInput = container.querySelector(
      'input[type="password"]',
    ) as HTMLInputElement;

    fireEvent.change(emailInput, { target: { value: "bad@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "wrong" } });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText("Invalid credentials")).toBeInTheDocument();
    });

    // Should NOT redirect
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("displays generic error message on non-Error failure", async () => {
    mockSignIn.mockRejectedValue("some string error");

    const { container } = render(<SigninPage />);

    const emailInput = screen.getByLabelText(/Email/i);
    const passwordInput = container.querySelector(
      'input[type="password"]',
    ) as HTMLInputElement;

    fireEvent.change(emailInput, { target: { value: "bad@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "wrong" } });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to sign in. Please try again."),
      ).toBeInTheDocument();
    });
  });

  it("shows loading state during submission", async () => {
    // Make signIn hang so we can check loading state
    let resolveSignIn: () => void;
    mockSignIn.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveSignIn = resolve;
        }),
    );

    const { container } = render(<SigninPage />);

    const emailInput = screen.getByLabelText(/Email/i);
    const passwordInput = container.querySelector(
      'input[type="password"]',
    ) as HTMLInputElement;

    fireEvent.change(emailInput, { target: { value: "test@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "password123" } });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    // During loading, the button text should change to a spinner
    await waitFor(() => {
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    });

    // Resolve the pending promise to clean up
    resolveSignIn!();

    await waitFor(() => {
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });
  });
});
