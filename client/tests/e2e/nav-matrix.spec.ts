import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import { MOCK_ANALYSIS, injectMockSession, setupMockAuth } from "./fixtures";

const ARTIFACTS = "/opt/cursor/artifacts/nav";

const APP_PAGES = [
  { slug: "dashboard", path: "/dashboard" },
  { slug: "options", path: "/options" },
  { slug: "not-found", path: "/this-route-does-not-exist" },
] as const;

const VIEWPORTS = [
  { name: "1280", width: 1280, height: 800 },
  { name: "900", width: 900, height: 800 },
  { name: "768", width: 768, height: 800 },
  { name: "390", width: 390, height: 844 },
] as const;

async function mockGuest(page: Page) {
  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    }),
  );
  await page.route("**/auth/v1/**", (route) => {
    const url = route.request().url();
    if (
      url.includes("/auth/v1/user") ||
      url.includes("/auth/v1/token") ||
      url.includes("/auth/v1/logout")
    ) {
      return route.fallback();
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    });
  });
  await page.route("**/api/oth/options/rh-status*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
}

async function shoot(page: Page, name: string, height = 180) {
  if (test.info().project.name !== "chromium") {
    return;
  }
  fs.mkdirSync(ARTIFACTS, { recursive: true });
  const width = page.viewportSize()?.width ?? 1280;
  await page.screenshot({
    path: `${ARTIFACTS}/${name}.png`,
    clip: { x: 0, y: 0, width, height: Math.min(height, page.viewportSize()?.height ?? height) },
  });
}

test.describe("nav matrix", () => {
  for (const auth of ["guest", "signed-in"] as const) {
    for (const viewport of VIEWPORTS) {
      test(`${auth} headers match at ${viewport.name}`, async ({ page }) => {
        await page.setViewportSize({
          width: viewport.width,
          height: viewport.height,
        });
        await mockGuest(page);
        if (auth === "signed-in") {
          await setupMockAuth(page);
          await injectMockSession(page);
        }

        const actionSets: string[] = [];
        const compact = viewport.width <= 900;

        for (const entry of APP_PAGES) {
          await page.goto(entry.path);
          const header = page.getByTestId("app-header");
          await expect(header).toBeVisible({ timeout: 15000 });
          await expect(
            header.getByRole("link", { name: "OptionsTaxHub" }),
          ).toHaveAttribute("href", "/");

          const nav = page.getByRole("navigation", { name: "Primary" });
          const tax = nav.getByRole("link", { name: "Tax", exact: true });
          const options = nav.getByRole("link", { name: "Options", exact: true });
          await expect(tax).toBeVisible();
          await expect(options).toBeVisible();
          await expect(tax).toHaveAttribute("href", "/dashboard");
          await expect(options).toHaveAttribute("href", "/options");
          await expect(page.getByText("Open Tax")).toHaveCount(0);
          await expect(page.getByRole("button", { name: /^Settings$/ })).toHaveCount(0);
          if (compact) {
            await expect(header.getByTestId("header-menu")).toBeVisible();
            await expect(header.getByTestId("header-actions")).toHaveCount(0);
          } else {
            await expect(header.getByTestId("header-menu")).toHaveCount(0);
            await expect(header.getByTestId("header-actions")).toBeVisible();
          }
          const headerOverflow = await header.evaluate(
            (el) => el.scrollWidth - el.clientWidth,
          );
          expect(headerOverflow).toBeLessThanOrEqual(1);

          if (entry.path === "/dashboard") {
            await expect(tax).toHaveAttribute("aria-current", "page");
          }
          if (entry.path === "/options") {
            await expect(options).toHaveAttribute("aria-current", "page");
            await expect(page.getByTestId("entry-analysis-panel")).toBeVisible();
          }
          if (entry.slug === "not-found") {
            await expect(page.getByTestId("not-found")).toBeVisible();
          }

          await shoot(page, `${auth}-${entry.slug}-${viewport.name}-header`);

          if (compact) {
            await header.getByRole("button", { name: "Menu" }).click();
            await expect(page.locator(".MuiMenu-paper")).toHaveCSS("opacity", "1");
            if (entry.path === "/dashboard") {
              await expect(
                page.getByRole("menuitem", { name: "Tax (current)" }),
              ).toBeVisible();
            }
            if (entry.path === "/options") {
              await expect(
                page.getByRole("menuitem", { name: "Options (current)" }),
              ).toBeVisible();
            }
            await shoot(page, `${auth}-${entry.slug}-${viewport.name}-menu`, 520);
          }

          await expect(page.getByTestId("header-upload")).toBeVisible();
          await expect(page.getByTestId("header-saved-runs")).toBeVisible();
          if (auth === "signed-in") {
            await expect(page.getByTestId("header-account")).toBeVisible();
            await expect(page.getByTestId("header-sign-in")).toHaveCount(0);
            actionSets.push("Upload|Saved runs|Account");
          } else {
            await expect(page.getByTestId("header-sign-in")).toBeVisible();
            await expect(page.getByTestId("header-account")).toHaveCount(0);
            const signIn = page.getByTestId("header-sign-in");
            const href = await signIn.getAttribute("href");
            expect(href).toContain("next=");
            actionSets.push("Upload|Saved runs|Sign in");
          }

          if (compact) {
            await page.keyboard.press("Escape");
          }
        }

        expect(new Set(actionSets).size).toBe(1);

        await page.goto("/");
        const header = page.getByTestId("app-header");
        await expect(header.getByRole("link", { name: "OptionsTaxHub" })).toHaveAttribute(
          "href",
          "/",
        );
        await expect(
          page.getByRole("navigation", { name: "Primary" }).getByRole("link", {
            name: "Tax",
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByRole("navigation", { name: "Primary" }).getByRole("link", {
            name: "Options",
            exact: true,
          }),
        ).toBeVisible();
        if (compact) {
          await header.getByRole("button", { name: "Menu" }).click();
          await expect(page.locator(".MuiMenu-paper")).toHaveCSS("opacity", "1");
        }
        await expect(page.getByTestId("header-try-sample")).toBeVisible();
        if (auth === "guest") {
          await expect(page.getByTestId("header-sign-in")).toHaveAttribute(
            "href",
            "/auth/signin?next=/",
          );
        } else {
          await expect(page.getByTestId("header-account")).toBeVisible();
        }
        await expect(page.getByText("Open Tax")).toHaveCount(0);
        await shoot(
          page,
          `${auth}-landing-${viewport.name}${compact ? "-menu" : "-header"}`,
          compact ? 520 : 180,
        );
        if (compact) {
          await page.keyboard.press("Escape");
        }

        await page.goto("/auth/signin");
        await expect(
          page.getByTestId("app-header").getByRole("link", { name: "OptionsTaxHub" }),
        ).toHaveAttribute("href", "/");
        await expect(
          page.getByRole("navigation", { name: "Primary" }).getByRole("link", {
            name: "Tax",
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByRole("navigation", { name: "Primary" }).getByRole("link", {
            name: "Options",
            exact: true,
          }),
        ).toBeVisible();
        await shoot(page, `${auth}-signin-${viewport.name}-header`);
      });

      test(`${auth} shell on the remaining pages at ${viewport.name}`, async ({
        page,
      }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({
          width: viewport.width,
          height: viewport.height,
        });
        await mockGuest(page);
        if (auth === "signed-in") {
          await setupMockAuth(page);
          await injectMockSession(page);
        }
        const compact = viewport.width <= 900;

        const assertChrome = async (kind: "app" | "auth") => {
          const header = page.getByTestId("app-header");
          await expect(header).toBeVisible({ timeout: 15000 });
          await expect(
            header.getByRole("link", { name: "OptionsTaxHub" }),
          ).toHaveAttribute("href", "/");
          const nav = page.getByRole("navigation", { name: "Primary" });
          await expect(nav.getByRole("link", { name: "Tax", exact: true })).toBeVisible();
          await expect(
            nav.getByRole("link", { name: "Options", exact: true }),
          ).toBeVisible();
          await expect(nav.getByRole("link", { name: "Tax", exact: true })).toHaveAttribute(
            "href",
            "/dashboard",
          );
          await expect(
            nav.getByRole("link", { name: "Options", exact: true }),
          ).toHaveAttribute("href", "/options");
          if (kind === "auth") {
            await expect(header.getByTestId("header-menu")).toHaveCount(0);
            await expect(header.getByTestId("header-actions")).toHaveCount(0);
          } else if (compact) {
            await expect(header.getByTestId("header-menu")).toBeVisible();
            await expect(header.getByTestId("header-actions")).toHaveCount(0);
            await header.getByRole("button", { name: "Menu" }).click();
            await expect(page.locator(".MuiMenu-paper")).toHaveCSS("opacity", "1");
          } else {
            await expect(header.getByTestId("header-menu")).toHaveCount(0);
            await expect(header.getByTestId("header-actions")).toBeVisible();
          }
          const headerOverflow = await header.evaluate(
            (el) => el.scrollWidth - el.clientWidth,
          );
          expect(headerOverflow).toBeLessThanOrEqual(1);
          if (kind === "auth") {
            return;
          }
          await expect(page.getByTestId("header-upload")).toBeVisible();
          await expect(page.getByTestId("header-saved-runs")).toBeVisible();
          if (auth === "signed-in") {
            await expect(page.getByTestId("header-account")).toBeVisible();
          } else {
            await expect(page.getByTestId("header-sign-in")).toBeVisible();
          }
          if (compact) {
            await page.keyboard.press("Escape");
          }
        };

        if (auth === "guest") {
          await page.goto("/settings");
          await expect(page).toHaveURL(/\/auth\/signin\?next=\/settings/, {
            timeout: 15000,
          });
          await assertChrome("auth");
        } else {
          await page.goto("/settings");
          await expect(
            page.getByRole("heading", { name: "Tax settings", exact: true }),
          ).toBeVisible({ timeout: 15000 });
          await assertChrome("app");
        }

        await page.goto("/privacy");
        await expect(page.getByRole("heading", { name: "Privacy" })).toBeVisible();
        await assertChrome("app");

        await page.goto("/auth/signup");
        await expect(page.getByRole("heading", { name: "Create Account" })).toBeVisible();
        await assertChrome("auth");

        await page.goto("/auth/confirm-email");
        await assertChrome("auth");

        await page.goto("/auth/reset-password");
        await assertChrome("auth");

        await page.goto("/tips/success");
        await expect(page.getByText(/Thank You/)).toBeVisible();
        await assertChrome("app");

        await page.goto("/tips/cancel");
        await expect(page.getByText("No Worries!")).toBeVisible();
        await assertChrome("app");

        await shoot(page, `${auth}-shell-${viewport.name}-header`);
      });
    }
  }

  test("landing CTAs open the sample, upload, and Options", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mockGuest(page);
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_ANALYSIS,
          sample_run: true,
          tax_profile: { ...MOCK_ANALYSIS.tax_profile, tax_year: 2026 },
        }),
      }),
    );

    await page.goto("/");
    const header = page.getByTestId("app-header");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await expect(nav.getByRole("link", { name: "Tax", exact: true })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    await expect(
      nav.getByRole("link", { name: "Options", exact: true }),
    ).toHaveAttribute("href", "/options");
    await expect(header.getByTestId("header-sign-in")).toHaveAttribute(
      "href",
      "/auth/signin?next=/",
    );
    await expect(page.getByRole("link", { name: "See what's kept" })).toHaveAttribute(
      "href",
      "/auth/signin?next=/",
    );
    const footer = page.getByRole("contentinfo");
    await expect(footer.getByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/auth/signin?next=/",
    );
    await expect(footer.getByRole("link", { name: "Tax", exact: true })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    await expect(footer.getByRole("link", { name: "Privacy" })).toHaveAttribute(
      "href",
      "/privacy",
    );

    await header.getByTestId("header-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
    await expect(page.getByText("Tax Year: 2026")).toBeVisible({ timeout: 20000 });

    await page.goto("/");
    await page.getByRole("button", { name: "Open the 2026 sample" }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
    await expect(page.getByText("Tax Year: 2026")).toBeVisible({ timeout: 20000 });

    await page.goto("/");
    await page.getByRole("button", { name: "I have a CSV" }).click();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByTestId("csv-dropzone")).toBeVisible();

    await page.goto("/");
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", {
      name: "Options",
      exact: true,
    }).click();
    await expect(page).toHaveURL(/\/options/);
    await expect(page.getByTestId("entry-guest-signin")).toBeVisible();

    await page.goto("/");
    await page.getByRole("link", { name: "Open Options" }).click();
    await expect(page).toHaveURL(/\/options/);
    await expect(page.getByTestId("entry-guest-signin")).toHaveAttribute(
      "href",
      "/auth/signin?next=/options",
    );
  });

  test("guest prompts keep the page they came from", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mockGuest(page);

    await page.goto("/options");
    await expect(page.getByTestId("entry-analysis-panel")).toBeVisible({
      timeout: 15000,
    });
    await page.getByTestId("header-saved-runs").click();
    await expect(page).toHaveURL(/\/auth\/signin\?next=\/options&reason=runs/);
    await expect(page.getByRole("link", { name: "Sign up" })).toHaveAttribute(
      "href",
      "/auth/signup?next=/options",
    );

    await page.goto("/options");
    await page.getByTestId("header-upload").click();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByTestId("csv-dropzone")).toBeVisible();

    await page.goto("/dashboard");
    await page.getByTestId("tax-settings-link").click();
    await expect(page).toHaveURL(
      /\/auth\/signin\?next=\/dashboard&reason=profile/,
    );
    await expect(page.getByRole("link", { name: "Sign up" })).toHaveAttribute(
      "href",
      "/auth/signup?next=/dashboard",
    );

    await page.goto("/settings");
    await expect(page).toHaveURL(/\/auth\/signin\?next=\/settings/, {
      timeout: 15000,
    });
    await expect(page.getByRole("link", { name: "Sign up" })).toHaveAttribute(
      "href",
      "/auth/signup?next=/settings",
    );
  });

  test("signed-in saved runs and tax settings leave Options for the right page", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await mockGuest(page);
    await setupMockAuth(page);
    await injectMockSession(page);

    await page.goto("/options");
    await expect(page.getByTestId("entry-analysis-panel")).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByRole("button", { name: /^Settings$/ })).toHaveCount(0);
    await page.getByTestId("header-saved-runs").click();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Saved runs" })).toBeVisible({
      timeout: 15000,
    });

    await page.goto("/options");
    await page.getByTestId("header-account").click();
    await page.getByTestId("account-tax-settings").click();
    await expect(page).toHaveURL(/\/settings/);
    await expect(
      page.getByRole("heading", { name: "Tax settings", exact: true }),
    ).toBeVisible();
  });

  test("390 menu still reaches the sample in one labeled step", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockGuest(page);
    await page.route("**/api/portfolio/analyze*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_ANALYSIS,
          sample_run: true,
          tax_profile: { ...MOCK_ANALYSIS.tax_profile, tax_year: 2026 },
        }),
      }),
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Menu" }).click();
    await page.getByTestId("header-try-sample").click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
    await expect(page.getByText("Tax Year: 2026")).toBeVisible({ timeout: 20000 });
  });
});
