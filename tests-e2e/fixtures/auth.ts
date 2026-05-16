/**
 * v1.44.3.2 — Shared auth helpers.
 *
 * Both the Next.js console and the legacy HTML console land their
 * JWT in an httpOnly cookie set by FastAPI's /api/auth/login.
 * Once the cookie is on the BrowserContext, every subsequent
 * navigation (Next or legacy) reuses it without re-prompting.
 *
 * Tests that need an authed page take the ``authedPage`` fixture
 * declared here. Tests that need to verify the unauthed flow
 * (login form, redirect on no-cookie, etc.) keep using the default
 * ``page`` fixture from @playwright/test.
 */
import { test as base, type Page, expect } from "@playwright/test";

export interface AuthCreds {
  email: string;
  password: string;
}

function readCreds(): AuthCreds {
  const email = process.env.TEST_EMAIL;
  const password = process.env.TEST_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "TEST_EMAIL + TEST_PASSWORD must be set in tests-e2e/.env " +
      "before running the suite. Copy .env.example and fill them in.",
    );
  }
  return { email, password };
}

/** Programmatic login via FastAPI's /api/auth/login. */
export async function loginViaApi(page: Page): Promise<void> {
  const creds = readCreds();
  const legacyBase =
    process.env.LEGACY_URL || "http://localhost:8000";

  const response = await page.request.post(`${legacyBase}/api/auth/login`, {
    data: { email: creds.email, password: creds.password },
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok()) {
    throw new Error(
      `Login failed for ${creds.email}: HTTP ${response.status()} ${response.statusText()}. ` +
      "Verify the credentials in tests-e2e/.env and that the FastAPI " +
      "console is reachable at LEGACY_URL.",
    );
  }
  // The cookie now lives on the request context. For browser
  // navigation we need to push it to the BrowserContext as well.
  const cookies = await page.request.storageState();
  await page.context().addCookies(cookies.cookies);
}

/** UI-driven login flow — exercises the /login form end-to-end. */
export async function loginViaUi(page: Page): Promise<void> {
  const creds = readCreds();
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(creds.email);
  await page.getByLabel(/contraseña|password/i).fill(creds.password);
  await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
  // The Next.js login pushes the user to /dashboard on success.
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 10_000,
  });
}

/**
 * Extended Playwright `test` that pre-authenticates via the API
 * before the user-supplied test body runs. Use this in any spec
 * whose precondition is "user is already logged in".
 */
export const test = base.extend<{ authedPage: Page }>({
  authedPage: async ({ page }, use) => {
    await loginViaApi(page);
    await use(page);
  },
});

export { expect };
