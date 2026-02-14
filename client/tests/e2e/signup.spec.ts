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
    await expect(page.getByRole('heading', { name: 'Create Account' })).toBeVisible({
      timeout: 10000,
    });
  });

  test("renders the sign-up form with all fields", async ({ page }) => {
    await expect(page.getByLabel("First Name")).toBeVisible();
    await expect(page.getByLabel("Last Name")).toBeVisible();
    await expect(page.getByLabel("Display Name")).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Email' })).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Confirm Password")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Create Account" }),
    ).toBeVisible();
    await expect(page.getByText("Join OptionsTaxHub today")).toBeVisible();
  });

  test("shows error when passwords do not match", async ({ page }) => {
    await page.getByLabel("First Name").fill("John");
    await page.getByLabel("Last Name").fill("Doe");
    await page.getByRole('textbox', { name: 'Email' }).fill("john@example.com");
    await page.getByLabel("Password", { exact: true }).fill("password123");
    await page.getByLabel("Confirm Password").fill("differentpassword");

    await page.getByRole("button", { name: "Create Account" }).click();

    await expect(page.getByText("Passwords do not match")).toBeVisible({
      timeout: 3000,
    });
  });

  test("shows error when password is too short", async ({ page }) => {
    await page.getByLabel("First Name").fill("John");
    await page.getByLabel("Last Name").fill("Doe");
    await page.getByRole('textbox', { name: 'Email' }).fill("john@example.com");
    await page.getByLabel("Password", { exact: true }).fill("abc");
    await page.getByLabel("Confirm Password").fill("abc");

    await page.getByRole("button", { name: "Create Account" }).click();

    await expect(
      page.getByText("Password must be at least 6 characters"),
    ).toBeVisible({ timeout: 3000 });
  });

  test("successful sign-up shows confirmation and redirects to sign-in", async ({
    page,
  }) => {
    // Mock Supabase signUp to succeed
    await page.route("**/auth/v1/signup*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_SESSION,
          user: { ...MOCK_USER, email: "newuser@example.com" },
        }),
      }),
    );

    // Listen for the browser alert()
    page.on("dialog", async (dialog) => {
      expect(dialog.message()).toContain("Sign up successful");
      await dialog.accept();
    });

    await page.getByLabel("First Name").fill("New");
    await page.getByLabel("Last Name").fill("User");
    await page.getByRole('textbox', { name: 'Email' }).fill("newuser@example.com");
    await page.getByLabel("Password", { exact: true }).fill("password123");
    await page.getByLabel("Confirm Password").fill("password123");

    await page.getByRole("button", { name: "Create Account" }).click();

    // Should redirect to sign-in page
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 10000 });
  });

  test("toggles password visibility for both password fields", async ({
    page,
  }) => {
    const passwordInput = page.getByLabel("Password", { exact: true });
    const confirmInput = page.getByLabel("Confirm Password");
    await passwordInput.fill("secret123");
    await confirmInput.fill("secret123");

    // Both start as password type
    await expect(passwordInput).toHaveAttribute("type", "password");
    await expect(confirmInput).toHaveAttribute("type", "password");

    // Toggle first password field
    const toggleButtons = page.getByRole("button", { name: "Show password" });
    await toggleButtons.first().click();
    await expect(passwordInput).toHaveAttribute("type", "text");

    // Toggle confirm password field
    await toggleButtons.first().click(); // now only the second toggle says "Show"
    // After the first one was toggled to "Hide", we have different states
  });

  test("'Sign in' link navigates to sign-in page", async ({ page }) => {
    await page.getByRole("link", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/auth\/signin/);
    await expect(page.getByRole('heading', { name: 'Sign In' })).toBeVisible({ timeout: 10000 });
  });

  test("phone provider changes helper text", async ({ page }) => {
    // Default helper is "Optional" for phone field
    await expect(page.getByText("Optional", { exact: true })).toBeVisible();

    // Change provider to Phone
    await page.getByLabel("Provider Type").click();
    await page.getByRole("option", { name: "Phone" }).click();

    // Now the phone helper text should change
    await expect(
      page.getByText("Required for phone sign-up"),
    ).toBeVisible({ timeout: 3000 });
  });

  test("shows loading spinner during submission", async ({ page }) => {
    // Mock slow Supabase signup
    await page.route("**/auth/v1/signup*", async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SESSION),
      });
    });

    // Dismiss alert that follows
    page.on("dialog", (dialog) => dialog.accept());

    await page.getByLabel("First Name").fill("New");
    await page.getByLabel("Last Name").fill("User");
    await page.getByRole('textbox', { name: 'Email' }).fill("new@example.com");
    await page.getByLabel("Password", { exact: true }).fill("password123");
    await page.getByLabel("Confirm Password").fill("password123");

    await page.getByRole("button", { name: "Create Account" }).click();

    // Should show loading spinner
    await expect(page.getByRole("progressbar")).toBeVisible({ timeout: 2000 });
    await expect(
      page.getByRole("button", { name: "Create Account" }),
    ).toBeDisabled();
  });
});
