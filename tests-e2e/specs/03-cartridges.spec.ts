import { test, expect } from "../fixtures/auth";

const cartridgeViewerLinks =
  'a[href^="/cartridges/viewer?id="], a[href^="/cartridges/viewer/?id="]';
const CONNECTED_TAB = "/marketplace?tab=conectadas";

test.describe("Fuentes de datos — Conectadas tab (/marketplace?tab=conectadas)", () => {
  test("renders the built-in cartridge tiles", async ({ authedPage: page }) => {
    await page.goto(CONNECTED_TAB);
    const tiles = page.locator(cartridgeViewerLinks);
    await expect(tiles.first()).toBeVisible({ timeout: 10_000 });
    const count = await tiles.count();
    expect(count,
      "expected built-in cartridge tiles (hubspot, replicon, sap_hcm, sap_s4hana, sap_successfactors)",
    ).toBeGreaterThanOrEqual(5);
  });

  test("each tile shows name + status badge", async ({ authedPage: page }) => {
    await page.goto(CONNECTED_TAB);
    const firstTile = page
      .locator(cartridgeViewerLinks)
      .first()
      .locator("xpath=ancestor::article[1]");
    await expect(firstTile).toBeVisible({ timeout: 10_000 });
    const text = (await firstTile.innerText()).trim();
    expect(text,
      "tile must render at least one of the 4 status labels",
    ).toMatch(/conectado|sin probar|sin configurar|falló/i);
  });

  test("clicking Replicon navigates to /cartridges/viewer?id=replicon", async ({
    authedPage: page,
  }) => {
    await page.goto(CONNECTED_TAB);
    const repliconLink = page.locator(
      'a[href="/cartridges/viewer?id=replicon"], a[href="/cartridges/viewer/?id=replicon"]',
    );
    await expect(repliconLink.first()).toBeVisible({ timeout: 10_000 });
    await repliconLink.first().click();
    await page.waitForURL(/\/cartridges\/viewer\/?\?id=replicon/, { timeout: 10_000 });
    await expect(
      page.getByRole("link", { name: /configurar en vault/i }),
    ).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("Legacy routes redirect into the unified surface", () => {
  for (const [legacy, tab] of [
    ["/cartridges", "conectadas"],
    ["/customer/cartridges", "conectadas"],
  ] as const) {
    test(`${legacy} lands on /marketplace?tab=${tab}`, async ({ authedPage: page }) => {
      await page.goto(legacy);
      await page.waitForURL(new RegExp(`/marketplace/?\\?tab=${tab}`), { timeout: 10_000 });
      await expect(
        page.getByRole("heading", { name: /fuentes de datos/i, level: 1 }),
      ).toBeVisible({ timeout: 15_000 });
    });
  }
});

test.describe("Cartridge detail (Next.js, /cartridges/viewer?id=...)", () => {
  test("the viewer renders the Vault-scoped config surface (no inline credential inputs)", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    await expect(
      page.getByRole("link", { name: /configurar en vault/i }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole("button", { name: /probar conexión/i }),
    ).toBeVisible();
    expect(
      await page.locator('input[type="password"]').count(),
      "Vault-only viewer must never render a password input",
    ).toBe(0);
  });

  test("'Probar conexión' button is present and clickable", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    const btn = page.getByRole("button", { name: /probar conexión/i });
    await expect(btn).toBeVisible({ timeout: 15_000 });
    await expect(btn).toBeEnabled();
  });

  test("'Configurar en Vault' CTA links to the scoped Vault page", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    const cta = page.getByRole("link", { name: /configurar en vault/i });
    await expect(cta).toBeVisible({ timeout: 15_000 });
    await expect(cta).toHaveAttribute("href", /\/operations\/vault/);
  });

  test("credential writes are delegated to Vault (no inline Guardar/Borrar)", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    await expect(
      page.getByRole("button", { name: /probar conexión/i }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole("button", { name: /guardar credenciales/i }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /borrar credenciales/i }),
    ).toHaveCount(0);
  });
});
