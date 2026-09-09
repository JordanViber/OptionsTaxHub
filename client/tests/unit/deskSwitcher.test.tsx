import { fireEvent, render, screen } from "@testing-library/react";
import DeskSwitcher, {
  rememberDesk,
  readLastDesk,
  lastDeskHref,
  LAST_DESK_KEY,
} from "../../app/components/DeskSwitcher";

jest.mock("next/link", () => {
  return ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    [key: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  );
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

  it("marks the tax desk link current on /dashboard", () => {
    mockPathname = "/dashboard";
    render(<DeskSwitcher />);
    expect(screen.getByRole("link", { name: /Tax desk/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("link", { name: /Options desk/i }),
    ).not.toHaveAttribute("aria-current");
  });

  it("marks the options desk link current on /options", () => {
    mockPathname = "/options";
    render(<DeskSwitcher />);
    expect(screen.getByRole("link", { name: /Options desk/i })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /Tax desk/i })).not.toHaveAttribute(
      "aria-current",
    );
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

  it("clicking a pill remembers that desk", () => {
    render(<DeskSwitcher />);
    fireEvent.click(screen.getByRole("link", { name: /Options desk/i }));
    expect(localStorage.getItem(LAST_DESK_KEY)).toBe("options");
    expect(readLastDesk()).toBe("options");
    fireEvent.click(screen.getByRole("link", { name: /Tax desk/i }));
    expect(readLastDesk()).toBe("tax");
  });
});

describe("rememberDesk / readLastDesk", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to tax when nothing stored", () => {
    expect(readLastDesk()).toBe("tax");
    expect(lastDeskHref()).toBe("/dashboard");
  });

  it("stores and reads back 'options'", () => {
    rememberDesk("options");
    expect(localStorage.getItem(LAST_DESK_KEY)).toBe("options");
    expect(readLastDesk()).toBe("options");
    expect(lastDeskHref()).toBe("/options");
  });

  it("stores and reads back 'tax'", () => {
    rememberDesk("tax");
    expect(readLastDesk()).toBe("tax");
    expect(lastDeskHref()).toBe("/dashboard");
  });

  it("returns 'tax' on localStorage read failure", () => {
    const original = Storage.prototype.getItem;
    try {
      Storage.prototype.getItem = () => {
        throw new Error("quota exceeded");
      };
      expect(readLastDesk()).toBe("tax");
    } finally {
      Storage.prototype.getItem = original;
    }
  });

  it("silently ignores localStorage write failure", () => {
    const original = Storage.prototype.setItem;
    try {
      Storage.prototype.setItem = () => {
        throw new Error("quota exceeded");
      };
      expect(() => rememberDesk("options")).not.toThrow();
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});
