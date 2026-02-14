/**
 * Shared mock data and helpers for Playwright E2E tests.
 *
 * Centralises Supabase auth interception, backend API mocking,
 * and test data so individual spec files stay concise.
 */

import { expect, type Page } from "@playwright/test";
import path from "path";

// ── Mock data ──────────────────────────────────────────────────────

export const MOCK_USER = {
  id: "test-user-123",
  email: "test@optionstaxhub.com",
  user_metadata: {
    display_name: "Test User",
    first_name: "Test",
    last_name: "User",
  },
  aud: "authenticated",
  role: "authenticated",
  created_at: "2025-01-01T00:00:00Z",
};

export const MOCK_SESSION = {
  access_token: "mock-access-token",
  token_type: "bearer",
  expires_in: 3600,
  refresh_token: "mock-refresh-token",
  user: MOCK_USER,
};

export const MOCK_ANALYSIS = {
  positions: [
    {
      symbol: "AAPL",
      quantity: 50,
      avg_cost_basis: 182.5,
      total_cost_basis: 9125,
      current_price: 170.0,
      market_value: 8500,
      unrealized_pnl: -625,
      unrealized_pnl_pct: -6.85,
      earliest_purchase_date: "2025-01-15",
      holding_period_days: 120,
      is_long_term: false,
      asset_type: "stock",
      tax_lots: [],
      wash_sale_risk: true,
    },
    {
      symbol: "MSFT",
      quantity: 30,
      avg_cost_basis: 405.0,
      total_cost_basis: 12150,
      current_price: 420.0,
      market_value: 12600,
      unrealized_pnl: 450,
      unrealized_pnl_pct: 3.7,
      earliest_purchase_date: "2023-06-01",
      holding_period_days: 600,
      is_long_term: true,
      asset_type: "stock",
      tax_lots: [],
      wash_sale_risk: false,
    },
  ],
  tax_lots: [],
  suggestions: [
    {
      symbol: "AAPL",
      action: "Sell",
      quantity: 50,
      current_price: 170.0,
      cost_basis_per_share: 182.5,
      estimated_loss: -625,
      tax_savings_estimate: 137.5,
      holding_period_days: 120,
      is_long_term: false,
      wash_sale_risk: true,
      wash_sale_explanation:
        "Repurchased within 30 days — selling now triggers wash-sale rule",
      replacement_candidates: [
        { symbol: "QQQ", name: "Invesco QQQ Trust", reason: "Tech sector ETF" },
      ],
      ai_explanation: "Consider harvesting this loss for tax savings.",
      ai_generated: true,
      priority: 1,
    },
  ],
  wash_sale_flags: [
    {
      symbol: "AAPL",
      sale_date: "2025-03-01",
      sale_quantity: 10,
      sale_loss: 125,
      repurchase_date: "2025-03-15",
      repurchase_quantity: 10,
      disallowed_loss: 125,
      adjusted_cost_basis: 195.0,
      explanation:
        "Sold 10 shares at a loss on 3/1 and repurchased 10 shares on 3/15 (14 days later).",
    },
  ],
  summary: {
    total_market_value: 21100,
    total_cost_basis: 21275,
    total_unrealized_pnl: -175,
    total_unrealized_pnl_pct: -0.8,
    total_harvestable_losses: 625,
    estimated_tax_savings: 137.5,
    positions_count: 2,
    lots_with_losses: 1,
    lots_with_gains: 1,
    wash_sale_flags_count: 1,
  },
  tax_profile: {
    filing_status: "single",
    estimated_annual_income: 75000,
    tax_year: 2025,
  },
  disclaimer: "For educational/simulation purposes only.",
  errors: [],
  warnings: [],
};

/** Analysis with positive PNL */
export const MOCK_ANALYSIS_GAIN = {
  ...MOCK_ANALYSIS,
  summary: {
    ...MOCK_ANALYSIS.summary,
    total_unrealized_pnl: 1500,
    total_unrealized_pnl_pct: 7.1,
  },
};

/** Analysis with no suggestions */
export const MOCK_ANALYSIS_NO_SUGGESTIONS = {
  ...MOCK_ANALYSIS,
  suggestions: [],
};

/** Analysis with warnings */
export const MOCK_ANALYSIS_WITH_WARNINGS = {
  ...MOCK_ANALYSIS,
  warnings: [
    "Some positions could not be priced — defaulting to cost basis.",
    "Wash-sale detection is approximate.",
  ],
};

/** Analysis with no wash-sale flags */
export const MOCK_ANALYSIS_NO_WASH_SALES = {
  ...MOCK_ANALYSIS,
  wash_sale_flags: [],
  summary: {
    ...MOCK_ANALYSIS.summary,
    wash_sale_flags_count: 0,
  },
};

export const MOCK_HISTORY = [
  {
    id: "hist-1",
    user_id: "test-user-123",
    filename: "portfolio_jan.csv",
    uploaded_at: "2025-05-10T14:30:00Z",
    summary: MOCK_ANALYSIS.summary,
    positions_count: 2,
    total_market_value: 21100,
  },
  {
    id: "hist-2",
    user_id: "test-user-123",
    filename: "portfolio_feb.csv",
    uploaded_at: "2025-06-15T09:00:00Z",
    summary: MOCK_ANALYSIS.summary,
    positions_count: 3,
    total_market_value: 35000,
  },
];

export const MOCK_TAX_PROFILE = {
  user_id: "test-user-123",
  filing_status: "single",
  estimated_annual_income: 85000,
  state: "CA",
  tax_year: 2025,
};

// ── Helpers ────────────────────────────────────────────────────────

/**
 * Set up Supabase auth interception + backend API mocks.
 */
export async function setupMockAuth(page: Page) {
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

  await page.route("**/api/tax-profile/**", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_TAX_PROFILE),
      });
    }
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Tax profile saved",
          profile: MOCK_TAX_PROFILE,
        }),
      });
    }
    return route.continue();
  });

  await page.route("**/api/portfolio/history/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_HISTORY),
    }),
  );
}

/**
 * Mock the portfolio analysis endpoint.
 */
export async function setupMockAnalysis(
  page: Page,
  analysisData = MOCK_ANALYSIS,
) {
  await page.route("**/api/portfolio/analyze*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(analysisData),
    }),
  );
}

/**
 * Inject a Supabase session into localStorage before page load.
 */
export async function injectMockSession(page: Page) {
  await page.addInitScript(() => {
    const authData = {
      access_token: "mock-access-token",
      token_type: "bearer",
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      refresh_token: "mock-refresh-token",
      user: {
        id: "test-user-123",
        email: "test@optionstaxhub.com",
        user_metadata: {
          display_name: "Test User",
          first_name: "Test",
          last_name: "User",
        },
        aud: "authenticated",
        role: "authenticated",
        created_at: "2025-01-01T00:00:00Z",
      },
    };
    const keys = Object.keys(localStorage);
    const existing = keys.find((k) => k.includes("auth-token"));
    const storageKey = existing || "sb-vgrlucxqncajjdoaoctq-auth-token";
    localStorage.setItem(storageKey, JSON.stringify(authData));
  });
}

/**
 * Navigate to home with mocked auth and wait for dashboard content.
 */
export async function goToAuthenticatedHome(page: Page) {
  await setupMockAuth(page);
  await injectMockSession(page);
  await page.goto("/");
  await expect(page.getByText("OptionsTaxHub")).toBeVisible({ timeout: 10000 });
}

/**
 * Upload the test CSV and wait for Portfolio Value card to appear.
 */
export async function uploadTestCsv(page: Page) {
  const fileInput = page.locator('input[type="file"]');
  const csvPath = path.resolve(__dirname, "../../../test.csv");
  await fileInput.setInputFiles(csvPath);
  await expect(page.getByText("Portfolio Value")).toBeVisible({
    timeout: 15000,
  });
}
