import path from "node:path";
import { test, expect } from "@playwright/test";
import { MOCK_ANALYSIS, MOCK_SESSION, MOCK_USER } from "./fixtures";

const LEAK_SYMBOL = "ZQZX";

test.describe("Sign-out clears the previous account run", () => {
  test("a guest dashboard does not render the signed-in run after logout 403", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");

    const leakAnalysis = {
      ...MOCK_ANALYSIS,
      analysis_id: "leak-run-153",
      positions: MOCK_ANALYSIS.positions.map((position, index) =>
        index === 0 ? { ...position, symbol: LEAK_SYMBOL } : position,
      ),
      suggestions: [
        {
          ...MOCK_ANALYSIS.suggestions[0],
          symbol: LEAK_SYMBOL,
          display_label: `${LEAK_SYMBOL} shared-device lot`,
        },
      ],
    };

    let logoutUrl = "";
    await page.route("**/auth/v1/logout*", (route) => {
      logoutUrl = route.request().url();
      return route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          error: "forbidden",
          error_description: "Forbidden",
          msg: "Forbidden",
        }),
      });
    });
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
    await page.route("**/api/tax-profile*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "null",
      }),
    );
    await page.route("**/api/portfolio/history*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      }),
    );
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(leakAnalysis),
      }),
    );
    await page.route("**/health", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      }),
    );

    await page.goto("/auth/signin");
    await page.getByLabel("Email").fill("test@optionstaxhub.com");
    await page.locator('input[type="password"]').fill("password123");
    await page.getByRole("button", { name: "Sign In" }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });

    await page.locator("#desk-csv-input").setInputFiles(
      path.resolve(__dirname, "../../../test.csv"),
    );
    await expect(page.getByText("Net Open Position Value")).toBeVisible({
      timeout: 15000,
    });
    const lotTab = page.getByRole("tab", { name: /Lot ledger/ });
    await lotTab.click();
    await expect(page.getByText(LEAK_SYMBOL).first()).toBeVisible({
      timeout: 10000,
    });

    await page.getByTestId("header-account").click();
    await page.getByRole("menuitem", { name: "Sign Out" }).click();
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 10000 });
    expect(logoutUrl).toContain("/auth/v1/logout");
    expect(logoutUrl).toContain("scope=global");

    await page.goto("/dashboard");
    await expect(
      page.getByText(/Get started with your first analysis/i),
    ).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("This run lives on this device")).toHaveCount(0);
    await expect(page.getByText(LEAK_SYMBOL)).toHaveCount(0);
    await expect(page.getByText("Net Open Position Value")).toHaveCount(0);

    const stored = await page.evaluate(() => {
      const values: string[] = [];
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key) {
          values.push(`${key}=${localStorage.getItem(key) ?? ""}`);
        }
      }
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key) {
          values.push(`${key}=${sessionStorage.getItem(key) ?? ""}`);
        }
      }
      return values.join("\n");
    });
    expect(stored).not.toContain(LEAK_SYMBOL);
    expect(stored).not.toContain("leak-run-153");
    expect(stored).not.toContain("optionstaxhub-analysis=");
  });
});
