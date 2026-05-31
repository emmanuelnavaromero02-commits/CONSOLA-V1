import { defineConfig, devices } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

function loadLocalEnv(): void {
  for (const envPath of [
    path.resolve(process.cwd(), ".env"),
    path.resolve(process.cwd(), "tests-e2e/.env"),
  ]) {
    if (!existsSync(envPath)) continue;
    for (const rawLine of readFileSync(envPath, "utf8").split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) continue;
      const idx = line.indexOf("=");
      const key = line.slice(0, idx).trim();
      const value = line.slice(idx + 1).replace(/\s+#.*$/, "").trim();
      if (key && process.env[key] === undefined) {
        process.env[key] = value;
      }
    }
  }
}

loadLocalEnv();

/**
 * v1.44.3.2.1 — Deep-coverage E2E suite (200+ tests).
 *
 * Detection-only. Browser-driven runs happen on the developer's
 * Mac against a booted stack; sandbox CI just verifies the suite
 * collects.
 *
 * Key additions over v1.44.3.2:
  *   - globalSetup logs in ONCE via the FastAPI-served static form and persists
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
  // Several deep probes intentionally wait for the documented
  // 30 s dashboard polling interval plus buffer. Keep the global
  // timeout above those contracts or the runner, not the app,
  // becomes the source of failure.
  timeout: 45_000,
  expect: { timeout: 10_000 },
  // CI starts a fresh Docker stack on shared runners; allow one retry
  // so transient service/UI timing does not fail an otherwise healthy PR.
  retries: process.env.CI ? 1 : 0,
  // Legacy /studio specs exercise a shared backend/UI surface and are
  // not yet isolated enough for file-level parallelism. Keep the
  // release gate deterministic by default; developers can opt into a
  // faster exploratory run with E2E_WORKERS=2.
  workers: process.env.CI ? 1 : Number(process.env.E2E_WORKERS || "1"),
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
    baseURL: process.env.BASE_URL || "http://localhost:8000",
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
