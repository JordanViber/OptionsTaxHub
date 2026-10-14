import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AppShell, { accountDisplayName } from "../../app/components/AppShell";

const mockPush = jest.fn();
const mockAuth = {
  user: null as null | {
    id?: string;
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

function renderShell(ui: React.ReactElement, client = new QueryClient()) {
  return {
    client,
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
  };
}

describe("AppShell", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    mockPush.mockReset();
    mockAuth.user = null;
    mockAuth.signOut.mockReset();
    mockAuth.signOut.mockResolvedValue(undefined);
    sessionStorage.clear();
    mockCompact(false);
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("shows the same app actions and an active Tax link", () => {
    renderShell(
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
    renderShell(<AppShell />);
    fireEvent.click(screen.getByTestId("header-saved-runs"));
    expect(mockPush).toHaveBeenCalledWith(
      "/auth/signin?next=/dashboard&reason=runs",
    );
  });

  it("opens Settings from the account menu", () => {
    mockAuth.user = {
      email: "ada@example.com",
      user_metadata: { display_name: "Ada" },
    };
    renderShell(<AppShell />);
    fireEvent.click(screen.getByTestId("header-account"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
    expect(mockPush).toHaveBeenCalledWith("/settings");
    expect(screen.queryByRole("button", { name: /^Settings$/ })).not.toBeInTheDocument();
  });

  it("labels the 390px menu and marks the active page", () => {
    mockCompact(true);
    renderShell(<AppShell />);

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
    renderShell(<AppShell onUpload={onUpload} />);
    fireEvent.click(screen.getByTestId("header-upload"));
    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("oth-upload-intent")).toBeNull();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("clears the saved analysis and query cache on shared sign-out", async () => {
    mockAuth.user = {
      id: "user-1",
      email: "ada@example.com",
      user_metadata: { display_name: "Ada" },
    };
    sessionStorage.setItem("optionstaxhub-analysis", "{\"sample\":true}");
    const client = new QueryClient();
    client.setQueryData(["portfolio-history", "user-1"], [{ id: "h1" }]);
    client.setQueryData(["tax-profile", "user-1"], { tax_year: 2026 });

    const deleteCache = jest.fn(async () => true);
    const cacheKeys = jest.fn(async () => [
      "optionstaxhub-v6",
      "optionstaxhub-v5",
    ]);
    Object.defineProperty(globalThis, "caches", {
      configurable: true,
      value: { keys: cacheKeys, delete: deleteCache },
    });

    renderShell(<AppShell />, client);
    expect(screen.getByTestId("header-account")).toHaveAttribute(
      "aria-label",
      "Ada",
    );
    fireEvent.click(screen.getByTestId("header-account"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign Out" }));

    await waitFor(() => expect(mockAuth.signOut).toHaveBeenCalledTimes(1));
    expect(deleteCache).toHaveBeenCalledWith("optionstaxhub-v6");
    expect(deleteCache).toHaveBeenCalledWith("optionstaxhub-v5");
    expect(sessionStorage.getItem("optionstaxhub-analysis")).toBeNull();
    expect(client.getQueryData(["portfolio-history", "user-1"])).toBeUndefined();
    expect(client.getQueryData(["tax-profile", "user-1"])).toBeUndefined();
    expect(mockPush).toHaveBeenCalledWith("/auth/signin");
  });
});
