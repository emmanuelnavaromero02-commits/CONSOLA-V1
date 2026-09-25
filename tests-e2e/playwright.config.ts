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

export default defineConfig({
  testDir: "./specs",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : Number(process.env.E2E_WORKERS || "1"),
  forbidOnly: !!process.env.CI,
  reporter: [
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["list"],
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
      testMatch: /09-ux-mobile.*\.spec\.ts$/,
    },
  ],
});
