/**
 * v1.44.3.2.2 R-Mac-4 — same-origin auth helpers.
 *
 * R-Mac-4 dropped CORS from the picture entirely: the Next.js
 * app at :3000 serves /login-proxy, /auth/login and /api/* as
 * itself, then proxies each call to FastAPI server-to-server
 * via the docker network (see console-next/src/lib/proxy.ts).
 *
 * Wire-level flow — unchanged from R-Mac, just same-origin now:
 *
 *   1. GET  /login-proxy     → Next proxy → FastAPI /login
 *                              → Set-Cookie: csrf_token=…
 *   2. POST /auth/login      → Next proxy → FastAPI /auth/login
 *                              headers: Content-Type + X-CSRF-Token
 *                              cookies: csrf_token
 *                              body:    {email, password}
 *   3. Response 200          → cookies: mod_session + refresh_token
 *                              scoped to the :3000 origin (no CORS
 *                              credentialed-request negotiation).
 *   4. Subsequent requests carry mod_session via the BrowserContext.
 *
 * BACKEND_URL is kept exported only for tests that still want to
 * probe the FastAPI backend directly (debug helpers, health
 * checks). The login flow itself goes through FRONTEND_URL.
 *
 * Credentials live in tests-e2e/.env (gitignored) — see
 * .env.example for the documented defaults. The brief confirms
 * emmanuel@local.ai / omega2026 EXIST in the local-dev DB.
 */
import {
  test as base,
  expect,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const BACKEND_URL =
  process.env.LEGACY_URL || process.env.BACKEND_URL || "http://localhost:8000";
const FRONTEND_URL =
  process.env.BASE_URL || "http://localhost:3000";

const TEST_EMAIL = process.env.TEST_EMAIL || "emmanuel@local.ai";
const TEST_PASSWORD = process.env.TEST_PASSWORD || "omega2026";

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
 * v1.44.3.2.2 R-Mac-4: both round-trips go through the Next.js
 * same-origin proxy (FRONTEND_URL) so they exercise the exact
 * code path the browser uses in production. The proxy forwards
 * to FastAPI over the docker network; the cookies that come
 * back are scoped to :3000 (matching what the browser actually
 * stores when a real user logs in via the UI).
 */
export async function loginViaApi(request: APIRequestContext) {
  // Step 1: GET /login-proxy to seed the csrf_token cookie.
  // `/login-proxy` (not `/login`) because the Next.js app owns
  // a client-rendered /login page; the proxy route lives under
  // a non-colliding path.
  const csrfResponse = await request.get(`${FRONTEND_URL}/login-proxy`);
  const csrfToken = extractCsrfFromSetCookie(
    csrfResponse.headers()["set-cookie"],
  );
  if (!csrfToken) {
    throw new Error(
      `csrf_token cookie not set by GET ${FRONTEND_URL}/login-proxy ` +
      `(status=${csrfResponse.status()}). The login flow has drifted; ` +
      "verify the backend still seeds csrf_token on the login page render " +
      "AND that the Next.js /login-proxy route is forwarding Set-Cookie.",
    );
  }

  // Step 2: POST /auth/login (same origin) with the CSRF echo +
  // Cookie header. Next.js forwards to FastAPI /auth/login.
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
      "user in the local DB AND that the Next.js /auth/[...path] proxy " +
      "is forwarding to the FastAPI backend.",
    );
  }
  return loginResponse;
}

/**
 * UI login via the Next.js /login form. Used by global-setup to
 * mint the BrowserContext storage state every spec downstream
 * inherits.
 */
export async function loginViaBrowser(
  context: BrowserContext,
  baseURL: string = FRONTEND_URL,
): Promise<void> {
  const page = await context.newPage();
  try {
    await page.goto(`${baseURL}/login`);
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
