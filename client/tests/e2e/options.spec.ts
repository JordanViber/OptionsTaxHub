import { test, expect } from "@playwright/test";

test("options desk is reachable and shows the switcher", async ({ page }) => {
  await page.goto("/options");
  await expect(page.getByTestId("desk-switcher")).toBeVisible();
  await expect(page.getByTestId("entry-analysis-panel")).toBeVisible();
  await expect(page.getByRole("link", { name: /Tax desk/i })).toHaveAttribute(
    "href",
    "/dashboard",
  );
});

test("options desk reads the tax-desk book from sessionStorage", async ({
  page,
}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem(
      "optionstaxhub-analysis",
      JSON.stringify({ positions: [{ symbol: "AAPL", quantity: 10 }] }),
    );
  });
  await page.goto("/options");
  await expect(page.getByTestId("options-book-status")).toContainText(
    /Using the book already loaded/i,
  );
  await page.getByTestId("desk-switch-tax").click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByTestId("desk-switcher")).toBeVisible();
});
