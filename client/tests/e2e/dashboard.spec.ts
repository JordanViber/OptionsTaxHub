import { test, expect } from "@playwright/test";
import {
  MOCK_ANALYSIS,
  MOCK_ANALYSIS_GAIN,
  MOCK_ANALYSIS_NO_SUGGESTIONS,
  MOCK_ANALYSIS_NO_WASH_SALES,
  MOCK_ANALYSIS_WITH_WARNINGS,
  MOCK_HISTORY,
  goToAuthenticatedHome,
  setupMockAnalysis,
  uploadTestCsv,
} from "./fixtures";

/**
 * Playwright E2E tests for the OptionsTaxHub dashboard (/).
 *
 * Section 1 — Original UI-fix regressions (install prompt, settings redirect,
 *              tooltips, cursor, PNL arrow, history drawer).
 * Section 2 — Core upload & results flow.
 * Section 3 — Tabs, suggestions, wash-sale banner.
 * Section 4 — User menu & navigation.
 * Section 5 — Error, loading & warning states.
 */

// ── Section 1: UI-fix regressions ──────────────────────────────────

test.describe("Dashboard UI Fixes", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockAnalysis(page);
    await goToAuthenticatedHome(page);
  });

  // --- Fix 1: Install prompt appears at most once per session ---
  test("install prompt does not reappear after page reload within same session", async ({
    page,
  }) => {
    // Set sessionStorage flag to simulate prompt already shown
    await page.evaluate(() => {
      sessionStorage.setItem("installPromptShownThisSession", "true");
    });

    // Reload the page
    await page.reload();
    await expect(page.getByText("OptionsTaxHub")).toBeVisible({
      timeout: 10000,
    });

    // Verify install prompt is not visible
    // The install prompt contains text like "Install OptionsTaxHub" or "App Already Installed"
    await page.waitForTimeout(3500); // Wait longer than the 3s delay in component
    const installPrompt = page.locator(
      "text=Install OptionsTaxHub, text=App Already Installed",
    );
    await expect(installPrompt).not.toBeVisible();
  });

  // --- Fix 2: Settings save navigates back to home ---
  test("saving tax profile navigates back to dashboard", async ({ page }) => {
    // Navigate to settings
    await page.click("text=Settings");
    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 5000,
    });

    // Click save button
    await page.click("text=Save Tax Profile");

    // Should show success message
    await expect(page.getByText("Tax profile saved successfully!")).toBeVisible(
      { timeout: 5000 },
    );

    // Should navigate back to dashboard after ~1.5s
    await expect(page.getByText("Portfolio Analysis")).toBeVisible({
      timeout: 5000,
    });
    expect(page.url()).toMatch(/\/dashboard/);
  });

  // --- Fix 3: Position table tooltips ---
  test("positions table shows tooltips for ST, LT, STK, and risk chips", async ({
    page,
  }) => {
    await uploadTestCsv(page);

    // Wait for the positions table to render inside the DataGrid
    await expect(page.locator('[data-testid="DashboardIcon"]')).toBeVisible();

    // Hover over ST chip and check tooltip
    const stChip = page.locator(".MuiChip-root", { hasText: "ST" }).first();
    await stChip.hover();
    await expect(
      page.getByRole("tooltip", { name: /Short-Term/i }),
    ).toBeVisible({ timeout: 3000 });

    // Hover away to dismiss tooltip
    await page.mouse.move(0, 0);
    await page.waitForTimeout(500);

    // Hover over STK chip and check tooltip
    const stkChip = page.locator(".MuiChip-root", { hasText: "STK" }).first();
    await stkChip.hover();
    await expect(
      page.getByRole("tooltip", { name: /Stock position/i }),
    ).toBeVisible({ timeout: 3000 });

    // Hover away
    await page.mouse.move(0, 0);
    await page.waitForTimeout(500);

    // Hover over wash-sale risk chip
    const riskChip = page.locator(".MuiChip-root", { hasText: "Risk" }).first();
    await riskChip.hover();
    await expect(
      page.getByRole("tooltip", { name: /Wash-Sale Risk/i }),
    ).toBeVisible({ timeout: 3000 });
  });

  // --- Fix 4: Cursor does not change shape on text hover ---
  test("cursor remains default on non-interactive text elements", async ({
    page,
  }) => {
    await uploadTestCsv(page);

    // Check cursor on the "Portfolio Analysis" heading (outside upload zone)
    const heading = page.getByText("Portfolio Analysis").first();
    const cursor = await heading.evaluate((el) => getComputedStyle(el).cursor);
    expect(cursor).toBe("default");

    // Upload zone text should inherit pointer cursor from parent
    const caption = page.getByText("Robinhood transaction export");
    const captionCursor = await caption.evaluate(
      (el) => getComputedStyle(el).cursor,
    );
    expect(captionCursor).toBe("pointer");
  });

  // --- Fix 5a: PNL downward arrow for losses ---
  test("shows downward arrow icon when unrealized PNL is negative", async ({
    page,
  }) => {
    await uploadTestCsv(page);

    // The TrendingDown icon has data-testid="TrendingDownIcon"
    const trendingDown = page.locator('[data-testid="TrendingDownIcon"]');
    await expect(trendingDown.first()).toBeVisible();
  });

  // --- Fix 5b: PNL upward arrow for gains ---
  test("shows upward arrow icon when unrealized PNL is positive", async ({
    page,
  }) => {
    // Re-route analysis to return positive PNL
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ANALYSIS_GAIN),
      }),
    );

    await uploadTestCsv(page);

    // The TrendingUp icon has data-testid="TrendingUpIcon"
    const trendingUp = page.locator('[data-testid="TrendingUpIcon"]');
    await expect(trendingUp.first()).toBeVisible();
  });

  // --- Fix 6: Upload history sidebar ---
  test("history drawer opens and shows past uploads", async ({ page }) => {
    // Click the History button in the AppBar
    await page.click("text=History");

    // Drawer should open with "Upload History" heading
    await expect(page.getByText("Upload History")).toBeVisible({
      timeout: 3000,
    });

    // Should display the mock history entries
    await expect(page.getByText("portfolio_jan.csv")).toBeVisible();
    await expect(page.getByText("portfolio_feb.csv")).toBeVisible();

    // Should show metadata (positions count)
    await expect(page.getByText("2 positions")).toBeVisible();
    await expect(page.getByText("3 positions")).toBeVisible();
  });

  test("history drawer shows empty state when no uploads exist", async ({
    page,
  }) => {
    // Unroute existing history mock and override with empty array
    await page.unroute("**/api/portfolio/history*");
    await page.route("**/api/portfolio/history*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );

    // Reload to pick up the new route
    await page.reload();
    await expect(page.getByText("OptionsTaxHub")).toBeVisible({
      timeout: 10000,
    });

    // Open history drawer
    await page.click("text=History");
    await expect(page.getByText("Upload History")).toBeVisible({
      timeout: 3000,
    });

    // Should show empty state text
    await expect(page.getByText("No past uploads yet")).toBeVisible();
  });

  test("new upload appears in history after analysis", async ({ page }) => {
    const updatedHistory = [
      {
        id: "hist-new",
        user_id: "test-user-123",
        filename: "test.csv",
        uploaded_at: new Date().toISOString(),
        summary: MOCK_ANALYSIS.summary,
        positions_count: 2,
        total_market_value: 21100,
      },
      ...MOCK_HISTORY,
    ];

    // Unroute the existing history mock and set up updated one
    await page.unroute("**/api/portfolio/history*");
    await page.route("**/api/portfolio/history*", (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updatedHistory),
      });
    });

    // Upload CSV
    await uploadTestCsv(page);

    // Open history drawer
    await page.click("text=History");
    await expect(page.getByText("Upload History")).toBeVisible({
      timeout: 3000,
    });

    // Should now show 3 entries (the new one + 2 existing)
    await expect(page.getByText("test.csv")).toBeVisible();
    await expect(page.getByText("portfolio_jan.csv")).toBeVisible();
  });
});

// ── Section 2: Core upload & results flow ──────────────────────────

test.describe("Dashboard Upload & Results", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockAnalysis(page);
    await goToAuthenticatedHome(page);
  });

  test("upload renders all four summary cards with correct values", async ({
    page,
  }) => {
    await uploadTestCsv(page);

    // Portfolio Value
    await expect(page.getByText("Portfolio Value")).toBeVisible();
    await expect(page.getByText("$21,100")).toBeVisible();
    await expect(page.getByText("2 positions")).toBeVisible();

    // Unrealized P&L (also appears as DataGrid column header, so use first())
    await expect(page.getByText("Unrealized P&L").first()).toBeVisible();

    // Harvestable Losses (also appears as -$625.00 in DataGrid P&L cell, so use first())
    await expect(page.getByText("Harvestable Losses")).toBeVisible();
    await expect(page.getByText("$625").first()).toBeVisible();

    // Est. Tax Savings
    await expect(page.getByText("Est. Tax Savings")).toBeVisible();
    await expect(page.getByText("$138")).toBeVisible();
  });

  test("positions table renders rows for all positions", async ({ page }) => {
    await uploadTestCsv(page);

    // Both symbols from mock data should appear (scoped to grid to avoid wash-sale text matches)
    await expect(page.getByRole('grid').getByText("AAPL")).toBeVisible();
    await expect(page.getByRole('grid').getByText("MSFT")).toBeVisible();
  });

  test("upload area is clickable and opens file dialog", async ({ page }) => {
    // The upload area has role="button"
    const uploadArea = page.getByRole("button", { name: /Click to upload CSV/i });
    await expect(uploadArea).toBeVisible();
  });

  test("re-upload same file updates results", async ({ page }) => {
    await uploadTestCsv(page);
    await expect(page.getByText("$21,100")).toBeVisible({ timeout: 10000 });

    // Change the mock to return different data
    const updatedAnalysis = {
      ...MOCK_ANALYSIS,
      summary: {
        ...MOCK_ANALYSIS.summary,
        total_market_value: 50000,
        positions_count: 5,
      },
    };
    // Remove old mock and set new one
    await page.unroute("**/api/portfolio/analyze*");
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updatedAnalysis),
      }),
    );

    // Upload again — can't reuse uploadTestCsv because "Portfolio Value" is already visible
    const fileInput = page.locator('input[type="file"]');
    const pathMod = await import("node:path");
    const csvPath = pathMod.resolve(__dirname, "../../../test.csv");
    await fileInput.setInputFiles(csvPath);

    // Wait for the updated values
    await expect(page.getByText("$50,000")).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("5 positions")).toBeVisible();
  });

  test("tax disclaimer renders after analysis", async ({ page }) => {
    await uploadTestCsv(page);

    await expect(
      page.getByText(/educational and simulation purposes only/i),
    ).toBeVisible();
  });
});

// ── Section 3: Tabs, suggestions & wash-sale banner ────────────────

test.describe("Dashboard Tabs & Components", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockAnalysis(page);
    await goToAuthenticatedHome(page);
  });

  test("tabs switch between Positions and Suggestions", async ({ page }) => {
    await uploadTestCsv(page);

    // Positions tab is active by default — AAPL also appears in wash-sale warning, so use .first()
    await expect(page.getByText("AAPL").first()).toBeVisible();
    await expect(page.getByRole('tab', { name: /Positions/ })).toBeVisible();
    await expect(page.getByRole('tab', { name: /Suggestions/ })).toBeVisible();

    // Click Suggestions tab
    await page.getByRole('tab', { name: /Suggestions/ }).click();

    // Suggestions content should show the AAPL suggestion card
    await expect(page.getByText("AAPL").first()).toBeVisible();
    await expect(page.getByText("Estimated Loss")).toBeVisible();
    await expect(page.getByText("Tax Savings", { exact: true })).toBeVisible();

    // Click back to Positions
    await page.getByRole('tab', { name: /Positions/ }).click();
    await expect(page.getByRole('grid').getByText("MSFT")).toBeVisible();
  });

  test("suggestion card shows AI badge", async ({ page }) => {
    await uploadTestCsv(page);

    // Switch to suggestions tab
    await page.getByRole('tab', { name: /Suggestions/ }).click();

    // AI chip should be visible (ai_generated: true)
    await expect(
      page.locator(".MuiChip-root", { hasText: "AI" }),
    ).toBeVisible();
  });

  test("suggestion card expand shows AI analysis and replacement candidates", async ({
    page,
  }) => {
    await uploadTestCsv(page);
    await page.getByRole('tab', { name: /Suggestions/ }).click();

    // Click expand button
    await page.getByRole("button", { name: "Show more" }).click();

    // AI Analysis section
    await expect(page.getByText("AI Analysis")).toBeVisible();
    await expect(
      page.getByText("Consider harvesting this loss for tax savings."),
    ).toBeVisible();

    // Replacement Candidates section
    await expect(page.getByText("Replacement Candidates")).toBeVisible();
    await expect(page.getByText("QQQ", { exact: true })).toBeVisible();
    await expect(page.getByText("Invesco QQQ Trust")).toBeVisible();
    await expect(page.getByText("Tech sector ETF")).toBeVisible();

    // Collapse it again
    await page.getByRole("button", { name: "Show less" }).click();
    await expect(page.getByText("AI Analysis")).not.toBeVisible();
  });

  test("suggestion card shows wash-sale risk inline warning", async ({
    page,
  }) => {
    await uploadTestCsv(page);
    await page.getByRole('tab', { name: /Suggestions/ }).click();

    // Wash-sale inline warning box
    await expect(
      page.getByText(/Repurchased within 30 days/),
    ).toBeVisible();
  });

  test("empty suggestions shows empty state message", async ({ page }) => {
    // Override with no suggestions
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ANALYSIS_NO_SUGGESTIONS),
      }),
    );

    await uploadTestCsv(page);
    await page.getByRole('tab', { name: /Suggestions/ }).click();

    await expect(
      page.getByText("No tax-loss harvesting opportunities found"),
    ).toBeVisible();
  });

  test("wash-sale warning banner renders with flag details", async ({
    page,
  }) => {
    await uploadTestCsv(page);

    await expect(
      page.getByText("Wash-Sale Rule Violations Detected (1)"),
    ).toBeVisible();
    await expect(
      page.getByText("AAPL: $125 loss disallowed"),
    ).toBeVisible();
    await expect(
      page.getByText(/Sold 10 shares at a loss on 3\/1/),
    ).toBeVisible();
  });

  test("no wash-sale banner when no flags", async ({ page }) => {
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ANALYSIS_NO_WASH_SALES),
      }),
    );

    await uploadTestCsv(page);

    await expect(
      page.getByText("Wash-Sale Rule Violations Detected"),
    ).not.toBeVisible();
  });
});

// ── Section 4: User menu & navigation ──────────────────────────────

test.describe("Dashboard User Menu & Navigation", () => {
  test.beforeEach(async ({ page }) => {
    await setupMockAnalysis(page);
    await goToAuthenticatedHome(page);
  });

  test("user avatar menu shows name and email", async ({ page }) => {
    // Click the user button to open menu
    await page.getByText("Test User").click();

    // Menu should show display name + email
    await expect(
      page.getByText("Test User (test@optionstaxhub.com)"),
    ).toBeVisible({ timeout: 3000 });
    await expect(page.getByText("Sign Out")).toBeVisible();
  });

  test("sign out redirects to sign-in page", async ({ page }) => {
    await page.getByText("Test User").click();
    await expect(page.getByText("Sign Out")).toBeVisible();

    await page.getByText("Sign Out").click();

    // Should redirect to sign-in
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 10000 });
  });

  test("Settings button navigates to settings page", async ({ page }) => {
    await page.getByRole("button", { name: "Settings" }).click();

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 5000,
    });
    expect(page.url()).toMatch(/\/settings/);
  });

  test("tax profile inline link navigates to settings", async ({ page }) => {
    // Click the "tax profile" button-link in the upload card description
    await page
      .locator("button", { hasText: "tax profile" })
      .click();

    await expect(page.getByText("Tax Profile Settings")).toBeVisible({
      timeout: 5000,
    });
    expect(page.url()).toMatch(/\/settings/);
  });
});

// ── Section 5: Error, loading & warning states ─────────────────────

test.describe("Dashboard Error & Loading States", () => {
  test("shows error alert when analysis fails", async ({ page }) => {
    // Mock analysis to return 500 error
    await page.route("**/api/portfolio/analyze*", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Internal server error" }),
      });
    });

    await goToAuthenticatedHome(page);

    // Upload — this should trigger an error
    const fileInput = page.locator('input[type="file"]');
    const path = await import("node:path");
    const csvPath = path.resolve(__dirname, "../../../test.csv");
    await fileInput.setInputFiles(csvPath);

    // Error alert should appear
    await expect(page.getByText("Analysis Failed")).toBeVisible({
      timeout: 15000,
    });
  });

  test("shows loading bar during analysis", async ({ page }) => {
    // Mock slow analysis
    await page.route("**/api/portfolio/analyze*", async (route) => {
      await new Promise((r) => setTimeout(r, 2000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ANALYSIS),
      });
    });

    await goToAuthenticatedHome(page);

    const fileInput = page.locator('input[type="file"]');
    const path = await import("node:path");
    const csvPath = path.resolve(__dirname, "../../../test.csv");
    await fileInput.setInputFiles(csvPath);

    // LinearProgress should be visible
    await expect(page.getByRole("progressbar")).toBeVisible({ timeout: 2000 });
    // Upload text should change
    await expect(page.getByText("Analyzing portfolio...")).toBeVisible();

    // Wait for results and verify loading disappears
    await expect(page.getByText("Portfolio Value")).toBeVisible({
      timeout: 15000,
    });
  });

  test("shows warning alerts when analysis returns warnings", async ({
    page,
  }) => {
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ANALYSIS_WITH_WARNINGS),
      }),
    );

    await goToAuthenticatedHome(page);
    await uploadTestCsv(page);

    await expect(
      page.getByText(
        "Some positions could not be priced — defaulting to cost basis.",
      ),
    ).toBeVisible();
    await expect(
      page.getByText("Wash-sale detection is approximate."),
    ).toBeVisible();
  });
});

// ── Section 6: Tip Jar ─────────────────────────────────────────────

test.describe("Tip Jar", () => {
  test.beforeEach(async ({ page }) => {
    await goToAuthenticatedHome(page);
  });

  test("tip button opens tip jar dialog", async ({ page }) => {
    await page.click("text=Tip");
    await expect(page.getByText("Support OptionsTaxHub")).toBeVisible();
    await expect(page.getByText("$3")).toBeVisible();
    await expect(page.getByText("$10")).toBeVisible();
    await expect(page.getByText("$25")).toBeVisible();
  });

  test("tip jar shows all three tiers with descriptions", async ({ page }) => {
    await page.click("text=Tip");
    await expect(page.getByText("Buy us a coffee")).toBeVisible();
    await expect(page.getByText("Buy us lunch")).toBeVisible();
    await expect(page.getByText("You're amazing!")).toBeVisible();
  });

  test("tip jar closes with close button", async ({ page }) => {
    await page.click("text=Tip");
    await expect(page.getByText("Support OptionsTaxHub")).toBeVisible();
    await page.click('button[aria-label="close"]');
    await expect(page.getByText("Support OptionsTaxHub")).not.toBeVisible();
  });

  test("clicking a tip tier calls checkout endpoint", async ({ page }) => {
    // Track whether checkout endpoint was called
    let checkoutCalled = false;
    await page.route("**/api/tips/checkout", (route) => {
      checkoutCalled = true;
      // Fulfill without a real redirect so the page stays open
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          checkout_url: "https://checkout.stripe.com/test",
        }),
      });
    });

    await page.click("text=Tip");
    // Click the Coffee tier card
    await page.getByText("Buy us a coffee").click();
    // Wait a moment for the request to be sent
    await page.waitForTimeout(1000);
    expect(checkoutCalled).toBe(true);
  });

  test("tip jar shows error on checkout failure", async ({ page }) => {
    await page.route("**/api/tips/checkout", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Stripe is not configured" }),
      }),
    );

    await page.click("text=Tip");
    await page.getByText("Buy us a coffee").click();
    await expect(page.getByRole("alert")).toBeVisible({ timeout: 5000 });
  });

  test("stripe footer text is visible", async ({ page }) => {
    await page.click("text=Tip");
    await expect(
      page.getByText("Payments processed securely by Stripe"),
    ).toBeVisible();
    await expect(
      page.getByText("One-time payment"),
    ).toBeVisible();
  });
});
