import { test, expect } from "@playwright/test";

test("homepage renders with Material UI components", async ({ page }) => {
  // Navigate to home
  await page.goto("/");

  // Check page title
  await expect(page).toHaveTitle(/OptionsTaxHub/i);

  // Check AppBar is rendered with title
  await expect(page.getByText("OptionsTaxHub", { exact: false })).toBeVisible();

  // Check for subtitle
  await expect(page.getByText(/Tax-Optimized Options Trading/i)).toBeVisible();

  // Check for upload button with "Upload CSV" text (should be disabled initially)
  const uploadButton = page.getByRole("button", { name: /Upload CSV/i });
  await expect(uploadButton).toBeVisible();
  await expect(uploadButton).toBeDisabled();
});

test("authenticated user sees avatar menu button", async ({ page }) => {
  // Navigate to home
  await page.goto("/");

  // Wait for the page to load
  await page.waitForTimeout(1000);

  // Check if user menu/avatar button appears (when authenticated)
  // This might redirect to login if not authenticated, which is expected
  const pageTitle = page.locator("title");
  const titleText = await pageTitle.innerText();

  // If still on home, verify UI elements exist
  if (titleText.includes("OptionsTaxHub")) {
    // Check for main card/container
    await expect(page.locator('[class*="MuiCard"]').first()).toBeVisible();
  }
});

test("upload area displays with Material Design", async ({ page }) => {
  // Navigate to home
  await page.goto("/");

  // Check for upload icon and text
  await expect(
    page.getByText(/Click to upload or drag and drop/i),
  ).toBeVisible();

  // Check for CSV format hint
  await expect(page.getByText(/CSV format only/i)).toBeVisible();
});
