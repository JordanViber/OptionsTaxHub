import { test, expect } from "@playwright/test";
import { MOCK_SESSION, MOCK_USER } from "./fixtures";

test.describe("Confirm email landing", () => {
  test("confirms a PKCE code and shows the confirmed state", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");

    await page.route("**/auth/v1/token*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_SESSION,
          user: {
            ...MOCK_USER,
            email_confirmed_at: "2026-01-01T00:00:00Z",
          },
        }),
      }),
    );
    await page.route("**/auth/v1/user*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_USER,
          email_confirmed_at: "2026-01-01T00:00:00Z",
        }),
      }),
    );

    await page.goto("/auth/confirm-email?code=pkce-confirm-code");

    await expect(
      page.getByRole("heading", { name: "Email confirmed" }),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByRole("button", { name: "Continue to dashboard" }),
    ).toBeVisible();
  });

  test("shows check-email when opened without a confirmation token", async ({
    page,
  }) => {
    await page.goto("/auth/confirm-email");
    await expect(
      page.getByRole("heading", { name: "Check your email" }),
    ).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText(/confirm your account before you sign in/i),
    ).toBeVisible();
  });
});

test.describe("Dashboard blocks unconfirmed sessions", () => {
  test("does not load portfolio for an unconfirmed session", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");

    const unconfirmedUser = {
      ...MOCK_USER,
      email_confirmed_at: null,
    };

    await page.route("**/auth/v1/user*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(unconfirmedUser),
      }),
    );
    await page.route("**/api/portfolio/**", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: "portfolio must not load" }),
      }),
    );

    await page.addInitScript(() => {
      const authData = {
        access_token: "mock-access-token",
        token_type: "bearer",
        expires_in: 3600,
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        refresh_token: "mock-refresh-token",
        user: {
          id: "test-user-123",
          email: "test@optionstaxhub.com",
          email_confirmed_at: null,
          user_metadata: {
            display_name: "Test User",
            first_name: "Test",
            last_name: "User",
          },
          aud: "authenticated",
          role: "authenticated",
          created_at: "2025-01-01T00:00:00Z",
        },
      };
      localStorage.setItem(
        "sb-vgrlucxqncajjdoaoctq-auth-token",
        JSON.stringify(authData),
      );
    });

    await page.goto("/dashboard");

    await expect(page).toHaveURL(/\/auth\/confirm-email/, { timeout: 15000 });
    await expect(page.getByText("Portfolio Analysis")).toHaveCount(0);
    await expect(page.getByText("Portfolio Value")).toHaveCount(0);
  });
});
