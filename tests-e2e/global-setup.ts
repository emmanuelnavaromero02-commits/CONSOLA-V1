/**
 * v1.44.3.2.1 — Playwright global setup.
 *
 * Logs in ONCE via the same-origin Next.js auth proxy, persists the
 * resulting BrowserContext storage state (cookies + localStorage) to
 * .auth/session.json, and lets every spec downstream inherit it via
 * playwright.config.ts ``use.storageState``.
 *
 * Why: the v1.44.3.2 sprint logged in per-test; that adds 1-2 s
 * to every spec AND triggers the rate limiter when the suite
 * grows past ~60 tests. Global setup runs the login exactly once
 * per `playwright test` invocation.
 *
 * Failure mode: if login fails here, EVERY spec downstream will
 * report "no authenticated session" — a single loud error at the
 * suite root is better than 200 cascading false-negatives.
 */
import { chromium, type FullConfig } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { loginViaApi } from "./fixtures/auth";

const AUTH_DIR = ".auth";
const STORAGE_STATE = path.join(AUTH_DIR, "session.json");

async function globalSetup(_config: FullConfig): Promise<void> {
  await mkdir(AUTH_DIR, { recursive: true });

  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    await loginViaApi(context.request);
    const state = await context.storageState();
    state.cookies = state.cookies.map((cookie) => {
      const domain = cookie.domain.replace(/^\./, "");
      if (domain === "localhost" || domain === "127.0.0.1" || domain === "::1") {
        return { ...cookie, secure: false };
      }
      return cookie;
    });
    await writeFile(STORAGE_STATE, JSON.stringify(state, null, 2));
    console.log(`[e2e:setup] storageState persisted → ${STORAGE_STATE}`);
  } finally {
    await browser.close();
  }
}

export default globalSetup;
