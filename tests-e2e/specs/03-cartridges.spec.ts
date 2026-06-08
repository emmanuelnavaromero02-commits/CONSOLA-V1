/**
 * v1.44.3.2 spec 03 — Next.js /cartridges.
 *
 * The user's bug report mentioned "muchos botones no responden" on
 * the legacy console; this spec validates the v1.44.3 Next.js
 * rewrite where every button must actually do something.
 */
import { test, expect } from "../fixtures/auth";

const cartridgeViewerLinks =
  'a[href^="/cartridges/viewer?id="], a[href^="/cartridges/viewer/?id="]';

test.describe("Cartridges grid (Next.js, /cartridges)", () => {
  test("renders the built-in cartridge grid", async ({ authedPage: page }) => {
    await page.goto("/cartridges");
    // Each tile exposes a static-export-safe query-param viewer link.
    const tiles = page.locator(cartridgeViewerLinks);
    await expect(tiles.first()).toBeVisible({ timeout: 10_000 });
    const count = await tiles.count();
    expect(count,
      "expected built-in cartridge tiles (hubspot, replicon, sap_hcm, sap_s4hana, sap_successfactors)",
    ).toBeGreaterThanOrEqual(5);
  });

  test("each tile shows name + status badge", async ({ authedPage: page }) => {
    await page.goto("/cartridges");
    const firstTile = page
      .locator(cartridgeViewerLinks)
      .first()
      .locator("xpath=ancestor::article[1]");
    await expect(firstTile).toBeVisible({ timeout: 10_000 });
    const text = (await firstTile.innerText()).trim();
    // The brief documents one of these 4 status labels.
    expect(text,
      "tile must render at least one of the 4 status labels",
    ).toMatch(/conectado|sin probar|sin configurar|falló/i);
  });

  test("clicking Replicon navigates to /cartridges/viewer?id=replicon", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges");
    const repliconLink = page.locator(
      'a[href="/cartridges/viewer?id=replicon"], a[href="/cartridges/viewer/?id=replicon"]',
    );
    await expect(repliconLink.first()).toBeVisible({ timeout: 10_000 });
    await repliconLink.first().click();
    await page.waitForURL(/\/cartridges\/viewer\/?\?id=replicon/, { timeout: 10_000 });
    // v1.45 Vault-only UX (#273 B5): the viewer delegates credential
    // writes to the scoped Vault page instead of an inline form.
    await expect(
      page.getByRole("link", { name: /configurar en vault/i }),
    ).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("Cartridge detail (Next.js, /cartridges/viewer?id=...)", () => {
  test("the viewer renders the Vault-scoped config surface with a test URL override", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    // #273 B5: credentials are never typed into the browser. The viewer
    // shows the expected-field summary + a CTA to the scoped Vault page.
    // #277 restores a temporary Base URL override only for connection tests.
    await expect(
      page.getByRole("link", { name: /configurar en vault/i }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel(/base url para prueba/i)).toBeVisible();
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
    await expect(cta).toHaveAttribute(
      "href",
      /\/operations\/vault\?cartridge=replicon(?:&conn_id=[^&]+)?/,
    );
  });

  test("credential writes are delegated to Vault (no inline Guardar/Borrar)", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    await expect(
      page.getByRole("button", { name: /probar conexión/i }),
    ).toBeVisible({ timeout: 15_000 });
    // #273 B5 removed the inline credential write/delete affordances so
    // secrets never live in the browser. Guard against their return.
    await expect(
      page.getByRole("button", { name: /guardar credenciales/i }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /borrar credenciales/i }),
    ).toHaveCount(0);
  });
});
