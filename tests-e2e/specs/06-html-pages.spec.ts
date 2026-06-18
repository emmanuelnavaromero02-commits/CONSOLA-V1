/**
 * v1.44.3.2 spec 06 — Legacy HTML pages (admin surfaces).
 *
 * Smoke checks: every admin page served by the FastAPI console
 * must load with HTTP 200 and render its primary content region.
 * Some paths are now static Next.js exports, but they still run
 * same-origin on :8000.
 *
 * v1.44.3.3 Task F: the v1.44.3.2 spec asserted on /audit
 * directly but console/app/routers/security.py exposes the audit
 * log as the JSON API /security/audit (router prefix=/security).
 * Keep that endpoint pinned below without treating it as an HTML
 * page: the payload depends on the current audit stream and must
 * not rely on a specific event action being present.
 *
 */
import { test, expect } from "../fixtures/auth";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

interface LegacyPage {
  path:     string;
  needle:   RegExp;   // text or selector substring proving the page rendered
  label:    string;   // human-readable name for the test
}

const PAGES: LegacyPage[] = [
  { path: "/iam",            needle: /usuarios|users|iam/i,     label: "iam (users)" },
  { path: "/operations",     needle: /operations|operaci[oó]n(?:es)?/i, label: "operations" },
  { path: "/monitor",        needle: /ejecuciones y logs|ejecuciones recientes/i, label: "monitor" },
  { path: "/me",             needle: /perfil|profile|me/i,      label: "me" },
  { path: "/settings",       needle: /settings|ajustes/i,       label: "settings" },
  { path: "/security",       needle: /security|sesiones|security/i, label: "security" },
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

test("legacy audit API (/security/audit) returns JSON audit stream", async ({
  authedPage: page,
}) => {
  const response = await page.goto(`${LEGACY}/security/audit`);
  expect(response?.status(),
    `/security/audit must respond 200 (got ${response?.status()})`,
  ).toBe(200);

  const contentType = response?.headers()["content-type"] || "";
  expect(contentType).toContain("application/json");

  const events = await response!.json();
  expect(Array.isArray(events)).toBe(true);
  for (const event of events.slice(0, 5)) {
    expect(event).toEqual(expect.objectContaining({
      action: expect.any(String),
      created_at: expect.any(String),
    }));
  }
});

test.describe("Legacy navigation surface", () => {
  test("at least one HTML page renders a global nav with a copilot link",
    async ({ authedPage: page }) => {
      // The v1.44.1 design system added copilot_fab.js as a drop-in
      // FAB. The user reported the legacy console doesn't reliably
      // surface the copilot — this test pins one of the legacy
      // pages and looks for a /copilot link OR the FAB.
      await page.goto(`${LEGACY}/monitor`);
      const link = page.locator(
        'a[href="/copilot"], a[href="/copilot/"], a[href$="/copilot"], a[href$="/copilot/"], button[data-fab="copilot"]',
      );
      await expect(link.first()).toBeAttached({ timeout: 10_000 });
    },
  );
});
