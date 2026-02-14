import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SignupPage from "../../app/auth/signup/page";
import { useRouter } from "next/navigation";
import { useAuth } from "../../app/context/auth";

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: jest.fn(),
}));

const mockPush = jest.fn();
const mockSignUp = jest.fn();

// Helper to fill out the signup form with default valid values
function fillForm(
  container: HTMLElement,
  overrides: Partial<{
    firstName: string;
    lastName: string;
    email: string;
    password: string;
    confirmPassword: string;
  }> = {},
) {
  const values = {
    firstName: "John",
    lastName: "Doe",
    email: "john@example.com",
    password: "password123",
    confirmPassword: "password123",
    ...overrides,
  };

  fireEvent.change(screen.getByLabelText(/First Name/i), {
    target: { value: values.firstName },
  });
  fireEvent.change(screen.getByLabelText(/Last Name/i), {
    target: { value: values.lastName },
  });

  // Use type="email" selector because getByLabelText(/Email/i) matches both
  // the MUI Select (provider type showing "Email") AND the email TextField
  const emailInput = container.querySelector(
    'input[type="email"]',
  ) as HTMLInputElement;
  fireEvent.change(emailInput, {
    target: { value: values.email },
  });

  const passwordInputs = container.querySelectorAll('input[type="password"]');
  fireEvent.change(passwordInputs[0], {
    target: { value: values.password },
  });
  fireEvent.change(passwordInputs[1], {
    target: { value: values.confirmPassword },
  });
}

describe("Sign Up Page", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    globalThis.alert = jest.fn();
    (useRouter as jest.Mock).mockReturnValue({
      push: mockPush,
    });
    mockSignUp.mockResolvedValue(undefined);
    (useAuth as jest.Mock).mockReturnValue({
      signUp: mockSignUp,
      user: null,
      isLoading: false,
    });
  });

  it("renders create account heading", () => {
    render(<SignupPage />);
    expect(
      screen.getByRole("heading", { name: /Create Account/i }),
    ).toBeInTheDocument();
  });

  it("displays welcome message", () => {
    render(<SignupPage />);
    expect(screen.getByText(/Join OptionsTaxHub/i)).toBeInTheDocument();
  });

  it("renders all form fields", () => {
    const { container } = render(<SignupPage />);
    expect(screen.getByLabelText(/First Name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Last Name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Display Name/i)).toBeInTheDocument();
    // Email label collides with MUI Provider Type select showing "Email",
    // so use type="email" selector instead
    expect(container.querySelector('input[type="email"]')).toBeInTheDocument();
  });

  it("displays create account button", () => {
    render(<SignupPage />);
    expect(
      screen.getByRole("button", { name: /Create Account/i }),
    ).toBeInTheDocument();
  });

  it("displays sign in link", () => {
    render(<SignupPage />);
    const signInLink = screen.getByRole("link", { name: /Sign in/i });
    expect(signInLink).toBeInTheDocument();
    expect(signInLink).toHaveAttribute("href", "/auth/signin");
  });

  it("toggles password visibility", () => {
    const { container } = render(<SignupPage />);
    const passwordInputs = container.querySelectorAll('input[type="password"]');
    expect(passwordInputs.length).toBe(2);

    const toggleButtons = screen.getAllByLabelText(/Show password/i);
    fireEvent.click(toggleButtons[0]);

    // First password should now be text
    expect(passwordInputs[0].getAttribute("type")).toBe("text");
  });

  it("submits form successfully and redirects", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container);

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(mockSignUp).toHaveBeenCalledWith(
        "john@example.com",
        "password123",
        {
          firstName: "John",
          lastName: "Doe",
          displayName: "John Doe",
          phone: "",
          providerType: "email",
        },
      );
    });

    await waitFor(() => {
      expect(globalThis.alert).toHaveBeenCalled();
      expect(mockPush).toHaveBeenCalledWith("/auth/signin");
    });
  });

  it("shows error when passwords do not match", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container, { confirmPassword: "different" });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when password is too short", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container, { password: "12345", confirmPassword: "12345" });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(
        screen.getByText("Password must be at least 6 characters"),
      ).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when phone signup without phone number", async () => {
    const { container } = render(<SignupPage />);

    // Change provider type to phone via the select
    const providerSelect = screen.getByLabelText(/Provider Type/i);
    fireEvent.mouseDown(providerSelect);
    const phoneOption = await screen.findByRole("option", { name: "Phone" });
    fireEvent.click(phoneOption);

    fillForm(container);

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(
        screen.getByText("Phone number is required for phone sign-up"),
      ).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when email signup without email", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container, { email: "" });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(
        screen.getByText("Email is required for email sign-up"),
      ).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("displays error message on sign up failure with Error object", async () => {
    mockSignUp.mockRejectedValue(new Error("Email already registered"));

    const { container } = render(<SignupPage />);
    fillForm(container);

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText("Email already registered")).toBeInTheDocument();
    });

    expect(mockPush).not.toHaveBeenCalled();
  });

  it("displays generic error message on non-Error failure", async () => {
    mockSignUp.mockRejectedValue("unexpected");

    const { container } = render(<SignupPage />);
    fillForm(container);

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(
        screen.getByText("Failed to sign up. Please try again."),
      ).toBeInTheDocument();
    });
  });

  it("shows loading state during submission", async () => {
    let resolveSignUp: () => void;
    mockSignUp.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveSignUp = resolve;
        }),
    );

    const { container } = render(<SignupPage />);
    fillForm(container);

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    });

    resolveSignUp!();

    await waitFor(() => {
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });
  });

  it("uses custom display name when provided", async () => {
    const { container } = render(<SignupPage />);

    fillForm(container);
    fireEvent.change(screen.getByLabelText(/Display Name/i), {
      target: { value: "CustomName" },
    });

    const form = container.querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(mockSignUp).toHaveBeenCalledWith(
        expect.any(String),
        expect.any(String),
        expect.objectContaining({
          displayName: "CustomName",
        }),
      );
    });
  });

  it("toggles confirm password visibility", () => {
    const { container } = render(<SignupPage />);

    // Both password fields start as type="password"
    const passwordInputs = container.querySelectorAll('input[type="password"]');
    expect(passwordInputs.length).toBe(2);

    // The second toggle button controls confirm password
    const toggleButtons = screen.getAllByLabelText(/Show password/i);
    expect(toggleButtons.length).toBe(2);

    fireEvent.click(toggleButtons[1]);

    // Confirm password should now be type="text"
    expect(passwordInputs[1].getAttribute("type")).toBe("text");
  });

  it("shows phone-specific helperText when provider type is phone", async () => {
    const { container } = render(<SignupPage />);

    // Default provider type is "email" — phone field shows "Optional"
    expect(screen.getByText("Optional")).toBeInTheDocument();

    // Switch provider type to phone
    const providerSelect = screen.getByLabelText(/Provider Type/i);
    fireEvent.mouseDown(providerSelect);
    const phoneOption = await screen.findByRole("option", { name: "Phone" });
    fireEvent.click(phoneOption);

    // Phone field helperText should now show the required message
    expect(screen.getByText("Required for phone sign-up")).toBeInTheDocument();

    // Type into the phone field to exercise the onChange handler
    const phoneInput = container.querySelector(
      'input[type="tel"]',
    ) as HTMLInputElement;
    fireEvent.change(phoneInput, { target: { value: "+15551234567" } });
    expect(phoneInput.value).toBe("+15551234567");
  });
});
