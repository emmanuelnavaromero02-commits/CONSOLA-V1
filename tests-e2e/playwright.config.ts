import { defineConfig } from "@playwright/test";

/**
 * v1.44.3.2 — OMEGA E2E test suite.
 *
 * Detection-only sprint: every test under specs/ is meant to FAIL
 * loudly when the corresponding surface is broken. Bug fixes follow
 * in v1.44.3.3 after the developer reviews the HTML report.
 *
 * Reporter combo:
 *   - 'list'  → live progress in the terminal
 *   - 'html'  → static report at playwright-report/index.html with
 *               screenshots + videos + traces of every failure.
 *
 * Workers = 1 because some tests share the same authenticated
 * session storage and a few legacy console pages serialise on
 * shared global state.
 */
export default defineConfig({
  testDir: "./specs",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  // Fail the run if it accidentally accumulated only.skip / only
  // calls — those are easy to forget after debugging.
  forbidOnly: !!process.env.CI,
  reporter: [
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["list"],
  ],
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3000",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
    // The legacy console issues HTML cookies; the Next.js console
    // does too. Both share localhost so a single context can talk
    // to either.
    ignoreHTTPSErrors: true,
    actionTimeout: 8_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
