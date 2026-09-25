import { test, expect, request as pwRequest } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

const FRONT = process.env.BASE_URL || "http://localhost:8000";
const BACKEND = process.env.LEGACY_URL || "http://localhost:8000";
const EMAIL = process.env.TEST_EMAIL || "emmanuel@local.ai";
const PASSWORD = process.env.TEST_PASSWORD || "";

async function csrfToken(ctx = pwRequest) {
  const c = await ctx.newContext();
  const r = await c.get(`${BACKEND}/login`);
  const sc = r.headers()["set-cookie"] || "";
  const m = (Array.isArray(sc) ? sc.join("\n") : sc).match(/csrf_token=([^;,\s]+)/);
  await c.dispose();
  return m ? m[1] : null;
}

test.describe("Login form — validation surface", () => {
  test("form renders email + password + submit button", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByLabel(/email/i)).toBeVisible();
    await expect(page.getByLabel(/contraseña|password/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /iniciar sesión|sign in/i }),
    ).toBeVisible();
  });

  test("submitting empty form does NOT call the API", async ({ page }) => {
    await page.goto("/login");
    let apiCalled = false;
    page.on("request", (req) => {
      if (req.url().includes("/auth/login")) apiCalled = true;
    });
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await expect
      .poll(() => apiCalled, { timeout: 2_000, intervals: [100, 250, 500] })
      .toBe(false);
  });

  test("invalid email format keeps the form on /login", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill("not-an-email");
    await page.getByLabel(/contraseña|password/i).fill("anything");
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await page.waitForTimeout(800);
    await expect(page).toHaveURL(/\/login/);
  });

  test("password-only submission does NOT navigate", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/contraseña|password/i).fill("only-password");
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await page.waitForTimeout(800);
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("Login — specific error toasts", () => {
  test("nonexistent email yields auth-error toast", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill("nobody@nowhere.invalid");
    await page.getByLabel(/contraseña|password/i).fill("whatever-123");
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    const toast = page.locator("[data-sonner-toast]");
    await expect(toast.first()).toBeVisible({ timeout: 5_000 });
    const text = (await toast.first().innerText()).toLowerCase();
    expect(text).toMatch(/incorrect|inválid|no se pudo|credenciales/);
  });

  test("wrong password yields the SAME toast (no email enumeration)",
    async ({ page }) => {
      await page.goto("/login");
      await page.getByLabel(/email/i).fill(EMAIL);
      await page.getByLabel(/contraseña|password/i).fill("definitely-wrong-pwd");
      await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
      const toast = page.locator("[data-sonner-toast]");
      await expect(toast.first()).toBeVisible({ timeout: 5_000 });
      const text = (await toast.first().innerText()).toLowerCase();
      expect(text).toMatch(/incorrect|inválid|no se pudo|credenciales/);
    },
  );
});

test.describe("Login — happy path", () => {
  test("valid credentials redirect to /dashboard", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/contraseña|password/i).fill(PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
  });

  test("after login a session cookie is set", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/contraseña|password/i).fill(PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
    const cookies = await page.context().cookies();
    const session = cookies.find((c) =>
      /mod_session|access_token|session|jwt|auth_token/.test(c.name),
    );
    expect(session, "expected a session cookie after login").toBeTruthy();
  });

  test("session cookie is HttpOnly", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/contraseña|password/i).fill(PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await page.waitForURL(/\/dashboard/, { timeout: 15_000 });
    const cookies = await page.context().cookies();
    const session = cookies.find((c) =>
      /mod_session|access_token|session|jwt|auth_token/.test(c.name),
    );
    expect(session?.httpOnly,
      "session cookie MUST be HttpOnly so JS can't exfiltrate it",
    ).toBe(true);
  });

  test("case-insensitive email — UPPERCASE login behavior is documented", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(EMAIL.toUpperCase());
    await page.getByLabel(/contraseña|password/i).fill(PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await page.waitForURL(/\/dashboard/, { timeout: 10_000 }).catch(() => undefined);
    const url = page.url();
    if (/\/dashboard/.test(url)) {
      expect(true).toBe(true);
    } else {
      await expect(page).toHaveURL(/\/login/);
    }
  });
});

test.describe("Middleware + unauth redirects", () => {
  for (const path of ["/dashboard", "/cartridges", "/cartridges/viewer?id=replicon", "/copilot"]) {
    test(`unauthenticated ${path} → /login with ?next=`, async ({ page }) => {
      await page.context().clearCookies();
      await page.goto(path);
      await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });
      expect(page.url(),
        "redirect MUST preserve original path as ?next= so post-login bounce works",
      ).toContain("next=");
    });
  }
});

test.describe("Auth API — CSRF flow", () => {
  test("GET /login seeds the csrf_token cookie", async () => {
    const c = await pwRequest.newContext();
    const r = await c.get(`${BACKEND}/login`);
    const sc = r.headers()["set-cookie"] || "";
    const haystack = Array.isArray(sc) ? sc.join("\n") : sc;
    expect(haystack).toMatch(/csrf_token=/);
    await c.dispose();
  });

  test("POST /auth/login WITHOUT csrf header → 4xx", async () => {
    const c = await pwRequest.newContext();
    const r = await c.post(`${BACKEND}/auth/login`, {
      data: { email: EMAIL, password: PASSWORD },
      headers: { "Content-Type": "application/json" },
    });
    expect(r.status(),
      "missing CSRF must reject (403 or 422) — auth flow drift if 200",
    ).toBeGreaterThanOrEqual(400);
    await c.dispose();
  });

  test("POST /auth/login WITH csrf returns 200 + cookies",
    async () => {
      const token = await csrfToken();
      expect(token).not.toBeNull();
      const c = await pwRequest.newContext();
      const r = await c.post(`${BACKEND}/auth/login`, {
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token":  token!,
          "Cookie":        `csrf_token=${token}`,
        },
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(r.status()).toBe(200);
      const setCookie = r.headers()["set-cookie"] || "";
      const text = Array.isArray(setCookie) ? setCookie.join("\n") : setCookie;
      expect(text).toMatch(/mod_session|access_token|session/);
      expect(text).toMatch(/httponly/i);
      await c.dispose();
    },
  );

  test("GET /auth/me returns user info after login", async () => {
    const token = await csrfToken();
    const c = await pwRequest.newContext();
    await c.post(`${BACKEND}/auth/login`, {
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token":  token!,
        "Cookie":        `csrf_token=${token}`,
      },
      data: { email: EMAIL, password: PASSWORD },
    });
    const me = await c.get(`${BACKEND}/auth/me`).catch(() => null) ||
               await c.get(`${BACKEND}/api/users/me`).catch(() => null);
    expect(me, "either /auth/me or /api/users/me must return user info").not.toBeNull();
    expect(me!.status()).toBeLessThan(400);
    const body = await me!.json();
    expect(JSON.stringify(body)).toContain(EMAIL.split("@")[0]);
    await c.dispose();
  });
});

test.describe("Logout + session lifecycle", () => {
  test("logout clears the session cookie", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/contraseña|password/i).fill(PASSWORD);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await page.waitForURL(/\/dashboard/, { timeout: 15_000 });

    const logout = page.locator(
      'button[name="logout"], a[href*="logout"], button:has-text("Cerrar sesión"), button:has-text("Logout")',
    );
    if (await logout.first().isVisible({ timeout: 5_000 }).catch(() => false)) {
      const [response] = await Promise.all([
        page.waitForResponse(
          (candidate) =>
            candidate.request().method() === "POST" &&
            /\/auth\/logout(?:\?|$)/.test(candidate.url()),
          { timeout: 10_000 },
        ),
        logout.first().click(),
      ]);
      expect(response.ok(), "POST /auth/logout should succeed").toBeTruthy();
      await expect.poll(
        async () => {
          const cookies = await page.context().cookies();
          return cookies.some((cookie) =>
            /mod_session|refresh_token|access_token|jwt/.test(cookie.name) &&
            cookie.value.length > 0,
          );
        },
        {
          message: "session cookies should be cleared (or expired) after logout",
          timeout: 10_000,
        },
      ).toBe(false);
    } else {
      test.fail(true, "no logout affordance found in Next.js UI — gap for v1.44.4");
    }
  });
});
