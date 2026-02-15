import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import TipJar from "../../app/components/TipJar";

// Mock fetch
const mockFetch = jest.fn();
globalThis.fetch = mockFetch;

describe("TipJar", () => {
  const onClose = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockFetch.mockReset();
  });

  it("does not render when closed", () => {
    render(<TipJar open={false} onClose={onClose} />);

    expect(screen.queryByText("Support OptionsTaxHub")).not.toBeInTheDocument();
  });

  it("renders dialog when open", () => {
    render(<TipJar open={true} onClose={onClose} />);

    expect(screen.getByText("Support OptionsTaxHub")).toBeInTheDocument();
  });

  it("displays all three tip tiers", () => {
    render(<TipJar open={true} onClose={onClose} />);

    expect(screen.getByText("$3")).toBeInTheDocument();
    expect(screen.getByText("$10")).toBeInTheDocument();
    expect(screen.getByText("$25")).toBeInTheDocument();
  });

  it("displays tier descriptions", () => {
    render(<TipJar open={true} onClose={onClose} />);

    expect(screen.getByText("Buy us a coffee")).toBeInTheDocument();
    expect(screen.getByText("Buy us lunch")).toBeInTheDocument();
    expect(screen.getByText("You're amazing!")).toBeInTheDocument();
  });

  it("shows Stripe footer text", () => {
    render(<TipJar open={true} onClose={onClose} />);

    expect(
      screen.getByText("Payments processed securely by Stripe"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/One-time payment/),
    ).toBeInTheDocument();
  });

  it("calls onClose when close button is clicked", () => {
    render(<TipJar open={true} onClose={onClose} />);

    fireEvent.click(screen.getByLabelText("close"));

    expect(onClose).toHaveBeenCalled();
  });

  it("initiates checkout on tier click", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ checkout_url: "https://checkout.stripe.com/test" }),
    });

    render(<TipJar open={true} onClose={onClose} />);

    fireEvent.click(screen.getByText("Buy us a coffee"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/tips/checkout"),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ tier: "coffee" }),
        }),
      );
    });
  });

  it("calls checkout endpoint and processes response on success", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ checkout_url: "https://checkout.stripe.com/session123" }),
    });

    render(<TipJar open={true} onClose={onClose} />);

    fireEvent.click(screen.getByText("Buy us a coffee"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/tips/checkout"),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ tier: "coffee" }),
        }),
      );
    });
  });

  it("shows error message when checkout fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({ detail: "Stripe error occurred" }),
    });

    render(<TipJar open={true} onClose={onClose} />);

    fireEvent.click(screen.getByText("Buy us a coffee"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Stripe error occurred");
    });
  });

  it("shows generic error when fetch throws", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    render(<TipJar open={true} onClose={onClose} />);

    fireEvent.click(screen.getByText("Buy us lunch"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Network error");
    });
  });

  it("shows generic message for non-Error throws", async () => {
    mockFetch.mockRejectedValueOnce("unknown");

    render(<TipJar open={true} onClose={onClose} />);

    fireEvent.click(screen.getByText("Buy us lunch"));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong");
    });
  });
});
