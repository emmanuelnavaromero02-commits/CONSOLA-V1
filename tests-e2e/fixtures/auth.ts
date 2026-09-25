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

function extractCsrfFromSetCookie(setCookie: string | string[] | undefined): string | null {
  if (!setCookie) return null;
  const haystack = Array.isArray(setCookie) ? setCookie.join("\n") : setCookie;
  const m = haystack.match(/csrf_token=([^;,\s]+)/);
  return m ? m[1] : null;
}

export async function loginViaApi(request: APIRequestContext) {
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
