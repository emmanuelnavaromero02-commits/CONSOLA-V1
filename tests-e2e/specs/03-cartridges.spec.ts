/**
 * v1.44.3.2 spec 03 — Next.js /cartridges.
 *
 * The user's bug report mentioned "muchos botones no responden" on
 * the legacy console; this spec validates the v1.44.3 Next.js
 * rewrite where every button must actually do something.
 */
import { test, expect } from "../fixtures/auth";

test.describe("Cartridges grid (Next.js, /cartridges)", () => {
  test("renders the 4-cartridge grid", async ({ authedPage: page }) => {
    await page.goto("/cartridges");
    // Each tile exposes a static-export-safe query-param viewer link.
    const tiles = page.locator('a[href^="/cartridges/viewer"]');
    await expect(tiles.first()).toBeVisible({ timeout: 10_000 });
    const count = await tiles.count();
    expect(count,
      "expected 4 cartridge tiles (replicon, sap_hcm, sap_s4hana, sap_successfactors)",
    ).toBeGreaterThanOrEqual(4);
  });

  test("each tile shows name + status badge", async ({ authedPage: page }) => {
    await page.goto("/cartridges");
    const firstTile = page
      .locator('a[href^="/cartridges/viewer"]')
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
    const repliconLink = page.locator('a[href="/cartridges/viewer?id=replicon"], a[href="/cartridges/viewer/?id=replicon"]');
    await expect(repliconLink.first()).toBeVisible({ timeout: 10_000 });
    await repliconLink.first().click();
    await page.waitForURL(/\/cartridges\/viewer\/?\?id=replicon/, { timeout: 10_000 });
    await expect(page.locator("form")).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("Cartridge detail (Next.js, /cartridges/viewer?id=...)", () => {
  test("the form renders real <input> elements (not stub rectangles)", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    // The dynamic form builds at least one labelled input — count
    // the form inputs after the schema load resolves.
    await expect(page.locator("form")).toBeVisible({ timeout: 15_000 });
    const inputs = await page.locator("form input, form select").count();
    expect(inputs,
      "credentials form must render at least one input from the schema",
    ).toBeGreaterThan(0);
  });

  test("'Probar conexión' button is present and clickable", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    const btn = page.getByRole("button", { name: /probar conexión/i });
    await expect(btn).toBeVisible({ timeout: 15_000 });
    await expect(btn).toBeEnabled();
  });

  test("'Guardar credenciales' button is present", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    await expect(
      page.getByRole("button", { name: /guardar credenciales/i }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("'Borrar credenciales' opens the confirm dialog", async ({
    authedPage: page,
  }) => {
    await page.goto("/cartridges/viewer?id=replicon");
    const deleteBtn = page.getByRole("button", { name: /borrar credenciales/i });
    await expect(deleteBtn).toBeVisible({ timeout: 15_000 });
    await deleteBtn.click();
    // The dialog ships with aria-labelledby="confirm-delete-title".
    await expect(
      page.getByRole("dialog", { name: /borrar credenciales/i }),
    ).toBeVisible({ timeout: 5_000 });
    // ESC dismisses (regression guard for v1.44.3 R1 P2 fix).
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("dialog", { name: /borrar credenciales/i }),
    ).toBeHidden({ timeout: 2_000 });
  });
});
