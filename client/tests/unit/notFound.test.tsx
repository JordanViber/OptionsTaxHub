import { render as rtlRender, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NotFound from "../../app/not-found";

jest.mock("next/link", () => {
  return ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  );
});

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/missing-page",
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => ({ user: null, loading: false, signOut: jest.fn() }),
}));

function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

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
    expect(screen.getByTestId("not-found-sign-in")).toHaveAttribute(
      "href",
      "/auth/signin?next=/dashboard",
    );
    expect(screen.getByTestId("header-sign-in")).toHaveAttribute(
      "href",
      "/auth/signin?next=/dashboard",
    );
  });
});
