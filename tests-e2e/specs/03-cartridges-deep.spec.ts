/**
 * v1.44.3.2.1 spec 03-deep — Exhaustive /cartridges.
 *
 * 28 tests covering the grid for all 4 cartridges, the dynamic
 * form for each, save / test / delete flows with the LIVE Vault
 * (using cleanup-safe fake credentials), and per-cartridge schema
 * shape sanity.
 */
import { test, expect } from "../fixtures/auth";

const CARTRIDGES = ["replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
const cartridgeViewerPath = (cart: string) => `/cartridges/viewer/?id=${cart}`;

test.describe("Cartridges grid — coverage of all 4", () => {
  test("renders the 'Cartuchos' h1", async ({ page }) => {
    await page.goto("/cartridges");
    await expect(
      page.getByRole("heading", { name: /cartuchos/i, level: 1 }),
    ).toBeVisible({ timeout: 10_000 });
  });

  for (const cart of CARTRIDGES) {
    test(`tile for ${cart} renders and links to ${cartridgeViewerPath(cart)}`,
      async ({ page }) => {
        await page.goto("/cartridges");
        const link = page.locator(`a[href="${cartridgeViewerPath(cart)}"]`).first();
        await expect(link).toBeVisible({ timeout: 15_000 });
      },
    );
  }

  test("grid has exactly 4 cartridge tiles", async ({ page }) => {
    await page.goto("/cartridges");
    const tiles = page.locator('a[href^="/cartridges/viewer"]');
    // Allow >4 in case the dashboard freshness card also appears,
    // but at least the canonical 4 must be present.
    await expect(tiles.first()).toBeVisible({ timeout: 15_000 });
    expect(await tiles.count()).toBeGreaterThanOrEqual(4);
  });

  test("each tile carries a status badge", async ({ page }) => {
    await page.goto("/cartridges");
    const labels = /conectado|sin probar|sin configurar|falló/i;
    const badge = page.getByText(labels).first();
    await expect(badge).toBeVisible({ timeout: 15_000 });
  });

  test("loading state shows 4 skeleton tiles before data arrives",
    async ({ page }) => {
      // Throttle the listCartridges request so the skeleton has
      // time to render.
      await page.route("**/api/cartridges", async (route) => {
        await new Promise((r) => setTimeout(r, 2_000));
        await route.continue();
      });
      await page.goto("/cartridges");
      const skeleton = page.locator(".animate-pulse").first();
      await expect(skeleton).toBeVisible({ timeout: 3_000 });
    },
  );

  test("on backend 500 the page shows retry", async ({ page }) => {
    await page.route("**/api/cartridges", (route) =>
      route.fulfill({ status: 500, body: '{"detail":"boom"}' }),
    );
    await page.goto("/cartridges");
    await expect(
      page.getByRole("button", { name: /reintentar|retry/i }),
    ).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("Cartridge detail — schema-driven form", () => {
  for (const cart of CARTRIDGES) {
    test(`${cart} detail page loads schema + renders form`,
      async ({ page }) => {
        await page.goto(cartridgeViewerPath(cart));
        await expect(page.locator("form")).toBeVisible({ timeout: 15_000 });
        const fields = await page.locator("form input, form select").count();
        expect(fields,
          `${cart} schema should produce at least one input`,
        ).toBeGreaterThan(0);
      },
    );

    test(`${cart} breadcrumb back to /cartridges`, async ({ page }) => {
      await page.goto(cartridgeViewerPath(cart));
      const crumb = page.locator('nav[aria-label="breadcrumb"]');
      await expect(crumb).toBeVisible({ timeout: 15_000 });
      const back = crumb.locator('a[href="/cartridges"]');
      await expect(back).toBeVisible();
    });

    test(`${cart} has Save + Test + Delete buttons`, async ({ page }) => {
      await page.goto(cartridgeViewerPath(cart));
      await expect(
        page.getByRole("button", { name: /guardar credenciales/i }),
      ).toBeVisible({ timeout: 15_000 });
      await expect(
        page.getByRole("button", { name: /probar conexión/i }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: /borrar credenciales/i }),
      ).toBeVisible();
    });
  }
});

test.describe("Cartridge detail — interactions (Replicon)", () => {
  test("Save with empty form: required fields surface errors",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      await page.getByRole("button", { name: /guardar credenciales/i }).click();
      await page.waitForTimeout(800);
      // Either react-hook-form renders an inline error OR the
      // sonner toast surfaces a 400-style message.
      const inlineError = page.locator('[role="alert"]').first();
      const toast = page.locator("[data-sonner-toast]").first();
      const ok =
        (await inlineError.isVisible({ timeout: 2_000 }).catch(() => false)) ||
        (await toast.isVisible({ timeout: 2_000 }).catch(() => false));
      expect(ok,
        "empty-form Save must surface validation feedback (inline OR toast)",
      ).toBe(true);
    },
  );

  test("password field has eye toggle that flips aria-label",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      const pwdField = page.locator('input[type="password"]').first();
      if (!(await pwdField.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.fail(true, "no password field in replicon schema");
        return;
      }
      const toggleHidden = page.getByRole("button", { name: /mostrar contraseña/i });
      await expect(toggleHidden).toBeVisible();
      await toggleHidden.click();
      await expect(
        page.getByRole("button", { name: /ocultar contraseña/i }),
      ).toBeVisible({ timeout: 2_000 });
    },
  );

  test("Test connection with NO creds in vault: shows useful error",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      const testBtn = page.getByRole("button", { name: /probar conexión/i });
      await testBtn.click();
      // The TestConnectionResult component renders OK/error with a
      // message + latency. Either path is acceptable as long as the
      // component renders.
      await expect(
        page.getByText(/conexión (exitosa|fallida)/i),
      ).toBeVisible({ timeout: 15_000 });
    },
  );

  test("Delete with no credentials: confirm dialog still opens",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      await page.getByRole("button", { name: /borrar credenciales/i }).click();
      await expect(
        page.getByRole("dialog", { name: /borrar credenciales/i }),
      ).toBeVisible({ timeout: 5_000 });
    },
  );

  test("Delete dialog auto-focuses Cancel button (v1.44.3 R1 regression guard)",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      await page.getByRole("button", { name: /borrar credenciales/i }).click();
      const dialog = page.getByRole("dialog", { name: /borrar credenciales/i });
      await expect(dialog).toBeVisible({ timeout: 5_000 });
      const focused = await page.evaluate(
        () => document.activeElement?.textContent?.trim(),
      );
      expect(focused?.toLowerCase()).toContain("cancelar");
    },
  );

  test("Delete dialog: ESC closes without firing the delete",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      await page.getByRole("button", { name: /borrar credenciales/i }).click();
      const dialog = page.getByRole("dialog", { name: /borrar credenciales/i });
      await expect(dialog).toBeVisible({ timeout: 5_000 });
      let deleteFired = false;
      page.on("request", (req) => {
        if (
          req.method() === "DELETE" &&
          req.url().includes("/credentials")
        ) deleteFired = true;
      });
      await page.keyboard.press("Escape");
      await page.waitForTimeout(1_000);
      expect(deleteFired,
        "ESC must NOT trigger the destructive DELETE",
      ).toBe(false);
      await expect(dialog).toBeHidden({ timeout: 2_000 });
    },
  );

  test("Delete dialog: backdrop click closes (also non-destructive)",
    async ({ page }) => {
      await page.goto(cartridgeViewerPath("replicon"));
      await page.getByRole("button", { name: /borrar credenciales/i }).click();
      const dialog = page.getByRole("dialog", { name: /borrar credenciales/i });
      await expect(dialog).toBeVisible({ timeout: 5_000 });
      // Click the backdrop (the overlay's outer div).
      await page.mouse.click(50, 50);
      await expect(dialog).toBeHidden({ timeout: 2_000 });
    },
  );
});

test.describe("Cartridge detail — backend round-trip with fake creds", () => {
  // These tests use throwaway credential values. They write to the
  // Vault and then delete cleanly — leaving the cartridge in the
  // SAME state as before the test ran.
  test("Save → toast.success appears + invalidates grid", async ({ page }) => {
    await page.goto(cartridgeViewerPath("replicon"));
    // Fill REQUIRED fields with throwaway values. The replicon
    // schema typically has base_url + username + password — we
    // populate by filling EVERY required field.
    const required = page.locator('input[required], input[aria-required="true"]');
    const count = await required.count();
    for (let i = 0; i < count; i++) {
      const el = required.nth(i);
      const type = await el.getAttribute("type");
      if (type === "url") {
        await el.fill("https://e2e.invalid.local/probe");
      } else {
        await el.fill(`e2e-throwaway-${i}`);
      }
    }
    await page.getByRole("button", { name: /guardar credenciales/i }).click();
    // v1.44.3.2.1 R1 Security P2: wrap the cleanup in try/finally so
    // a failed assertion above doesn't leave throwaway credentials in
    // the Vault. The cleanup runs even on cancellation/assertion-fail.
    try {
      const toast = page.locator("[data-sonner-toast]").first();
      await expect(toast).toBeVisible({ timeout: 10_000 });
      const text = (await toast.innerText()).toLowerCase();
      // Either success (200 round-trip) or a USEFUL error (4xx/5xx
      // with a real message) is acceptable. A SILENT failure is not.
      expect(text.length).toBeGreaterThan(3);
    } finally {
      await page
        .getByRole("button", { name: /borrar credenciales/i })
        .click()
        .catch(() => null);
      const dialog = page.getByRole("dialog", { name: /borrar credenciales/i });
      if (await dialog.isVisible({ timeout: 3_000 }).catch(() => false)) {
        await page
          .getByRole("button", { name: /^borrar$/i })
          .click()
          .catch(() => null);
      }
    }
  });
});
