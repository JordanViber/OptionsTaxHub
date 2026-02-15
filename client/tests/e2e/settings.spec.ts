import { test, expect } from "@playwright/test";
import {
  setupMockAuth,
  injectMockSession,
} from "./fixtures";

/**
 * E2E tests for the Settings page (/settings).
 *
 * Covers: form pre-population, auth gate, back navigation,
 * form interactions, and the tax disclaimer banner.
 */

test.describe("Settings Page", () => {
  test("redirects to sign-in when not authenticated", async ({ page }) => {
    // Don't inject auth — visit settings directly
    await page.goto("/settings");

    // Should redirect to sign-in (wait for the URL to change)
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 15000 });
  });

  test("loads and displays the settings form when authenticated", async ({
    page,
  }) => {
    await setupMockAuth(page);
    await injectMockSession(page);
    await page.goto("/settings");

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText("Your Tax Profile")).toBeVisible();
    await expect(page.getByLabel("Filing Status")).toBeVisible();
    await expect(page.getByLabel("Estimated Annual Income")).toBeVisible();
    await expect(page.getByLabel("State")).toBeVisible();
    await expect(page.getByLabel("Tax Year")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Save Tax Profile/i }),
    ).toBeVisible();
  });

  test("pre-populates form fields from saved tax profile", async ({ page }) => {
    await setupMockAuth(page);
    await injectMockSession(page);
    await page.goto("/settings");

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 10000,
    });

    // Wait for profile data to load
    await expect(page.getByText("Single")).toBeVisible({ timeout: 5000 });

    // Income field should show the saved value
    const incomeInput = page.locator('input[type="number"]');
    await expect(incomeInput).toHaveValue("85000");
  });

  test("Dashboard back button navigates to home", async ({ page }) => {
    await setupMockAuth(page);
    await injectMockSession(page);

    // Also mock analyze endpoint to avoid errors on dashboard
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      }),
    );

    await page.goto("/settings");
    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 10000,
    });

    // Click the Dashboard back button
    await page.getByRole("button", { name: "Dashboard" }).click();

    // Should navigate to home
    await expect(page.getByText("OptionsTaxHub")).toBeVisible({
      timeout: 10000,
    });
    expect(page.url()).toMatch(/\/$/);
  });

  test("shows tax disclaimer banner", async ({ page }) => {
    await setupMockAuth(page);
    await injectMockSession(page);
    await page.goto("/settings");

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 10000,
    });

    // The TaxDisclaimer text
    await expect(
      page.getByText(/educational and simulation purposes only/i),
    ).toBeVisible();
  });

  test("filing status dropdown changes value", async ({ page }) => {
    await setupMockAuth(page);
    await injectMockSession(page);
    await page.goto("/settings");

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 10000,
    });

    // Wait for form to load
    await expect(page.getByText("Single")).toBeVisible({ timeout: 5000 });

    // Open the Filing Status dropdown and select a different option
    await page.getByLabel("Filing Status").click();
    await page.getByRole("option", { name: "Married Filing Jointly" }).click();

    // Value should update — wait for dropdown to close, then check the displayed value
    await expect(page.getByText("Married Filing Jointly").first()).toBeVisible({
      timeout: 3000,
    });
  });

  test("tax year dropdown shows available years", async ({ page }) => {
    await setupMockAuth(page);
    await injectMockSession(page);
    await page.goto("/settings");

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 10000,
    });

    // Open the Tax Year dropdown
    await page.getByLabel("Tax Year").click();

    // Should show 2025 and 2026
    await expect(page.getByRole("option", { name: "2025" })).toBeVisible();
    await expect(page.getByRole("option", { name: "2026" })).toBeVisible();
  });
});
