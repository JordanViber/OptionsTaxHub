import { fireEvent, render, screen } from "@testing-library/react";
import AppShell, { accountDisplayName } from "../../app/components/AppShell";

const mockPush = jest.fn();
const mockAuth = {
  user: null as null | {
    email?: string;
    user_metadata?: { display_name?: string };
  },
  loading: false,
  signOut: jest.fn(),
};

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/dashboard",
}));

jest.mock("../../app/context/auth", () => ({
  useAuth: () => mockAuth,
}));

function mockCompact(compact: boolean) {
  window.matchMedia = jest.fn().mockImplementation((query: string) => ({
    matches: compact && String(query).includes("max-width"),
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  }));
}

describe("accountDisplayName", () => {
  it("prefers a profile name, then email, then Account", () => {
    expect(
      accountDisplayName({
        email: "a@b.c",
        user_metadata: { display_name: "Ada" },
      }),
    ).toBe("Ada");
    expect(accountDisplayName({ email: "a@b.c", user_metadata: {} })).toBe(
      "a@b.c",
    );
    expect(accountDisplayName({ user_metadata: {} })).toBe("Account");
  });
});

describe("AppShell", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    mockPush.mockReset();
    mockAuth.user = null;
    mockAuth.signOut.mockReset();
    sessionStorage.clear();
    mockCompact(false);
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("shows the same app actions and an active Tax link", () => {
    render(
      <AppShell>
        <div>Desk</div>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: "Tax" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Options" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByTestId("header-upload")).toBeInTheDocument();
    expect(screen.getByTestId("header-saved-runs")).toBeInTheDocument();
    expect(screen.getByTestId("header-sign-in")).toHaveAttribute(
      "href",
      "/auth/signin?next=/dashboard",
    );
    expect(screen.queryByRole("button", { name: /^Settings$/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("header-menu")).not.toBeInTheDocument();
  });

  it("sends guests from Saved runs to sign-in for this page", () => {
    render(<AppShell />);
    fireEvent.click(screen.getByTestId("header-saved-runs"));
    expect(mockPush).toHaveBeenCalledWith(
      "/auth/signin?next=/dashboard&reason=runs",
    );
  });

  it("opens Tax settings from the account menu", () => {
    mockAuth.user = {
      email: "ada@example.com",
      user_metadata: { display_name: "Ada" },
    };
    render(<AppShell />);
    fireEvent.click(screen.getByRole("button", { name: "Account" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Tax settings" }));
    expect(mockPush).toHaveBeenCalledWith("/settings");
    expect(screen.queryByRole("button", { name: /^Settings$/ })).not.toBeInTheDocument();
  });

  it("labels the 390px menu and marks the active page", () => {
    mockCompact(true);
    render(<AppShell />);

    expect(screen.getByRole("link", { name: "Tax" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Options" })).toBeVisible();
    expect(screen.queryByTestId("header-upload")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(screen.getByRole("menuitem", { name: "Tax (current)" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("menuitem", { name: "Options" })).toBeInTheDocument();
    expect(screen.getByTestId("header-upload")).toBeVisible();
    expect(screen.getByTestId("header-saved-runs")).toBeVisible();
    expect(screen.getByTestId("header-sign-in")).toBeVisible();
  });

  it("uses the tax page upload handler instead of leaving the desk", () => {
    const onUpload = jest.fn();
    render(<AppShell onUpload={onUpload} />);
    fireEvent.click(screen.getByTestId("header-upload"));
    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("oth-upload-intent")).toBeNull();
    expect(mockPush).not.toHaveBeenCalled();
  });
});
