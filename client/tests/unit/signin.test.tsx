import { render, screen } from "@testing-library/react";
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

describe("Sign In Page", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (useRouter as jest.Mock).mockReturnValue({
      push: mockPush,
    });
    (useAuth as jest.Mock).mockReturnValue({
      signIn: jest.fn().mockResolvedValue(undefined),
      user: null,
      isLoading: false,
    });
  });

  it("renders sign in page", () => {
    render(<SigninPage />);

    // Check for Sign In heading using getByRole
    expect(screen.getByRole("heading", { name: /Sign In/i })).toBeInTheDocument();
  });

  it("displays welcome message", () => {
    render(<SigninPage />);

    expect(screen.getByText(/Welcome back/i)).toBeInTheDocument();
  });

  it("displays sign in button", () => {
    render(<SigninPage />);

    const signInButton = screen.getByRole("button", { name: /Sign In/i });
    expect(signInButton).toBeInTheDocument();
  });

  it("displays sign up link", () => {
    render(<SigninPage />);

    const signUpLink = screen.getByRole("link", { name: /Sign up/i });
    expect(signUpLink).toBeInTheDocument();
    expect(signUpLink).toHaveAttribute("href", "/auth/signup");
  });

  it("uses router from next/navigation", () => {
    render(<SigninPage />);

    expect(useRouter).toHaveBeenCalled();
  });

  it("uses auth context", () => {
    render(<SigninPage />);

    expect(useAuth).toHaveBeenCalled();
  });

  it("renders email and password input fields", () => {
    render(<SigninPage />);

    const inputs = screen.getAllByRole("textbox");
    expect(inputs.length).toBeGreaterThan(0);
  });

  it("has password input field", () => {
    render(<SigninPage />);

    const passwordInputs = screen.queryAllByDisplayValue("");
    const passwordInput = passwordInputs.find(
      (el) => (el as HTMLInputElement).type === "password",
    ) as HTMLInputElement | undefined;
    expect(passwordInput).toBeDefined();
    expect(passwordInput?.type).toBe("password");
  });

  it("has password visibility toggle button", () => {
    render(<SigninPage />);

    const showPasswordButtons = screen.getAllByLabelText(/show password|hide password/i);
    expect(showPasswordButtons.length).toBeGreaterThan(0);
  });
});
