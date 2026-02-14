import { render, screen } from "@testing-library/react";
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

describe("Sign Up Page", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (useRouter as jest.Mock).mockReturnValue({
      push: mockPush,
    });
    (useAuth as jest.Mock).mockReturnValue({
      signUp: jest.fn().mockResolvedValue(undefined),
      user: null,
      isLoading: false,
    });
  });

  it("renders sign up page", () => {
    render(<SignupPage />);

    // Check for Create Account heading using getByRole
    expect(screen.getByRole("heading", { name: /Create Account/i })).toBeInTheDocument();
  });

  it("displays welcome message", () => {
    render(<SignupPage />);

    expect(screen.getByText(/Join OptionsTaxHub/i)).toBeInTheDocument();
  });

  it("displays sign up button", () => {
    render(<SignupPage />);

    const signUpButton = screen.getByRole("button", { name: /Create Account/i });
    expect(signUpButton).toBeInTheDocument();
  });

  it("displays sign in link", () => {
    render(<SignupPage />);

    const signInLink = screen.getByRole("link", { name: /Sign in/i });
    expect(signInLink).toBeInTheDocument();
    expect(signInLink).toHaveAttribute("href", "/auth/signin");
  });

  it("uses router from next/navigation", () => {
    render(<SignupPage />);

    expect(useRouter).toHaveBeenCalled();
  });

  it("uses auth context", () => {
    render(<SignupPage />);

    expect(useAuth).toHaveBeenCalled();
  });
});
