import { test, expect } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test.describe("CSP smoke — Next.js must hydrate", () => {
  test("/login renders the email input within 5 s", async ({ page }) => {
    const cspErrors: string[] = [];
    page.on("console", (msg) => {
      const text = msg.text();
      if (/Content Security Policy/i.test(text)) cspErrors.push(text);
    });

    await page.goto("/login", { waitUntil: "domcontentloaded" });

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
    const response = await request.get("/login");
    const csp = response.headers()["content-security-policy"] || "";
    if (!csp) return;
    const scriptSrc = csp
      .split(";")
      .map((directive) => directive.trim())
      .find((directive) => directive.startsWith("script-src")) || "";
    expect(scriptSrc,
      "script-src must allow same-origin bundles",
    ).toMatch(/(?:^|\s)'self'(?:\s|$)/);
    expect(scriptSrc,
      "script-src must include hashes for the exported bootstrap",
    ).toMatch(/'sha256-[A-Za-z0-9+/=]+'/);
    expect(scriptSrc,
      "script-src must not reopen inline script execution",
    ).not.toContain("'unsafe-inline'");
    expect(scriptSrc,
      "script-src must not require eval in the static export",
    ).not.toContain("'unsafe-eval'");
    expect(csp).toMatch(/frame-ancestors 'none'/);
    expect(csp).toMatch(/form-action 'self'/);
    expect(csp).toMatch(/base-uri 'self'/);
  });
});
