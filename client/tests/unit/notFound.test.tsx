import { render, screen } from "@testing-library/react";
import NotFound from "../../app/not-found";

jest.mock("next/link", () => {
  return ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>;
});

describe("NotFound", () => {
  it("renders a styled missing-page with Tax and Options links", () => {
    render(<NotFound />);
    expect(screen.getByTestId("not-found")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tax" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.getByRole("link", { name: "Options" })).toHaveAttribute(
      "href",
      "/options",
    );
  });
});
