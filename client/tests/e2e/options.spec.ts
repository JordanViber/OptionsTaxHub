import { test, expect } from "@playwright/test";

test("options desk is reachable and shows the switcher", async ({ page }) => {
  await page.goto("/options");
  await expect(page.getByTestId("desk-switcher")).toBeVisible();
  await expect(page.getByTestId("entry-analysis-panel")).toBeVisible();
  await expect(page.getByRole("link", { name: /Tax desk/i })).toHaveAttribute(
    "href",
    "/dashboard",
  );
};
