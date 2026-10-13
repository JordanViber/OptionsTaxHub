import { test, expect } from "@playwright/test";
import { goToAuthenticatedHome, setupMockAnalysis } from "./fixtures";

test.describe("Tax and Options navigation", () => {
  test("unknown routes render a styled not-found page", async ({ page }) => {
    await page.goto("/this-route-does-not-exist");
    await expect(page.getByTestId("not-found")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Tax", exact: true }),
    ).toHaveAttribute("href", "/dashboard");
    await expect(
      page.getByRole("link", { name: "Options", exact: true }),
    ).toHaveAttribute("href", "/options");
  });

  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "phone-390", width: 390, height: 844 },
  ] as const) {
    test(`Tax and Options nav work on ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await setupMockAnalysis(page);
      await goToAuthenticatedHome(page);

      const nav = page.getByRole("navigation", { name: "Primary" });
      await expect(nav.getByRole("link", { name: "Tax", exact: true })).toBeVisible();
      await expect(
        nav.getByRole("link", { name: "Options", exact: true }),
      ).toBeVisible();

      await nav.getByRole("link", { name: "Options", exact: true }).click();
      await expect(page).toHaveURL(/\/options/);
      await expect(page.getByTestId("entry-analysis-panel")).toBeVisible();
      await expect(
        page.getByRole("heading", { name: /LEAP ranking and what-if/i }),
      ).toBeVisible();
      await expect(page.getByTestId("not-found")).toHaveCount(0);

      await nav.getByRole("link", { name: "Tax", exact: true }).click();
      await expect(page).toHaveURL(/\/dashboard/);
      await expect(page.getByText("Portfolio Analysis")).toBeVisible();
    });
  }

  test("guest Options sign-in returns to Options", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/health", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      }),
    );
    await page.route("**/auth/v1/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      }),
    );
    await page.goto("/options");
    await expect(page.getByTestId("entry-analysis-panel")).toBeVisible({
      timeout: 15000,
    });
    const signIn = page.getByTestId("entry-guest-signin");
    await expect(signIn).toBeVisible();
    await expect(signIn).toHaveAttribute("href", "/auth/signin?next=/options");
    await expect(
      page.getByText("Connect Robinhood after sign-in for live top-3"),
    ).toHaveCount(0);
    await signIn.click();
    await expect(page).toHaveURL(/\/auth\/signin\?next=\/options/);
    await expect(page.getByText(/Sign in to continue on Options/i)).toBeVisible();
  });
});
