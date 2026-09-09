import { render, screen, act } from "@testing-library/react";
import DeskSwitcher, {
  rememberDesk,
  readLastDesk,
  LAST_DESK_KEY,
} from "../../app/components/DeskSwitcher";

jest.mock("next/link", () => {
  return ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>;
});

let mockPathname = "/dashboard";
jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

describe("DeskSwitcher", () => {
  beforeEach(() => {
    mockPathname = "/dashboard";
    localStorage.clear();
  });

  it("renders with data-testid desk-switcher", () => {
    render(<DeskSwitcher />);
    expect(screen.getByTestId("desk-switcher")).toBeInTheDocument();
  });

  it("marks tax desk active when on /dashboard", () => {
    mockPathname = "/dashboard";
    render(<DeskSwitcher />);
    expect(screen.getByTestId("desk-switch-tax")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByTestId("desk-switch-options"),
    ).not.toHaveAttribute("aria-current");
  });

  it("marks options desk active when on /options", () => {
    mockPathname = "/options";
    render(<DeskSwitcher />);
    expect(screen.getByTestId("desk-switch-options")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByTestId("desk-switch-tax"),
    ).not.toHaveAttribute("aria-current");
  });

  it("tax desk link points to /dashboard", () => {
    render(<DeskSwitcher />);
    expect(screen.getByRole("link", { name: /Tax desk/i })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("options desk link points to /options", () => {
    render(<DeskSwitcher />);
    expect(screen.getByRole("link", { name: /Options desk/i })).toHaveAttribute(
      "href",
      "/options",
    );
  });
});

describe("rememberDesk / readLastDesk", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to tax when nothing stored", () => {
    expect(readLastDesk()).toBe("tax");
  });

  it("stores and reads back 'options'", () => {
    rememberDesk("options");
    expect(localStorage.getItem(LAST_DESK_KEY)).toBe("options");
    expect(readLastDesk()).toBe("options");
  });

  it("stores and reads back 'tax'", () => {
    rememberDesk("tax");
    expect(readLastDesk()).toBe("tax");
  });

  it("returns 'tax' on localStorage read failure", () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new Error("quota exceeded");
    };
    expect(readLastDesk()).toBe("tax");
    Storage.prototype.getItem = original;
  });

  it("silently ignores localStorage write failure", () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error("quota exceeded");
    };
    expect(() => rememberDesk("options")).not.toThrow();
    Storage.prototype.setItem = original;
  });
});
