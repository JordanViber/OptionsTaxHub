import { render, screen, fireEvent } from "@testing-library/react";

const mockPush = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

import TipCancelPage from "../../app/tips/cancel/page";

describe("TipCancelPage", () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  it("renders No Worries heading", () => {
    render(<TipCancelPage />);

    expect(screen.getByText("No Worries!")).toBeInTheDocument();
  });

  it("renders reassuring message", () => {
    render(<TipCancelPage />);

    expect(
      screen.getByText(/Tips are totally optional/),
    ).toBeInTheDocument();
  });

  it("renders Back to Dashboard button", () => {
    render(<TipCancelPage />);

    expect(
      screen.getByRole("button", { name: /Back to Dashboard/ }),
    ).toBeInTheDocument();
  });

  it("navigates to dashboard when button is clicked", () => {
    render(<TipCancelPage />);

    fireEvent.click(
      screen.getByRole("button", { name: /Back to Dashboard/ }),
    );

    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("displays wave emoji", () => {
    render(<TipCancelPage />);

    expect(screen.getByText("👋")).toBeInTheDocument();
  });
});
