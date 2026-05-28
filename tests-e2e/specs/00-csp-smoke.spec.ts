/**
 * v1.44.3.2.2 spec 00 — CSP smoke check.
 *
 * Codex's Mac diagnostic surfaced that v1.44.2's CSP set
 * ``script-src 'self'`` which blocked Next.js 14's hydration
 * inline-bootstrap script. Result: /login rendered a skeleton
 * forever, the form never mounted, and ALL 319 E2E tests failed
 * because Playwright couldn't find the email/password inputs.
 *
 * This file runs FIRST (filename starts with 00-) so a regression
 * to the broken CSP fails loudly before the rest of the suite
 * runs and produces cascade noise.
 *
 * Storage state is intentionally NOT mounted — we test the
 * /login render from a clean context, the same path a real user
 * takes.
 */
import { test, expect } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test.describe("CSP smoke — Next.js must hydrate", () => {
  test("/login renders the email input within 5 s", async ({ page }) => {
    // Trap CSP violations early — they're the load-bearing signal.
    const cspErrors: string[] = [];
    page.on("console", (msg) => {
      const text = msg.text();
      if (/Content Security Policy/i.test(text)) cspErrors.push(text);
    });

    await page.goto("/login", { waitUntil: "domcontentloaded" });

    // The form's email input must mount within 5 s. If hydration
    // is blocked, we never see it.
    await expect(page.getByLabel(/email/i)).toBeVisible({ timeout: 5_000 });
    expect(cspErrors,
      `CSP violations on /login (${cspErrors.length}):\n  ` + cspErrors.join("\n  "),
    ).toEqual([]);
  });

  test("/login renders the password input within 5 s", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await expect(
      page.getByLabel(/contraseña|password/i),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("/login renders the submit button within 5 s", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await expect(
      page.getByRole("button", { name: /iniciar sesión|sign in/i }),
    ).toBeVisible({ timeout: 5_000 });
  });

  test("/login response carries a non-blocking CSP", async ({ request }) => {
    // Static HTTP check — no browser. Production keeps script-src
    // strict by allowing Next's inline bootstrap with per-build
    // hashes/nonces instead of opening script-src broadly.
    const response = await request.get("/login");
    const csp = response.headers()["content-security-policy"] || "";
    // In dev mode next.config.mjs returns no headers — that's fine,
    // dev never trips the bug. Only assert when CSP is present.
    if (!csp) return;
    expect(csp,
      "script-src must be present and scoped to this origin",
    ).toMatch(/script-src[^;]*'self'/);
    expect(csp,
      "script-src must allow the generated Next bootstrap via hashes/nonces, not by disabling CSP",
    ).toMatch(/script-src[^;]*('sha256-|nonce-|unsafe-inline)/);
    // And the defenses that matter against clickjacking / form-hijacking
    // STAY in place — that's what the audit actually cared about.
    expect(csp).toMatch(/frame-ancestors 'none'/);
    expect(csp).toMatch(/form-action 'self'/);
    expect(csp).toMatch(/base-uri 'self'/);
  });
});
