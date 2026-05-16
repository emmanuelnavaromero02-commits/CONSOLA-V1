/**
 * v1.44.3.2 spec 01 — Next.js login flow.
 *
 * The login page is the gate to everything else. If any of these
 * tests fails the rest of the Next.js suite is uninformative
 * (every spec downstream piggy-backs on a working login).
 *
 * v1.44.3.2.1: explicitly resets storageState because the global
 * setup pre-authenticates every spec by default; this one needs
 * to exercise the unauth surface.
 */
import { test, expect } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Login page (Next.js, /login)", () => {
  test("renders email + password fields + submit button", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByLabel(/email/i)).toBeVisible();
    await expect(page.getByLabel(/contraseña|password/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /iniciar sesión|sign in/i }),
    ).toBeVisible();
  });

  test("invalid credentials surface an error toast", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill("nobody@invalid.local");
    await page.getByLabel(/contraseña|password/i).fill("wrong-password-xx");
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    // The page must NOT navigate away on a failed login.
    await page.waitForTimeout(2_000);
    await expect(page).toHaveURL(/\/login(\?|$)/);
    // The error surface is a sonner toast — text varies by status,
    // but sonner injects a <li data-sonner-toast="…"> on render.
    const toast = page.locator("[data-sonner-toast]");
    await expect(toast.first()).toBeVisible({ timeout: 5_000 });
  });

  test("valid credentials redirect to /dashboard", async ({ page }) => {
    const email = process.env.TEST_EMAIL;
    const password = process.env.TEST_PASSWORD;
    test.skip(
      !email || !password,
      "TEST_EMAIL + TEST_PASSWORD must be set in tests-e2e/.env",
    );
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(email!);
    await page.getByLabel(/contraseña|password/i).fill(password!);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 });
  });

  test("unauthenticated access to /dashboard redirects to /login", async ({
    page,
  }) => {
    // Clear any cookies first to guarantee unauth state.
    await page.context().clearCookies();
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login(\?|$)/, { timeout: 10_000 });
    // The redirect must preserve the original path as ``?next=``
    // so the post-login bounce lands on /dashboard.
    expect(page.url()).toContain("next=");
  });
});
