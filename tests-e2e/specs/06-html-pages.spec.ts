/**
 * v1.44.3.2 spec 06 — Legacy HTML pages (admin surfaces).
 *
 * Smoke checks: every admin page that's still served from the
 * FastAPI console must load with HTTP 200 and render its primary
 * content region. These pages live at /audit, /iam (users),
 * /operations, /monitor, /workspace, /me, /settings, /security.
 *
 * Per the v1.44.2 brief these pages stay HTML for now (Next.js
 * migration is scoped to user-facing flows only).
 */
import { test, expect } from "../fixtures/auth";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

interface LegacyPage {
  path:     string;
  needle:   RegExp;   // text or selector substring proving the page rendered
  label:    string;   // human-readable name for the test
}

const PAGES: LegacyPage[] = [
  { path: "/audit",      needle: /eventos|audit/i,          label: "audit" },
  { path: "/iam",        needle: /usuarios|users|iam/i,     label: "iam (users)" },
  { path: "/operations", needle: /operations|operación/i,   label: "operations" },
  { path: "/monitor",    needle: /monitor/i,                label: "monitor" },
  { path: "/me",         needle: /perfil|profile|me/i,      label: "me" },
  { path: "/settings",   needle: /settings|ajustes/i,       label: "settings" },
  { path: "/security",   needle: /security|sesiones|security/i, label: "security" },
];

for (const p of PAGES) {
  test(`legacy ${p.label} (${p.path}) loads and renders content`, async ({
    authedPage: page,
  }) => {
    const response = await page.goto(`${LEGACY}${p.path}`);
    expect(response?.status(),
      `${p.path} must respond 200 (got ${response?.status()})`,
    ).toBe(200);
    // After the HTML loads, give the page a moment to hydrate its
    // JS surface, then assert the primary content text is visible.
    await page.waitForTimeout(1_500);
    await expect(
      page.getByText(p.needle).first(),
    ).toBeVisible({ timeout: 10_000 });
  });
}

test.describe("Legacy navigation surface", () => {
  test("at least one HTML page renders a global nav with a copilot link",
    async ({ authedPage: page }) => {
      // The v1.44.1 design system added copilot_fab.js as a drop-in
      // FAB. The user reported the legacy console doesn't reliably
      // surface the copilot — this test pins one of the legacy
      // pages and looks for a /copilot link OR the FAB.
      await page.goto(`${LEGACY}/monitor`);
      const link = page.locator('a[href="/copilot"], a[href$="/copilot"], button[data-fab="copilot"]');
      await expect(link.first()).toBeAttached({ timeout: 10_000 });
    },
  );
});
