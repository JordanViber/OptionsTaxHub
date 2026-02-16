import { render, screen } from "@testing-library/react";
import TaxDisclaimer from "../../app/components/TaxDisclaimer";

describe("TaxDisclaimer", () => {
  it("renders an alert component", () => {
    render(<TaxDisclaimer />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("displays the educational purposes disclaimer text", () => {
    render(<TaxDisclaimer />);

    expect(
      screen.getByText(
        /For educational and simulation purposes only/,
      ),
    ).toBeInTheDocument();
  });

  it("mentions consulting a tax professional", () => {
    render(<TaxDisclaimer />);

    expect(
      screen.getByText(/Consult a qualified tax professional/),
    ).toBeInTheDocument();
  });
});
