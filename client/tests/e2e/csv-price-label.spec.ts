import { test, expect } from "@playwright/test";
import {
  MOCK_ANALYSIS,
  goToAuthenticatedHome,
  setupMockAnalysis,
  uploadTestCsv,
} from "./fixtures";

const CSV_LABEL = "Prices as of your CSV (2026-08-12)";

const csvBasis = {
  quotes: [
    {
      symbol: "AAPL",
      price: 170,
      source: "csv",
      provider: null,
      as_of: "2026-08-12",
    },
    {
      symbol: "MSFT",
      price: 420,
      source: "csv",
      provider: null,
      as_of: "2026-08-12",
    },
  ],
  live: false,
  csv_as_of: "2026-08-12",
  label: CSV_LABEL,
};

const csvAnalysis = {
  ...MOCK_ANALYSIS,
  sample_run: true,
  packet_unlocked: false,
  price_basis: csvBasis,
  tax_profile: {
    ...MOCK_ANALYSIS.tax_profile,
    tax_year: 2026,
  },
};

const signedInAnalysis = {
  ...MOCK_ANALYSIS,
  sample_run: false,
  packet_unlocked: true,
  price_basis: {
    ...csvBasis,
    label: "Prices as of your CSV (2025-03-15)",
    csv_as_of: "2025-03-15",
    quotes: csvBasis.quotes.map((quote) => ({
      ...quote,
      as_of: "2025-03-15",
    })),
  },
};

const viewports = [
  { name: "desktop-1280", width: 1280, height: 800 },
  { name: "phone-390", width: 390, height: 844 },
] as const;

async function mockGuestDesk(page: Parameters<typeof setupMockAnalysis>[0]) {
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

async function expectCsvLabels(
  page: Parameters<typeof setupMockAnalysis>[0],
  label: string,
) {
  await expect(page.getByTestId("price-as-of-pnl")).toHaveText(label);
  await expect(page.getByTestId("price-as-of-prices")).toHaveText(label);
  await expect(page.getByTestId("price-as-of-harvest")).toHaveText(label);
  await expect(page.getByText(/^Live /)).toHaveCount(0);
  await expect(page.getByText(/current price/i)).toHaveCount(0);

  const lotTab = page.getByRole("tab", { name: /Lot ledger/ });
  await expect(async () => {
    if ((await lotTab.getAttribute("aria-selected")) !== "true") {
      await lotTab.evaluate((el) =>
        el.scrollIntoView({ block: "center", inline: "nearest" }),
      );
      await lotTab.click();
    }
    await expect(lotTab).toHaveAttribute("aria-selected", "true");
  }).toPass();
  await expect(page.getByTestId("price-as-of-positions")).toHaveText(label);
}

for (const viewport of viewports) {
  test(`guest sample shows the CSV price label on ${viewport.name}`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await setupMockAnalysis(page, csvAnalysis);
    await mockGuestDesk(page);
    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    await expect(page.getByText("Tax Year: 2026")).toBeVisible({
      timeout: 20000,
    });
    await expectCsvLabels(page, CSV_LABEL);
    if (viewport.width === 390) {
      const scrollWidth = await page.evaluate(
        () => document.documentElement.scrollWidth,
      );
      expect(scrollWidth).toBeLessThanOrEqual(391);
    }
  });

  test(`signed-in user shows the CSV price label on ${viewport.name}`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await setupMockAnalysis(page, signedInAnalysis);
    await goToAuthenticatedHome(page);
    await uploadTestCsv(page);
    await expectCsvLabels(page, "Prices as of your CSV (2025-03-15)");
    if (viewport.width === 390) {
      const scrollWidth = await page.evaluate(
        () => document.documentElement.scrollWidth,
      );
      expect(scrollWidth).toBeLessThanOrEqual(391);
    }
  });
}
