import { test, expect } from "@playwright/test";
import { MOCK_SESSION, MOCK_USER } from "./fixtures";

/**
 * E2E tests for the Sign Up page (/auth/signup).
 *
 * Covers: successful registration, validation errors (password mismatch,
 * short password, missing phone), password toggle, and navigation to sign-in.
 */

test.describe("Sign Up Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/auth/signup");
    await expect(
      page.getByRole("heading", { name: "Create Account" }),
    ).toBeVisible({
      timeout: 10000,
    });
  });

  test("renders the sign-up form with all fields", async ({ page }) => {
    await expect(page.getByLabel("Name")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Email" })).toBeVisible();
    await expect(page.locator('input[type="password"]').first()).toBeVisible();
    await expect(page.locator('input[type="password"]').nth(1)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Create Account" }),
    ).toBeVisible();
    await expect(page.getByText(/Join OptionsTaxHub/)).toBeVisible();
    await expect(page.getByLabel("Provider Type")).toHaveCount(0);
  });

  test("shows error when passwords do not match", async ({
    page,
    browserName,
  }) => {
    // WebKit on Windows doesn't reliably trigger form onSubmit via button click
    test.skip(browserName === "webkit", "WebKit form submission limitation");

    await page.getByLabel("Name").fill("John Doe");
    await page.getByRole("textbox", { name: "Email" }).fill("john@example.com");
    await page.locator('input[type="password"]').first().fill("password123");
    await page
      .locator('input[type="password"]')
      .nth(1)
      .fill("differentpassword");

    await page.getByRole("button", { name: "Create Account" }).click();

    await expect(page.getByText("Passwords do not match")).toBeVisible({
      timeout: 10000,
    });
  });

  test("shows error when password is too short", async ({
    page,
    browserName,
  }) => {
    // WebKit on Windows doesn't reliably trigger form onSubmit via button click
    test.skip(browserName === "webkit", "WebKit form submission limitation");

    await page.getByLabel("Name").fill("John Doe");
    await page.getByRole("textbox", { name: "Email" }).fill("john@example.com");
    await page.locator('input[type="password"]').first().fill("abc");
    await page.locator('input[type="password"]').nth(1).fill("abc");

    await page.getByRole("button", { name: "Create Account" }).click();

    await expect(
      page.getByText("Password must be at least 6 characters"),
    ).toBeVisible({ timeout: 10000 });
  });

  test("successful sign-up shows a check-email page instead of redirecting to sign-in", async ({
    page,
    browserName,
  }) => {
    // WebKit on Windows doesn't process mocked Supabase auth responses correctly
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");

    // Mock Supabase signUp to succeed without a session (email confirm ON)
    await page.route("**/auth/v1/signup*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          user: {
            ...MOCK_USER,
            email: "newuser@example.com",
            email_confirmed_at: null,
          },
          session: null,
        }),
      }),
    );

    await page.getByLabel("Name").fill("New User");
    await page
      .getByRole("textbox", { name: "Email" })
      .fill("newuser@example.com");
    await page.locator('input[type="password"]').first().fill("password123");
    await page.locator('input[type="password"]').nth(1).fill("password123");

    await page.getByRole("button", { name: "Create Account" }).click();

    await expect(
      page.getByRole("heading", { name: "Check your email" }),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText(/We sent a confirmation link to newuser@example.com/),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Resend confirmation email" }),
    ).toBeVisible();
    await expect(page).not.toHaveURL(/\/auth\/signin/);
  });

  test("toggles password visibility for both password fields", async ({
    page,
  }) => {
    // Use getByRole('textbox') — Playwright treats password inputs as textboxes
    const passwordInput = page.getByRole("textbox", {
      name: "Password",
      exact: true,
    });
    const confirmInput = page.getByRole("textbox", {
      name: "Confirm Password",
    });
    await passwordInput.fill("secret123");
    await confirmInput.fill("secret123");

    // Both start as password type
    await expect(passwordInput).toHaveAttribute("type", "password");
    await expect(confirmInput).toHaveAttribute("type", "password");

    // Toggle first password field
    const toggleButtons = page.getByRole("button", { name: /password/i });
    await toggleButtons.first().click();
    await expect(passwordInput).toHaveAttribute("type", "text");
    await expect(confirmInput).toHaveAttribute("type", "password");
  });

  test("'Sign in' link navigates to sign-in page", async ({ page }) => {
    await page.getByRole("link", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/auth\/signin/);
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible({
      timeout: 10000,
    });
  });

  test("home link and tax disclaimer are visible", async ({ page }) => {
    await expect(page.getByRole("link", { name: /OptionsTaxHub/ })).toBeVisible();
    await expect(
      page.getByText(/For educational and simulation purposes only/),
    ).toBeVisible();
  });

  test("shows loading spinner during submission", async ({
    page,
    browserName,
  }) => {
    // WebKit doesn't reliably render MUI CircularProgress in Playwright
    test.skip(browserName === "webkit", "WebKit spinner rendering limitation");

    // Mock slow Supabase signup
    await page.route("**/auth/v1/signup*", async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SESSION),
      });
    });

    await page.getByLabel("Name").fill("New User");
    await page.getByRole("textbox", { name: "Email" }).fill("new@example.com");
    await page.locator('input[type="password"]').first().fill("password123");
    await page.locator('input[type="password"]').nth(1).fill("password123");

    await page.getByRole("button", { name: "Create Account" }).click();

    // Should show loading spinner
    await expect(page.getByRole("progressbar")).toBeVisible({ timeout: 5000 });
    // When loading, button contains spinner not text — find by type=submit
    await expect(page.locator('button[type="submit"]')).toBeDisabled();
  });
});
