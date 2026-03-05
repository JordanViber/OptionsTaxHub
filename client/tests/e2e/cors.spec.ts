import { test, expect } from "@playwright/test";
import path from "path";
import { writeFileSync, mkdirSync } from "fs";

interface CorsRecord {
  type: "request" | "response" | "direct-fetch" | "direct-fetch-error";
  url?: string;
  method?: string;
  status?: number;
  headers?: Record<string, string>;
  endpoint?: string;
  error?: string;
}

test("reproduce CORS and unregister SW", async ({ page, context }, testInfo) => {
  const records: CorsRecord[] = [];

  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/api/")) {
      records.push({ type: "request", url, method: req.method() });
    }
  });

  page.on("response", async (res) => {
    const url = res.url();
    if (url.includes("/api/")) {
      const headers = await res.allHeaders();
      records.push({ type: "response", url, status: res.status(), headers });
    }
  });

  const BASE = process.env.BASE_URL || "http://localhost:3000";
  // Navigate to dashboard
  await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle" });

  // Unregister service workers and clear caches
  await page.evaluate(async () => {
    if ("serviceWorker" in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      for (const r of regs) {
        await r.unregister();
      }
    }
    if ("caches" in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
    // also clear localStorage/sessionStorage
    try {
      localStorage.clear();
      sessionStorage.clear();
    } catch (e) {}
  });

  // Reload and wait
  await page.reload({ waitUntil: "networkidle" });

  // Wait a short time for any API calls
  await page.waitForTimeout(2000);

  // Query specific endpoints directly from the page to capture responses
  const endpoints = ["/api/tax-profile", "/api/portfolio/history"];

  for (const ep of endpoints) {
    try {
      const res = await page.evaluate(async (e) => {
        const r = await fetch(e, { method: "GET", credentials: "include" });
        const headers: any = {};
        r.headers.forEach((v, k) => (headers[k] = v));
        return { url: location.origin + e, status: r.status, headers };
      }, ep);
      records.push({ type: "direct-fetch", ...res });
    } catch (err) {
      records.push({
        type: "direct-fetch-error",
        endpoint: ep,
        error: String(err),
      });
    }
  }

  // Save records to Playwright's per-test output directory (not source tree)
  const outputPath = testInfo.outputPath("cors-results.json");
  mkdirSync(path.dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, JSON.stringify(records, null, 2));
  await testInfo.attach("cors-results", {
    path: outputPath,
    contentType: "application/json",
  });

  // Assert deterministic CORS behavior for the known endpoints we fetched explicitly
  const directFetches = records.filter((r) => r.type === "direct-fetch");
  // We expect one successful direct-fetch per endpoint
  expect(directFetches).toHaveLength(endpoints.length);

  for (const fetchRecord of directFetches) {
    // Basic sanity check on HTTP status (auth errors 401/403 are expected without a session)
    expect(fetchRecord.status).toBeGreaterThanOrEqual(200);
    expect(fetchRecord.status).toBeLessThan(500);

    const acao =
      fetchRecord.headers?.["access-control-allow-origin"] ??
      fetchRecord.headers?.["Access-Control-Allow-Origin"];

    // CORS should either be open or allow our origin/BASE
    if (acao !== undefined) {
      expect(
        acao === "*" ||
          acao === BASE ||
          acao === `${BASE}/` ||
          acao === new URL(BASE).origin,
      ).toBeTruthy();
    }
  }
});
