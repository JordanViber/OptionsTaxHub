import { test, expect } from "@playwright/test";
import fs from "fs";

test("reproduce CORS and unregister SW", async ({ page, context }) => {
  const records: any[] = [];

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

  // Save records
  const out = JSON.stringify(records, null, 2);
  fs.writeFileSync("tests/e2e/cors-results.json", out);

  // Assert that at least one API response was received (not strictly asserting success)
  const responses = records.filter(
    (r) => r.type === "response" || r.type === "direct-fetch",
  );
  expect(responses.length).toBeGreaterThan(0);
});
