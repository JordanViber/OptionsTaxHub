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

const QQQ_LINE =
  "Not on your 1099: cross-account. An Individual sale of 4 QQQ on Feb 10 lost $120. A buy in an IRA (traditional) within 30 days before or after that sale covers 4 shares. Estimated disallowed amount $120. Permanent: a loss washed by an IRA buy is lost for good. It does not add to the IRA's basis.";

const AMD_LINE =
  "You sold 10 AMD at a $300 loss on Jul 15 and bought 10 back on Jul 24, so $300 moved into the new lot's basis. This matches your 1099 wash adjustment of $300.";

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
  suggestions: [
    {
      ...MOCK_ANALYSIS.suggestions[0],
      symbol: "NVDA",
      display_label: "NVDA",
      suggestion_id: "NVDA-1",
      quantity: 40,
      estimated_loss: 800,
      tax_savings_estimate: 176,
      wash_sale_risk: false,
      wash_sale_explanation: "",
      replacement_candidates: [
        { symbol: "SMH", name: "VanEck Semiconductor ETF", reason: "Sector" },
        { symbol: "SOXX", name: "iShares Semiconductor ETF", reason: "Index" },
      ],
      ai_explanation: "",
      ai_generated: false,
    },
  ],
  harvest_checks: [
    {
      symbol: "SOFI",
      trigger_count: 3,
      clean: false,
      open_loss: 100,
      disallowed_loss: 100,
      earliest_clean_sell_date: "2026-09-10",
      safe_rebuy_date: "2026-10-11",
      triggers: [],
      permanent_disallowance: false,
      line: "$100 of your $100 loss may be disallowed",
      as_of: "2026-08-12",
    },
    {
      symbol: "TSLA",
      trigger_count: 0,
      clean: true,
      open_loss: 40,
      disallowed_loss: 0,
      earliest_clean_sell_date: null,
      safe_rebuy_date: null,
      triggers: [],
      permanent_disallowance: false,
      line: "No replacement buys found in the 30 days either side. A wash-sale rule may apply, ask a tax pro.",
      as_of: "2026-08-12",
    },
  ],
  wash_sale_chains: [
    {
      chain_id: "amd-match",
      symbol: "AMD",
      reconcile_status: "matches",
      plain_english: AMD_LINE,
      hops: [
        {
          hop_id: "amd-match:h0",
          hop_index: 0,
          symbol: "AMD",
          sale_date: "2026-07-15",
          sale_quantity: 10,
          proceeds: 1000,
          cost_basis: 1300,
          loss: 300,
          disallowed_loss: 300,
          reconcile_status: "matches",
          plain_english: AMD_LINE,
          replacement_buys: [],
        },
      ],
    },
    {
      chain_id: "cross-qqq",
      symbol: "QQQ",
      reconcile_status: "not_on_1099",
      cross_account: true,
      permanent_disallowance: true,
      plain_english: QQQ_LINE,
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
          plain_english: QQQ_LINE,
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

async function expectSampleCopy(page: Page) {
  const note = page.getByTestId("harvest-recommended-action");
  await expect(note).toContainText(
    "40 NVDA shares show a short-term loss you may be able to harvest.",
  );
  await expect(note).toContainText("Estimated loss $800");
  await expect(note).toContainText("Estimated tax savings $176");
  await expect(note).not.toContainText("Sell");
  await expect(note).not.toContainText("SMH");
  await expect(note).not.toContainText("SOXX");
  await expect(page.getByText("SMH")).toHaveCount(0);
  await expect(page.getByText("SOXX")).toHaveCount(0);
  await expect(page.getByTestId("harvest-check-SOFI")).toContainText(
    "$100 of your $100 loss may be disallowed",
  );
  await expect(page.getByTestId("harvest-check-SOFI")).toContainText(
    "Sep 10, 2026",
  );
  await expect(page.getByTestId("harvest-check-SOFI")).toContainText(
    "Oct 11, 2026",
  );
  await expect(page.getByTestId("harvest-check-TSLA")).toHaveCount(0);
  await expect(page.getByTestId("harvest-check-SPCX")).toHaveCount(0);
  await expect(page.getByText(AMD_LINE)).toBeVisible();
  await expect(page.getByText(/An Individual sale of 4 QQQ/)).toBeVisible();
  await expect(page.getByText(/A buy in an IRA \(traditional\)/)).toBeVisible();
  await expect(page.getByTestId("wash-chain-permanent-cross-qqq")).toBeVisible();
  await expect(
    page.getByText(
      "Permanent: a loss washed by an IRA buy is lost for good. It does not add to the IRA's basis.",
    ).first(),
  ).toBeVisible();
}

async function expectFitsWidth(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  for (const testId of [
    "harvest-recommended-action",
    "harvest-check-SOFI",
    "wash-sale-chain-card",
    "primary-nav",
  ]) {
    const box = await page.getByTestId(testId).boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(391);
  }
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

    test(`guest sample copy at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await mockGuest(page);
      await setupMockAnalysis(page, DETAIL);
      await page.goto("/dashboard");
      await expect(page.getByTestId("account-exports")).toBeVisible({
        timeout: 15000,
      });
      await expectNav(page, false, viewport.width);
      await analyzeAccounts(page);
      await expectSampleCopy(page);
      if (viewport.width === 390) {
        await expectFitsWidth(page);
      }
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
      await expectSampleCopy(page);
      if (viewport.width === 390) {
        await expectFitsWidth(page);
        await page.screenshot({
          path: "/opt/cursor/artifacts/jor-192-copy-390.png",
          fullPage: true,
        });
      }
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
