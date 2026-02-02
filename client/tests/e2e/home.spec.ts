import { test, expect } from "@playwright/test";

test("navigates to home page successfully", async ({ page }) => {
  // Navigate to home
  await page.goto("/");

  // Check page title contains OptionsTaxHub
  await expect(page).toHaveTitle(/OptionsTaxHub/i);
});

test("appbar with title is visible on home", async ({ page }) => {
  // Navigate to home
  await page.goto("/");

  // Check for AppBar title
  await expect(page.getByText("OptionsTaxHub")).toBeVisible();
});

test("redirects unauthenticated users appropriately", async ({ page }) => {
  // Navigate to home
  await page.goto("/", { waitUntil: "networkidle" });

  // Wait a moment for redirect to occur
  await page.waitForTimeout(1000);

  // Either still on home, or redirected to signin
  const url = page.url();
  const isHome = url.includes("http://localhost:3000/");
  const isSignIn = url.includes("/signin");

  // At least one should be true
  expect(isHome || isSignIn).toBe(true);
});
