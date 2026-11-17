import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  MOCK_ANALYSIS,
  goToAuthenticatedHome,
  setupMockAnalysis,
  setupMockAuth,
} from "./fixtures";

const TAXABLE_CSV = path.resolve(
  __dirname,
  "../../public/sample-cross-account-taxable.csv",
);
const IRA_CSV = path.resolve(__dirname, "../../public/sample-robinhood-ira.csv");

const VIEWPORTS = [
  { name: "1280", width: 1280, height: 800 },
  { name: "390", width: 390, height: 844 },
] as const;

const COUNT_ONLY = {
  ...MOCK_ANALYSIS,
  sample_run: false,
  packet_unlocked: false,
  cross_account_wash_count: 1,
  wash_sale_chains: [],
};

const DETAIL = {
  ...MOCK_ANALYSIS,
  sample_run: true,
  packet_unlocked: false,
  cross_account_wash_count: 1,
  positions: [
    {
      ...MOCK_ANALYSIS.positions[0],
      tax_lots: [
        {
          ...MOCK_ANALYSIS.positions[0].tax_lots[0],
          account_name: "Individual",
        },
      ],
    },
    MOCK_ANALYSIS.positions[1],
  ],
  wash_sale_chains: [
    {
      chain_id: "cross-qqq",
      symbol: "QQQ",
      reconcile_status: "not_on_1099",
      cross_account: true,
      permanent_disallowance: true,
      plain_english:
        "Not on your 1099: cross-account. Estimated disallowed amount $120. An IRA replacement may apply, ask a tax pro. No basis carryover.",
      hops: [
        {
          hop_id: "cross-qqq:h0",
          hop_index: 0,
          symbol: "QQQ",
          sale_date: "2026-02-10",
          sale_quantity: 4,
          proceeds: 1880,
          cost_basis: 2000,
          loss: 120,
          disallowed_loss: 120,
          reconcile_status: "not_on_1099",
          account_name: "Individual",
          cross_account: true,
          permanent_disallowance: true,
          plain_english:
            "Not on your 1099: cross-account. Estimated disallowed amount $120. An IRA replacement may apply, ask a tax pro. No basis carryover.",
          replacement_buys: [
            {
              activity_date: "2026-02-20",
              quantity: 4,
              price: 480,
              cash_cost: 1920,
              allocated_quantity: 4,
              allocated_disallowed: 120,
              explained_basis: 1920,
              account_name: "IRA (traditional)",
            },
          ],
        },
      ],
    },
  ],
};

async function mockGuest(page: Page) {
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
}

async function analyzeAccounts(page: Page) {
  const uploadToggle = page.getByText("Upload CSV or 1099");
  if (await uploadToggle.isVisible()) {
    await uploadToggle.click();
  }
  await page.getByTestId("account-exports-input").setInputFiles([
    TAXABLE_CSV,
    IRA_CSV,
  ]);
  await page.getByTestId("account-label-1").selectOption("ira_traditional");
  await page.getByTestId("analyze-accounts").click();
}

async function expectNav(page: Page, signedIn: boolean, width: number) {
  const nav = page.getByTestId("primary-nav");
  await expect(nav.getByRole("link", { name: "Tax", exact: true })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Options", exact: true })).toHaveAttribute(
    "href",
    "/options",
  );
  if (width >= 1280) {
    if (signedIn) {
      await expect(page.getByTestId("header-account-name")).toBeVisible();
    } else {
      await expect(page.getByTestId("header-sign-in")).toBeVisible();
    }
    return;
  }
  await page.getByTestId("header-menu").click();
  if (signedIn) {
    await expect(page.getByTestId("account-settings")).toBeVisible();
  } else {
    await expect(page.getByTestId("header-sign-in")).toBeVisible();
  }
  await page.keyboard.press("Escape");
}

test.describe("cross-account wash exports", () => {
  for (const viewport of VIEWPORTS) {
    test(`guest count only at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await mockGuest(page);
      await setupMockAnalysis(page, COUNT_ONLY);
      await page.goto("/dashboard");
      await expect(page.getByTestId("account-exports")).toBeVisible({
        timeout: 15000,
      });
      await expectNav(page, false, viewport.width);
      await analyzeAccounts(page);
      await expect(page.getByTestId("cross-account-wash-count")).toHaveText(
        "1 cross-account wash event is not on your 1099.",
      );
      await expect(
        page.getByText("Not on your 1099: cross-account", { exact: true }),
      ).toHaveCount(0);
      await expect(page.getByText(/Estimated disallowed amount/)).toHaveCount(0);
    });

    test(`signed-in detail at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await setupMockAuth(page);
      await setupMockAnalysis(page, DETAIL);
      await goToAuthenticatedHome(page);
      await expectNav(page, true, viewport.width);
      await analyzeAccounts(page);
      await expect(
        page.getByText("Not on your 1099: cross-account", { exact: true }),
      ).toBeVisible();
      await expect(page.getByText(/may apply, ask a tax pro/).first()).toBeVisible();
      await expect(page.getByTestId("cross-account-wash-count")).toHaveCount(0);
      await expect(page.getByTestId("wash-chain-account-cross-qqq")).toHaveText(
        "Individual",
      );
      await page.getByRole("tab", { name: /Lot ledger/ }).click();
      await expect(page.getByText("Individual").first()).toBeVisible();
    });
  }

  test("landing CTAs reach the sample and the upload desk", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mockGuest(page);
    await setupMockAnalysis(page, DETAIL);
    await page.goto("/");
    await expect(page.getByTestId("landing-try-sample")).toBeVisible();
    await expect(page.getByRole("button", { name: "I have a CSV" })).toBeVisible();
    await expect(page.getByTestId("landing-options-tools")).toBeVisible();
    await page.getByRole("button", { name: "I have a CSV" }).click();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByTestId("account-exports")).toBeVisible();
    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(
      page.getByText("Not on your 1099: cross-account", { exact: true }),
    ).toBeVisible({
      timeout: 15000,
    });
  });
});
