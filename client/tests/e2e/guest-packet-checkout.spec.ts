import { test, expect } from "@playwright/test";
import { MOCK_ANALYSIS, MOCK_SESSION, MOCK_USER } from "./fixtures";

const ANALYSIS_ID = "11111111-1111-4111-8111-111111111111";
const RESUME_PATH = "/dashboard?resume_checkout=1";

test.describe("Guest packet checkout", () => {
  test("guest Pay $49 routes through sign-in and calls year-close checkout with Authorization", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");
    test.setTimeout(60_000);

    const sample = {
      ...MOCK_ANALYSIS,
      analysis_id: ANALYSIS_ID,
      sample_run: true,
      tax_profile: {
        ...MOCK_ANALYSIS.tax_profile,
        tax_year: 2026,
      },
    };
    const checkoutCalls: Array<{ url: string; authorization: string; body: string }> =
      [];

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
    await page.route("**/api/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(sample),
      }),
    );

    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    const pay = page.getByRole("button", { name: "Pay $49" });
    await expect(pay).toBeEnabled({ timeout: 20000 });

    const storedBeforePay = await page.evaluate(() =>
      sessionStorage.getItem("optionstaxhub-analysis"),
    );
    expect(storedBeforePay).toContain(ANALYSIS_ID);

    await pay.click();
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 15000 });
    expect(new URL(page.url()).searchParams.get("next")).toBe(RESUME_PATH);
    const storedAfterPay = await page.evaluate(() =>
      sessionStorage.getItem("optionstaxhub-analysis"),
    );
    expect(storedAfterPay).toContain(ANALYSIS_ID);
    expect(checkoutCalls).toHaveLength(0);

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
        body: JSON.stringify(null),
      }),
    );
    await page.route("**/api/portfolio/history*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      }),
    );
    await page.route("**/api/year-close-packet/checkout", async (route) => {
      checkoutCalls.push({
        url: route.request().url(),
        authorization: route.request().headers().authorization || "",
        body: route.request().postData() || "",
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          checkout_url: "https://checkout.stripe.com/c/pay/cs_test_e2e",
          session_id: "cs_test_e2e",
          amount: 4900,
          stripe_mode: "test",
        }),
      });
    });
    await page.route("https://checkout.stripe.com/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<html><body>stripe-test-checkout</body></html>",
      }),
    );

    await page.getByLabel("Email").fill("test@optionstaxhub.com");
    await page.locator('input[type="password"]').fill("password123");
    await page.getByRole("button", { name: "Sign In" }).click();

    await expect.poll(() => checkoutCalls.length, { timeout: 20000 }).toBe(1);
    expect(checkoutCalls[0].url).toContain("/api/year-close-packet/checkout");
    expect(checkoutCalls[0].url).not.toContain("/api/tips/checkout");
    expect(checkoutCalls[0].authorization).toBe("Bearer mock-access-token");
    expect(JSON.parse(checkoutCalls[0].body).analysis_id).toBe(ANALYSIS_ID);
    await expect(page).toHaveURL(/checkout\.stripe\.com/, { timeout: 15000 });
    expect(checkoutCalls).toHaveLength(1);
  });
});
