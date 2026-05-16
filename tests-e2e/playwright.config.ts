import { defineConfig, devices } from "@playwright/test";

/**
 * v1.44.3.2.1 — Deep-coverage E2E suite (200+ tests).
 *
 * Detection-only. Browser-driven runs happen on the developer's
 * Mac against a booted stack; sandbox CI just verifies the suite
 * collects.
 *
 * Key additions over v1.44.3.2:
 *   - globalSetup logs in ONCE via the Next.js form and persists
 *     storage state to .auth/session.json. Every spec downstream
 *     reuses the session (no per-test login = faster + dodges the
 *     auth rate limiter).
 *   - projects split: ``desktop-chromium`` (default 1280×720) +
 *     ``mobile-chromium`` (Pixel-5 viewport, used by 09-ux-mobile
 *     and the responsiveness checks).
 *   - Login + global-setup specs run WITHOUT pre-mounted state so
 *     they can exercise the unauth flow.
 */
export default defineConfig({
  testDir: "./specs",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: [
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["list"],
    // JSON reporter used by scripts/e2e-report-summary.sh for the
    // categorised post-run digest.
    ["json", { outputFile: "playwright-report/results.json" }],
  ],
  globalSetup: "./global-setup.ts",
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3000",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
    ignoreHTTPSErrors: true,
    actionTimeout: 8_000,
    navigationTimeout: 15_000,
    // EVERY spec downstream of the storageState directive inherits
    // the pre-authenticated context. Specs that need the unauth
    // surface (01-login-deep, the unauth API gate checks) explicitly
    // opt out via `test.use({ storageState: { cookies: [], origins: [] } })`.
    storageState: ".auth/session.json",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 5"] },
      // Mobile project only runs specs that opt in via test.describe
      // grep matchers. Today only 09-ux-mobile actually consumes it.
      testMatch: /09-ux-mobile.*\.spec\.ts$/,
    },
  ],
});
