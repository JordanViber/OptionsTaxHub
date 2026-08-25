import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SignupPage from "../../app/auth/signup/page";
import { useRouter } from "next/navigation";
import { useAuth } from "../../app/context/auth";

jest.mock("next/navigation", () => ({
  useRouter: jest.fn(),
}));

jest.mock("next/link", () => {
  return ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>;
});

jest.mock("../../app/context/auth", () => ({
  useAuth: jest.fn(),
}));

const mockPush = jest.fn();
const mockSignUp = jest.fn();

const getInput = (container: HTMLElement, selector: string) => {
  const element = container.querySelector(selector);
  if (!(element instanceof HTMLInputElement)) {
    throw new TypeError(`Expected input for selector: ${selector}`);
  }
  return element;
};

const getInputs = (container: HTMLElement, selector: string) => {
  const elements = Array.from(container.querySelectorAll(selector));
  if (elements.length === 0) {
    throw new TypeError(
      `Expected at least one input for selector: ${selector}`,
    );
  }
  const inputs = elements.filter(
    (element): element is HTMLInputElement =>
      element instanceof HTMLInputElement,
  );
  if (inputs.length !== elements.length) {
    throw new TypeError(`Expected only inputs for selector: ${selector}`);
  }
  return inputs;
};

const getForm = (container: HTMLElement) => {
  const element = container.querySelector("form");
  if (!(element instanceof HTMLFormElement)) {
    throw new TypeError("Expected form element");
  }
  return element;
};

function fillForm(
  container: HTMLElement,
  overrides: Partial<{
    name: string;
    email: string;
    password: string;
    confirmPassword: string;
  }> = {},
) {
  const values = {
    name: "John Doe",
    email: "john@example.com",
    password: "password123", // NOSONAR typescript:S2068
    confirmPassword: "password123", // NOSONAR typescript:S2068
    ...overrides,
  };

  fireEvent.change(screen.getByLabelText(/^Name/i), {
    target: { value: values.name },
  });

  const emailInput = getInput(container, 'input[type="email"]');
  fireEvent.change(emailInput, {
    target: { value: values.email },
  });

  const passwordInputs = getInputs(container, 'input[type="password"]');
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

  it("renders email, password, and optional name fields", () => {
    const { container } = render(<SignupPage />);
    expect(screen.getByLabelText(/^Name/i)).toBeInTheDocument();
    expect(container.querySelector('input[type="email"]')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Provider Type/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Phone/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/First Name/i)).not.toBeInTheDocument();
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

  it("links back to home", () => {
    render(<SignupPage />);
    const homeLink = screen.getByRole("link", { name: /OptionsTaxHub/i });
    expect(homeLink).toHaveAttribute("href", "/");
  });

  it("shows the tax disclaimer", () => {
    render(<SignupPage />);
    expect(
      screen.getByText(/For educational and simulation purposes only/),
    ).toBeInTheDocument();
  });

  it("toggles password visibility", () => {
    const { container } = render(<SignupPage />);
    const passwordInputs = container.querySelectorAll('input[type="password"]');
    expect(passwordInputs.length).toBe(2);

    const toggleButtons = screen.getAllByLabelText(/Show password/i);
    fireEvent.click(toggleButtons[0]);

    expect(passwordInputs[0].getAttribute("type")).toBe("text");
  });

  it("shows a check-email page after signup instead of redirecting to sign-in", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container);

    const form = getForm(container);
    fireEvent.submit(form);

    await waitFor(() => {
      expect(mockSignUp).toHaveBeenCalledWith(
        "john@example.com",
        "password123", // NOSONAR typescript:S2068
        { name: "John Doe" },
      );
    });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /Check your email/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText(/We sent a confirmation link to john@example.com/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Resend confirmation email/i }),
    ).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: /Create Account/i }),
    ).not.toBeInTheDocument();
  });

  it("resends the signup confirmation email from the check-email page", async () => {
    const { resendSignupConfirmation } = jest.requireMock("@/lib/supabase") as {
      resendSignupConfirmation: jest.Mock;
    };
    resendSignupConfirmation.mockResolvedValue(undefined);

    const { container } = render(<SignupPage />);
    fillForm(container);
    fireEvent.submit(getForm(container));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Resend confirmation email/i }),
      ).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", { name: /Resend confirmation email/i }),
    );

    await waitFor(() => {
      expect(resendSignupConfirmation).toHaveBeenCalledWith("john@example.com");
    });
    expect(
      screen.getByText(/Another confirmation email is on the way/),
    ).toBeInTheDocument();
  });

  it("shows error when passwords do not match", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container, { confirmPassword: "different" }); // NOSONAR typescript:S2068 — test value, not a real credential

    const form = getForm(container);
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when password is too short", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container, { password: "12345", confirmPassword: "12345" }); // NOSONAR typescript:S2068

    const form = getForm(container);
    fireEvent.submit(form);

    await waitFor(() => {
      expect(
        screen.getByText("Password must be at least 6 characters"),
      ).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("shows error when email is missing", async () => {
    const { container } = render(<SignupPage />);
    fillForm(container, { email: "" });

    const form = getForm(container);
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText("Email is required")).toBeInTheDocument();
    });

    expect(mockSignUp).not.toHaveBeenCalled();
  });

  it("displays error message on sign up failure with Error object", async () => {
    mockSignUp.mockRejectedValue(new Error("Email already registered"));

    const { container } = render(<SignupPage />);
    fillForm(container);

    const form = getForm(container);
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

    const form = getForm(container);
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

    const form = getForm(container);
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
    });

    if (!resolveSignUp) {
      throw new Error("resolveSignUp was not set");
    }
    resolveSignUp();

    await waitFor(() => {
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });
  });

  it("toggles confirm password visibility", () => {
    const { container } = render(<SignupPage />);

    const passwordInputs = getInputs(container, 'input[type="password"]');
    expect(passwordInputs.length).toBe(2);

    const toggleButtons = screen.getAllByLabelText(/Show password/i);
    expect(toggleButtons.length).toBe(2);

    fireEvent.click(toggleButtons[1]);

    expect(passwordInputs[1].getAttribute("type")).toBe("text");
  });
});
