/**
 * v1.44.3.2.2 R-Mac-4 — same-origin auth helpers.
 *
 * Static export dropped CORS and the Next.js proxy from the picture:
 * FastAPI serves the compiled console on the same origin as /auth/*
 * and /api/*.
 *
 * Wire-level flow — unchanged from R-Mac, just same-origin now:
 *
 *   1. GET  /login            → FastAPI /login
 *                              → Set-Cookie: csrf_token=...
 *   2. POST /auth/login      → FastAPI /auth/login
 *                              headers: Content-Type + X-CSRF-Token
 *                              cookies: csrf_token
 *                              body:    {email, password}
 *   3. Response 200          → cookies: mod_session + refresh_token
 *                              scoped to the FastAPI origin (no CORS
 *                              credentialed-request negotiation).
 *   4. Subsequent requests carry mod_session via the BrowserContext.
 *
 * BACKEND_URL is kept exported for direct backend probes. The login
 * flow itself goes through FRONTEND_URL, which defaults to BACKEND_URL.
 *
 * Credentials live in tests-e2e/.env (gitignored). The brief confirms
 * emmanuel@local.ai / Admin123! EXIST in the local-dev DB.
 */
import {
  test as base,
  expect,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";
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

const BACKEND_URL =
  process.env.LEGACY_URL || process.env.BACKEND_URL || "http://localhost:8000";
const FRONTEND_URL =
  process.env.BASE_URL || BACKEND_URL;

const TEST_EMAIL = process.env.TEST_EMAIL || "emmanuel@local.ai";
const TEST_PASSWORD = process.env.TEST_PASSWORD || "";

/**
 * Extract the csrf_token value from a Set-Cookie header (either a
 * single header string or an array). The backend sets the cookie
 * on every GET /login; we just need its value to echo back as
 * X-CSRF-Token + Cookie on the POST.
 */
function extractCsrfFromSetCookie(setCookie: string | string[] | undefined): string | null {
  if (!setCookie) return null;
  const haystack = Array.isArray(setCookie) ? setCookie.join("\n") : setCookie;
  const m = haystack.match(/csrf_token=([^;,\s]+)/);
  return m ? m[1] : null;
}

/**
 * Programmatic login via the discovered CSRF flow. Returns the
 * login HTTP response so callers can assert status / cookies.
 *
 * Both round-trips go to the FastAPI origin serving the static console.
 */
export async function loginViaApi(request: APIRequestContext) {
  // Step 1: seed the csrf_token cookie.
  const csrfPath = "/login";
  const csrfResponse = await request.get(`${FRONTEND_URL}${csrfPath}`);
  const csrfToken = extractCsrfFromSetCookie(
    csrfResponse.headers()["set-cookie"],
  );
  if (!csrfToken) {
    throw new Error(
      `csrf_token cookie not set by GET ${FRONTEND_URL}${csrfPath} ` +
      `(status=${csrfResponse.status()}). The login flow has drifted; ` +
      "verify the backend still seeds csrf_token on the login page render.",
    );
  }

  // Step 2: POST /auth/login (same origin) with the CSRF echo +
  // Cookie header.
  const loginResponse = await request.post(`${FRONTEND_URL}/auth/login`, {
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token":  csrfToken,
      "Cookie":        `csrf_token=${csrfToken}`,
    },
    data: { email: TEST_EMAIL, password: TEST_PASSWORD },
  });

  if (loginResponse.status() !== 200) {
    const body = await loginResponse.text();
    throw new Error(
      `POST ${FRONTEND_URL}/auth/login failed: HTTP ${loginResponse.status()}.\n` +
      `Body: ${body.slice(0, 300)}\n` +
      "Verify TEST_EMAIL + TEST_PASSWORD in tests-e2e/.env match a real " +
      "user in the local DB.",
    );
  }
  return loginResponse;
}

/**
 * UI login via the Next.js /login form. Kept for specs that need
 * to exercise the visible login screen; global setup uses
 * loginViaApi so suite auth does not depend on page-load timing.
 */
export async function loginViaBrowser(
  context: BrowserContext,
  baseURL: string = FRONTEND_URL,
): Promise<void> {
  const page = await context.newPage();
  try {
    await page.goto(`${baseURL}/login`, {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });
    await page.fill(
      'input[type="email"], input[name="email"], input#email',
      TEST_EMAIL,
    );
    await page.fill(
      'input[type="password"], input[name="password"], input#password',
      TEST_PASSWORD,
    );
    await page.click('button[type="submit"]');
    await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
      timeout: 15_000,
      waitUntil: "commit",
    });
  } finally {
    await page.close();
  }
}

/**
 * Extended Playwright `test` with an ``authedPage`` fixture. With
 * the global storageState mounted by playwright.config.ts, ``page``
 * is ALREADY authenticated, so authedPage is a thin alias that
 * documents intent — but we still re-export it so the v1.44.3.2
 * deep specs can keep their signatures while we transition.
 */
export const test = base.extend<{ authedPage: Page }>({
  authedPage: async ({ page }, use) => {
    await use(page);
  },
});

export { expect };
export const CREDENTIALS = {
  email:    TEST_EMAIL,
  password: TEST_PASSWORD,
  backend:  BACKEND_URL,
  frontend: FRONTEND_URL,
};
