import { test, expect, type Page, type Route } from "@playwright/test";
import {
  injectMockSession,
  MOCK_ANALYSIS,
  MOCK_SESSION,
  MOCK_USER,
  setupMockAnalysis,
  setupMockAuth,
} from "./fixtures";

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

  test("email confirmation in a new tab resumes checkout from localStorage", async ({
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
    await pay.click();
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 15000 });

    const handoff = JSON.parse(
      await page.evaluate(() =>
        localStorage.getItem("optionstaxhub-packet-resume"),
      ) as string,
    ) as Record<string, unknown>;
    expect(Object.keys(handoff).sort()).toEqual([
      "analysisId",
      "exp",
      "sample",
      "taxYear",
      "v",
    ]);
    expect(handoff).toMatchObject({
      v: 2,
      sample: true,
      analysisId: ANALYSIS_ID,
      taxYear: 2026,
    });
    expect(handoff.exp).toEqual(expect.any(Number));

    // Leave the app so this tab's sign-in listener cannot claim checkout.
    // The both-tabs-open case is covered by the test below.
    await page.goto("about:blank");

    const resumePage = await page.context().newPage();
    const checkoutCalls: Array<{ url: string; authorization: string }> = [];
    await armFreshResumePage(resumePage, sample, checkoutCalls);
    await resumePage.goto(RESUME_PATH);

    const started = await resumePage.evaluate(() => {
      const win = window as Window & {
        __packetResumeSessionAtStart?: string | null;
        __packetResumeLocalAtStart?: string | null;
      };
      return {
        session: win.__packetResumeSessionAtStart,
        local: win.__packetResumeLocalAtStart,
      };
    });
    expect(started.session).toBeNull();
    expect(JSON.parse(started.local || "{}").sample).toBe(true);

    await expect.poll(() => checkoutCalls.length, { timeout: 20000 }).toBe(1);
    expect(checkoutCalls[0].url).toContain("/api/year-close-packet/checkout");
    expect(checkoutCalls[0].authorization).toBe("Bearer mock-access-token");
    await expect(resumePage).toHaveURL(/checkout\.stripe\.com/, { timeout: 15000 });
    expect(checkoutCalls).toHaveLength(1);
    expect(
      await resumePage.evaluate(() =>
        localStorage.getItem("optionstaxhub-packet-resume"),
      ),
    ).toBeNull();
  });

  test("a resume tab does not check out a different saved run", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");
    test.setTimeout(60_000);

    const sample = guestSample();
    const wrongId = "wrong-run-2222-2222-4222-8222-222222222222";
    const checkoutBodies: string[] = [];

    await armGuestLanding(page, sample);
    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    const pay = page.getByRole("button", { name: "Pay $49" });
    await expect(pay).toBeEnabled({ timeout: 20000 });
    await pay.click();
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 15000 });
    // Unload the sign-in tab so its listener cannot claim checkout first.
    // This tab must be the one that decides which analysis id is posted.
    await page.goto("about:blank");

    const resumePage = await page.context().newPage();
    await resumePage.addInitScript((stored: string) => {
      sessionStorage.setItem("optionstaxhub-analysis", stored);
    }, JSON.stringify(wrongRun(wrongId)));
    await injectMockSession(resumePage);
    await armResumeApis(resumePage, sample, checkoutBodies);
    // Test-local override, registered after armResumeApis. The mock Stripe
    // URL embeds the posted analysis id so a navigation is proof of which
    // run was checked out. Other tests keep the shared helper.
    await resumePage.route("**/api/year-close-packet/checkout", async (route) => {
      const body = route.request().postData() || "";
      checkoutBodies.push(body);
      let postedId = "";
      try {
        const parsed = JSON.parse(body) as { analysis_id?: unknown };
        postedId = typeof parsed.analysis_id === "string" ? parsed.analysis_id : "";
      } catch {
        postedId = "";
      }
      const sessionId = `cs_test_e2e_resume_${postedId}`;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          checkout_url: `https://checkout.stripe.com/c/pay/${sessionId}`,
          session_id: sessionId,
          amount: 4900,
          stripe_mode: "test",
        }),
      });
    });
    await resumePage.goto(RESUME_PATH);

    // Firefox at 390 can leave for Stripe before the panel is observed.
    // One 20s poll: a checkout POST has arrived, and either the panel is
    // visible or the page is already on the mock Stripe URL for that id.
    // A navigation that has left the panel but not committed Stripe yet
    // stays inside this same wait.
    let settledUrl = "";
    let settledPanel = false;
    await expect
      .poll(
        async () => {
          const panelVisible = await resumePage
            .getByTestId("year-close-packet-panel")
            .isVisible()
            .catch(() => false);
          const url = resumePage.url();
          const onCorrectStripe =
            url.includes("checkout.stripe.com") &&
            url.includes(`cs_test_e2e_resume_${ANALYSIS_ID}`) &&
            !url.includes(wrongId);
          if (checkoutBodies.length > 0 && (panelVisible || onCorrectStripe)) {
            settledUrl = url;
            settledPanel = panelVisible;
            return true;
          }
          return false;
        },
        { timeout: 20000 },
      )
      .toBe(true);
    expect(checkoutBodies.length).toBeLessThanOrEqual(1);
    for (const body of checkoutBodies) {
      const postedId = JSON.parse(body).analysis_id;
      expect(postedId).not.toBe(wrongId);
      expect(postedId).toBe(ANALYSIS_ID);
    }
    expect(JSON.parse(checkoutBodies[0]).analysis_id).toBe(ANALYSIS_ID);
    expect(settledUrl).not.toContain(wrongId);
    expect(
      settledPanel ||
        (settledUrl.includes("checkout.stripe.com") &&
          settledUrl.includes(`cs_test_e2e_resume_${ANALYSIS_ID}`)),
    ).toBe(true);
    if (settledUrl.includes("checkout.stripe.com")) {
      expect(settledUrl).toContain(ANALYSIS_ID);
      expect(settledUrl).not.toContain(wrongId);
    }
    const finalUrl = resumePage.url();
    expect(finalUrl).not.toContain(wrongId);
    if (finalUrl.includes("checkout.stripe.com")) {
      expect(finalUrl).toContain(ANALYSIS_ID);
      expect(finalUrl).toContain(`cs_test_e2e_resume_${ANALYSIS_ID}`);
      expect(finalUrl).not.toContain(wrongId);
    }
    await expect
      .poll(
        () => checkoutBodies.filter((body) => JSON.parse(body).analysis_id === wrongId).length,
      )
      .toBe(0);
    expect(checkoutBodies.length).toBeLessThanOrEqual(1);
  });

  test("original tab checkout is the only POST when confirm lands in a second page", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");
    test.setTimeout(60_000);

    const sample = guestSample();
    const checkoutBodies: string[] = [];
    const saved = savedRunAnalysis();

    await armGuestLanding(page, sample);
    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    const pay = page.getByRole("button", { name: "Pay $49" });
    await expect(pay).toBeEnabled({ timeout: 20000 });
    await pay.click();
    await expect(page).toHaveURL(/\/auth\/signin\?next=/, { timeout: 15000 });
    expect(checkoutBodies).toHaveLength(0);
    const handoff = JSON.parse(
      (await page.evaluate(() => localStorage.getItem("optionstaxhub-packet-resume"))) ||
        "{}",
    ) as Record<string, unknown>;
    expect(handoff).toMatchObject({
      v: 2,
      sample: true,
      analysisId: ANALYSIS_ID,
      taxYear: 2026,
    });

    await armOriginalTabCheckout(page, checkoutBodies);

    const otherTab = await page.context().newPage();
    await otherTab.route("**/health", fulfillJson({ status: "ok" }));
    await otherTab.route("**/auth/v1/**", fulfillJson({}));
    await otherTab.route("**/api/**", fulfillJson([]));
    await otherTab.goto("/auth/signin");
    await publishSessionFromOtherTab(otherTab);

    await expect.poll(() => checkoutBodies.length, { timeout: 20000 }).toBe(1);
    expect(JSON.parse(checkoutBodies[0]).analysis_id).toBe(ANALYSIS_ID);
    // Page 1 has navigated to Stripe, so read the shared timestamp from page 2.
    await expect
      .poll(
        async () =>
          otherTab.evaluate(() =>
            localStorage.getItem("optionstaxhub-packet-checkout-started"),
          ),
        { timeout: 15000 },
      )
      .toMatch(/^\d+$/);

    await armSavedRunResume(otherTab, saved, checkoutBodies);
    // The sign-in page replaces to /dashboard once the shared session
    // arrives. Firefox aborts a goto that overlaps that replacement.
    try {
      await otherTab.goto(RESUME_PATH);
    } catch (error) {
      if (!/NS_BINDING_ABORTED|frame was detached/i.test(String(error))) {
        throw error;
      }
      await otherTab.goto(RESUME_PATH);
    }
    await expect(otherTab.getByText("ZZSAVED")).toBeVisible({ timeout: 20000 });
    await expect(otherTab.getByTestId("packet-checkout-other-tab")).toHaveText(
      "Checkout opened in your other tab",
    );
    await expect(otherTab.getByRole("button", { name: "Pay $49" })).toBeVisible();
    await expect.poll(() => checkoutBodies.length).toBe(1);
  });

  test("confirm tab waits for a late history row instead of asking for a re-upload", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");
    test.setTimeout(60_000);

    const originId = "33333333-3333-4333-8333-333333333333";
    const origin = {
      ...MOCK_ANALYSIS,
      analysis_id: originId,
      sample_run: false,
      packet_unlocked: false,
      suggestions: MOCK_ANALYSIS.suggestions.map((item) => ({
        ...item,
        symbol: "ORIGINLATE",
      })),
      tax_profile: {
        ...MOCK_ANALYSIS.tax_profile,
        tax_year: 2025,
      },
    };
    const originHistory = [
      {
        id: originId,
        user_id: "test-user-123",
        filename: "origin-late.csv",
        uploaded_at: "2026-03-01T00:00:00Z",
        summary: MOCK_ANALYSIS.summary,
        positions_count: 1,
        total_market_value: 8500,
      },
    ];
    const page1Bodies: string[] = [];
    const page2Bodies: string[] = [];
    let releaseOrigin = false;

    await armGuestLanding(page, origin);
    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    const pay = page.getByRole("button", { name: "Pay $49" });
    await expect(pay).toBeEnabled({ timeout: 20000 });
    await pay.click();
    await expect(page).toHaveURL(/\/auth\/signin\?next=/, { timeout: 15000 });
    const handoff = JSON.parse(
      (await page.evaluate(() => localStorage.getItem("optionstaxhub-packet-resume"))) ||
        "{}",
    ) as Record<string, unknown>;
    expect(handoff).toMatchObject({
      v: 2,
      sample: false,
      analysisId: originId,
      taxYear: 2025,
    });

    await armOriginalTabCheckout(page, page1Bodies);

    const otherTab = await page.context().newPage();
    await otherTab.addInitScript(() => {
      const raw = localStorage.getItem("optionstaxhub-packet-resume");
      if (!raw) {
        return;
      }
      try {
        const parsed = JSON.parse(raw) as {
          sample?: boolean;
          analysisId?: string;
          taxYear?: number;
        };
        sessionStorage.setItem("optionstaxhub-packet-resume-checkout", "1");
        if (parsed.sample) {
          sessionStorage.setItem("optionstaxhub-packet-resume-sample", "1");
        } else {
          sessionStorage.removeItem("optionstaxhub-packet-resume-sample");
        }
        if (parsed.analysisId && typeof parsed.taxYear === "number") {
          sessionStorage.setItem(
            "optionstaxhub-packet-resume-analysis",
            JSON.stringify({
              analysisId: parsed.analysisId,
              taxYear: parsed.taxYear,
            }),
          );
        }
      } catch {
        // The page consume path still reads the handoff.
      }
    });
    await otherTab.route("**/health", fulfillJson({ status: "ok" }));
    await otherTab.route("**/auth/v1/**", fulfillJson({}));
    await otherTab.route("**/api/**", fulfillJson([]));
    await injectMockSession(otherTab);
    await setupMockAuth(otherTab);
    await otherTab.route("**/api/portfolio/history*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(releaseOrigin ? originHistory : []),
      });
    });
    await otherTab.route(
      "**/api/portfolio/analysis/**",
      fulfillJson({
        ...originHistory[0],
        result: origin,
      }),
    );
    await otherTab.route("**/api/year-close-packet/checkout", (route) =>
      countCheckout(route, page2Bodies),
    );

    await otherTab.goto(RESUME_PATH);
    await expect.poll(() => page1Bodies.length, { timeout: 20000 }).toBe(1);
    expect(JSON.parse(page1Bodies[0]).analysis_id).toBe(originId);
    releaseOrigin = true;

    await expect(otherTab.getByText("ORIGINLATE")).toBeVisible({ timeout: 20000 });
    // A saved run's confidence note says "Re-upload the CSV if…". That is
    // not the resume notice. The notice is only packet-resume-reupload.
    await expect(otherTab.getByTestId("packet-resume-reupload")).toHaveCount(0);
    await expect(otherTab.getByTestId("packet-checkout-other-tab")).toHaveText(
      "Checkout opened in your other tab",
    );
    await expect(otherTab.getByRole("button", { name: "Pay $49" })).toBeVisible();
    expect(page2Bodies).toHaveLength(0);
    await expect.poll(() => page1Bodies.length).toBe(1);
    expect(page2Bodies).toHaveLength(0);
  });

  test("a second dashboard tab shows the handoff and then the saved run", async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === "webkit", "WebKit auth mocking limitation");
    test.setTimeout(90_000);

    const sample = guestSample();
    await armGuestLanding(page, sample);
    await page.goto("/");
    await page.getByTestId("landing-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
    const pay = page.getByRole("button", { name: "Pay $49" });
    await expect(pay).toBeEnabled({ timeout: 20000 });
    await pay.click();
    await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 15000 });
    const handoff = await page.evaluate(() =>
      localStorage.getItem("optionstaxhub-packet-resume"),
    );
    expect(handoff).toContain(ANALYSIS_ID);

    const tab2 = await page.context().newPage();
    await armGuestLanding(tab2, sample);
    await tab2.goto("/dashboard");
    await expect(
      tab2.getByText(/Get started with your first analysis/i),
    ).toHaveCount(0);
    await expect(
      tab2
        .getByText("AAPL")
        .or(tab2.getByRole("button", { name: "Pay $49" }))
        .or(tab2.getByText("Checkout opened in your other tab"))
        .or(tab2.getByTestId("packet-resume-reupload"))
        .first(),
    ).toBeVisible({ timeout: 20000 });

    const saved = savedRunAnalysis();
    await injectMockSession(tab2);
    await setupMockAuth(tab2);
    await tab2.route("**/api/portfolio/history*", fulfillJson(saved.history));
    await tab2.route(
      "**/api/portfolio/analysis/**",
      fulfillJson({
        ...saved.history[0],
        result: saved.analysis,
      }),
    );
    await tab2.evaluate(() => {
      sessionStorage.clear();
    });
    await tab2.goto("/dashboard");
    await expect(tab2.getByText("ZZSAVED").first()).toBeVisible({
      timeout: 20000,
    });
    await expect(tab2.getByText(/First upload/i)).not.toBeVisible();
    await expect(
      tab2.getByText(/Get started with your first analysis/i),
    ).toHaveCount(0);
    await tab2.close();
  });

  const signedOutViewports = [
    { name: "desktop", width: 1280, height: 800 },
    { name: "phone-390", width: 390, height: 844 },
  ] as const;

  for (const viewport of signedOutViewports) {
    test(`signed-out second tab times out instead of spinning at ${viewport.name}`, async ({
      page,
      browserName,
    }) => {
      test.skip(browserName === "webkit", "WebKit auth mocking limitation");
      test.setTimeout(60_000);
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });

      const sample = guestSample();
      await armGuestLanding(page, sample);
      await page.goto("/");
      await page.getByTestId("landing-try-sample").click();
      await expect(page).toHaveURL(/\/dashboard/, { timeout: 30000 });
      const pay = page.getByRole("button", { name: "Pay $49" });
      await expect(pay).toBeEnabled({ timeout: 20000 });
      await pay.click();
      await expect(page).toHaveURL(/\/auth\/signin/, { timeout: 15000 });

      const tab2 = await page.context().newPage();
      await tab2.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await armGuestLanding(tab2, sample);
      await tab2.route("**/api/portfolio/analyze*", async () => {
        await new Promise(() => {
          /* a regression that re-analyzes must not finish inside the timeout */
        });
      });
      await tab2.route("**/sample-robinhood*", async () => {
        await new Promise(() => {
          /* same for the sample CSV fetch */
        });
      });
      await tab2.goto("/dashboard");
      await expect(
        tab2.getByText(/Get started with your first analysis/i),
      ).toHaveCount(0);
      await expect(
        tab2
          .getByTestId("packet-resume-reupload")
          .or(tab2.getByTestId("packet-checkout-other-tab")),
      ).toBeVisible({ timeout: 15000 });
      await tab2.close();
    });
  }
});

async function armFreshResumePage(
  resumePage: Page,
  sample: Parameters<typeof setupMockAnalysis>[1],
  checkoutCalls: Array<{ url: string; authorization: string }>,
): Promise<void> {
  await resumePage.addInitScript(() => {
    const win = window as Window & {
      __packetResumeSessionAtStart?: string | null;
      __packetResumeLocalAtStart?: string | null;
    };
    try {
      win.__packetResumeSessionAtStart = sessionStorage.getItem(
        "optionstaxhub-packet-resume-checkout",
      );
      win.__packetResumeLocalAtStart = localStorage.getItem(
        "optionstaxhub-packet-resume",
      );
    } catch {
      win.__packetResumeSessionAtStart = null;
      win.__packetResumeLocalAtStart = null;
    }
  });
  await injectMockSession(resumePage);
  await resumePage.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    }),
  );
  await resumePage.route("**/auth/v1/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    }),
  );
  await resumePage.route("**/api/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    }),
  );
  await setupMockAuth(resumePage);
  await setupMockAnalysis(resumePage, sample);
  await resumePage.route("**/api/year-close-packet/checkout", async (route) => {
    checkoutCalls.push({
      url: route.request().url(),
      authorization: route.request().headers().authorization || "",
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        checkout_url: "https://checkout.stripe.com/c/pay/cs_test_e2e_new_tab",
        session_id: "cs_test_e2e_new_tab",
        amount: 4900,
        stripe_mode: "test",
      }),
    });
  });
  await resumePage.route("https://checkout.stripe.com/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body>stripe-test-checkout</body></html>",
    }),
  );
}

function fulfillJson(body: unknown) {
  return async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  };
}

function guestSample() {
  return {
    ...MOCK_ANALYSIS,
    analysis_id: ANALYSIS_ID,
    sample_run: true,
    tax_profile: {
      ...MOCK_ANALYSIS.tax_profile,
      tax_year: 2026,
    },
  };
}

function wrongRun(id: string) {
  return {
    ...MOCK_ANALYSIS,
    analysis_id: id,
    sample_run: false,
    packet_unlocked: false,
    tax_profile: {
      ...MOCK_ANALYSIS.tax_profile,
      tax_year: 2024,
    },
  };
}

function savedRunAnalysis() {
  const analysis = {
    ...MOCK_ANALYSIS,
    analysis_id: "hist-saved-newer",
    sample_run: false,
    packet_unlocked: false,
    positions: [
      {
        ...MOCK_ANALYSIS.positions[0],
        symbol: "ZZSAVED",
      },
    ],
    suggestions: MOCK_ANALYSIS.suggestions.map((item) => ({
      ...item,
      symbol: "ZZSAVED",
    })),
    tax_profile: {
      ...MOCK_ANALYSIS.tax_profile,
      tax_year: 2025,
    },
  };
  const history = [
    {
      id: "hist-saved-newer",
      user_id: "test-user-123",
      filename: "portfolio_saved_newer.csv",
      uploaded_at: "2026-02-01T00:00:00Z",
      summary: MOCK_ANALYSIS.summary,
      positions_count: 1,
      total_market_value: 8500,
    },
  ];
  return { analysis, history };
}

async function armGuestLanding(
  page: Page,
  sample: ReturnType<typeof guestSample>,
): Promise<void> {
  await page.route("**/health", fulfillJson({ status: "ok" }));
  await page.route("**/auth/v1/**", fulfillJson({}));
  await page.route("**/api/**", fulfillJson([]));
  await page.route("**/api/portfolio/analyze*", fulfillJson(sample));
}

async function countCheckout(route: Route, bodies: string[]): Promise<void> {
  bodies.push(route.request().postData() || "");
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      checkout_url: "https://checkout.stripe.com/c/pay/cs_test_e2e_resume",
      session_id: "cs_test_e2e_resume",
      amount: 4900,
      stripe_mode: "test",
    }),
  });
}

async function armOriginalTabCheckout(page: Page, bodies: string[]): Promise<void> {
  await page.route("**/auth/v1/token*", fulfillJson(MOCK_SESSION));
  await page.route("**/auth/v1/user*", fulfillJson(MOCK_USER));
  await page.route("**/api/tax-profile*", fulfillJson(null));
  await page.route("**/api/portfolio/history*", fulfillJson([]));
  await page.route("**/api/year-close-packet/checkout", (route) =>
    countCheckout(route, bodies),
  );
  await page.route("https://checkout.stripe.com/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body>stripe-test-checkout</body></html>",
    }),
  );
}

async function publishSessionFromOtherTab(page: Page): Promise<void> {
  // supabase-js tells other tabs with BroadcastChannel, not the storage event.
  // setItem still writes the same session payload injectMockSession uses.
  await page.evaluate(() => {
    const authData = {
      access_token: "mock-access-token",
      token_type: "bearer",
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      refresh_token: "mock-refresh-token",
      user: {
        id: "test-user-123",
        email: "test@optionstaxhub.com",
        email_confirmed_at: "2025-01-01T00:00:00Z",
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
    const storageKeys = [
      "sb-vgrlucxqncajjdoaoctq-auth-token",
      "sb-127-auth-token",
    ];
    for (const storageKey of storageKeys) {
      localStorage.setItem(storageKey, JSON.stringify(authData));
      const channel = new BroadcastChannel(storageKey);
      channel.postMessage({ event: "SIGNED_IN", session: authData });
      channel.close();
    }
  });
}

async function armSavedRunResume(
  page: Page,
  saved: ReturnType<typeof savedRunAnalysis>,
  bodies: string[],
): Promise<void> {
  await setupMockAuth(page);
  await page.route("**/api/portfolio/history*", fulfillJson(saved.history));
  await page.route(
    "**/api/portfolio/analysis/**",
    fulfillJson({
      ...saved.history[0],
      result: saved.analysis,
    }),
  );
  await page.route("**/api/year-close-packet/checkout", (route) =>
    countCheckout(route, bodies),
  );
}

async function armResumeApis(
  page: Page,
  sample: ReturnType<typeof guestSample>,
  bodies: string[],
): Promise<void> {
  await page.route("**/health", fulfillJson({ status: "ok" }));
  await page.route("**/auth/v1/**", fulfillJson({}));
  await page.route("**/api/**", fulfillJson([]));
  await setupMockAuth(page);
  await setupMockAnalysis(page, sample);
  await page.route("**/api/portfolio/history*", fulfillJson([]));
  await page.route("**/api/year-close-packet/checkout", (route) =>
    countCheckout(route, bodies),
  );
  await page.route("https://checkout.stripe.com/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<html><body>stripe-test-checkout</body></html>",
    }),
  );
}
