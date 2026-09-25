import { test, expect } from "../fixtures/auth";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

interface LegacyPage {
  path:     string;
  needle:   RegExp;
  label:    string;
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
      await page.goto(`${LEGACY}/monitor`);
      const link = page.locator(
        'a[href="/copilot"], a[href="/copilot/"], a[href$="/copilot"], a[href$="/copilot/"], button[data-fab="copilot"]',
      );
      await expect(link.first()).toBeAttached({ timeout: 10_000 });
    },
  );
});
