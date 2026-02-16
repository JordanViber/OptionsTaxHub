import { test, expect } from "@playwright/test";
import { MOCK_SESSION, MOCK_USER } from "./fixtures";

/**
 * E2E tests for the Sign In page (/auth/signin).
 *
 * Covers: successful sign-in redirect, error display, password toggle,
 * loading state, and navigation to sign-up.
 */

test.describe("Sign In Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/auth/signin");
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible({
      timeout: 10000,
    });
  });

  test("renders the sign-in form with all fields", async ({ page }) => {
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign In" })).toBeVisible();
    await expect(page.getByText("Welcome back to OptionsTaxHub")).toBeVisible();
  });

  test("successful sign-in redirects to dashboard", async ({
    page,
    browserName,
  }) => {
    // WebKit on Windows doesn't process mocked Supabase auth responses correctly
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");

    // Mock Supabase auth to succeed
    await page.route("**/auth/v1/token*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SESSION),
      }),
    );
    await page.route("**/auth/v1/user*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_USER),
      }),
    );
    // Mock backend APIs that dashboard loads
    await page.route("**/api/tax-profile/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(null),
      }),
    );
    await page.route("**/api/portfolio/history/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    await page.getByLabel("Email").fill("test@optionstaxhub.com");
    await page.locator('input[type="password"]').fill("password123");
    await page.getByRole("button", { name: "Sign In" }).click();

    // Should redirect to dashboard
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
    await expect(page.getByText("OptionsTaxHub")).toBeVisible({
      timeout: 10000,
    });
  });

  test("shows error alert on failed sign-in", async ({ page }) => {
    // Mock Supabase auth to fail
    await page.route("**/auth/v1/token*", (route) =>
      route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          error: "invalid_grant",
          error_description: "Invalid login credentials",
        }),
      }),
    );

    await page.getByLabel("Email").fill("wrong@example.com");
    await page.locator('input[type="password"]').fill("badpassword");
    await page.getByRole("button", { name: "Sign In" }).click();

    // Error alert should appear (use .first() — Next.js also renders a route announcer with role=alert)
    await expect(page.getByRole("alert").first()).toBeVisible({
      timeout: 5000,
    });
    // The button should be re-enabled (loading done)
    await expect(page.getByRole("button", { name: "Sign In" })).toBeEnabled({
      timeout: 5000,
    });
  });

  test("toggles password visibility", async ({ page }) => {
    // Use a stable locator — the second <input> on the page (first is email)
    const passwordField = page.locator("form input").nth(1);
    await passwordField.fill("secret123");

    // Initially type=password
    await expect(passwordField).toHaveAttribute("type", "password");

    // Click the toggle button
    await page.getByRole("button", { name: "Show password" }).click();
    await expect(passwordField).toHaveAttribute("type", "text");

    // Click again to hide
    await page.getByRole("button", { name: "Hide password" }).click();
    await expect(passwordField).toHaveAttribute("type", "password");
  });

  test("shows loading spinner when submitting", async ({
    page,
    browserName,
  }) => {
    // WebKit doesn't reliably render MUI CircularProgress in Playwright
    test.skip(browserName === "webkit", "WebKit spinner rendering limitation");

    // Mock Supabase auth with a delayed response
    await page.route("**/auth/v1/token*", async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SESSION),
      });
    });

    await page.getByLabel("Email").fill("test@optionstaxhub.com");
    await page.locator('input[type="password"]').fill("password123");
    await page.getByRole("button", { name: "Sign In" }).click();

    // Button should show a spinner (CircularProgress) and be disabled
    await expect(page.getByRole("progressbar")).toBeVisible({ timeout: 5000 });
    // When loading, the button contains a spinner instead of text, so find by type=submit
    await expect(page.locator('button[type="submit"]')).toBeDisabled();
  });

  test("'Sign up' link navigates to sign-up page", async ({ page }) => {
    await page.getByRole("link", { name: "Sign up" }).click();
    await expect(page).toHaveURL(/\/auth\/signup/);
    await expect(
      page.getByRole("heading", { name: "Create Account" }),
    ).toBeVisible({
      timeout: 10000,
    });
  });
});
