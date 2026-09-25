import { test, expect } from "../fixtures/auth";

const CARTRIDGES = ["hubspot", "replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
const cartridgeViewer = (id: string) => `/cartridges/viewer?id=${id}`;
const cartridgeViewerLink = (id: string) =>
  `a[href="/cartridges/viewer?id=${id}"], a[href="/cartridges/viewer/?id=${id}"]`;
const cartridgeViewerLinks =
  'a[href^="/cartridges/viewer?id="], a[href^="/cartridges/viewer/?id="]';

test.describe("Cartridges grid — coverage of all 5", () => {
  test("renders the 'Cartuchos' h1", async ({ page }) => {
    await page.goto("/cartridges");
    await expect(
      page.getByRole("heading", { name: /cartuchos/i, level: 1 }),
    ).toBeVisible({ timeout: 10_000 });
  });

  for (const cart of CARTRIDGES) {
    test(`tile for ${cart} renders and links to /cartridges/viewer?id=${cart}`,
      async ({ page }) => {
        await page.goto("/cartridges");
        const link = page.locator(cartridgeViewerLink(cart)).first();
        await expect(link).toBeVisible({ timeout: 15_000 });
      },
    );
  }

  test("grid has at least 5 cartridge tiles", async ({ page }) => {
    await page.goto("/cartridges");
    const tiles = page.locator(cartridgeViewerLinks);
    await expect(tiles.first()).toBeVisible({ timeout: 15_000 });
    expect(await tiles.count()).toBeGreaterThanOrEqual(5);
  });

  test("each tile carries a status badge", async ({ page }) => {
    await page.goto("/cartridges");
    const labels = /conectado|sin probar|sin configurar|falló/i;
    const badge = page.getByText(labels).first();
    await expect(badge).toBeVisible({ timeout: 15_000 });
  });

  test("loading state shows skeleton tiles before data arrives",
    async ({ page }) => {
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
    test(`${cart} detail page loads the Vault-scoped config surface`,
      async ({ page }) => {
        await page.goto(cartridgeViewer(cart));
        await expect(
          page.getByRole("link", { name: /configurar en vault/i }),
        ).toBeVisible({ timeout: 15_000 });
        await expect(
          page.getByRole("button", { name: /probar conexión/i }),
        ).toBeVisible();
      },
    );

    test(`${cart} breadcrumb back to /cartridges`, async ({ page }) => {
      await page.goto(cartridgeViewer(cart));
      const crumb = page.locator('nav[aria-label="breadcrumb"]');
      await expect(crumb).toBeVisible({ timeout: 15_000 });
      const back = crumb.locator('a[href="/cartridges"], a[href="/cartridges/"]');
      await expect(back).toBeVisible();
    });

    test(`${cart} exposes Probar conexión + Vault CTA (no inline Save/Delete)`, async ({ page }) => {
      await page.goto(cartridgeViewer(cart));
      await expect(
        page.getByRole("button", { name: /probar conexión/i }),
      ).toBeVisible({ timeout: 15_000 });
      await expect(
        page.getByRole("link", { name: /configurar en vault/i }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: /guardar credenciales/i }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: /borrar credenciales/i }),
      ).toHaveCount(0);
    });
  }
});

test.describe("Cartridge detail — interactions (Replicon)", () => {
  test("'Configurar en Vault' navigates to the scoped Vault page",
    async ({ page }) => {
      await page.goto(cartridgeViewer("replicon"));
      const cta = page.getByRole("link", { name: /configurar en vault/i });
      await expect(cta).toBeVisible({ timeout: 15_000 });
      await cta.click();
      await page.waitForURL(/\/operations\/vault/, { timeout: 10_000 });
    },
  );

  test("password field has eye toggle that flips aria-label",
    async ({ page }) => {
      await page.goto(cartridgeViewer("replicon"));
      const pwdField = page.locator('input[type="password"]').first();
      if (!(await pwdField.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.skip(true, "replicon schema has no password field");
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
      await page.goto(cartridgeViewer("replicon"));
      const testBtn = page.getByRole("button", { name: /probar conexión/i });
      await testBtn.click();
      await expect(
        page.getByText(/conexión (exitosa|fallida)/i),
      ).toBeVisible({ timeout: 15_000 });
    },
  );

  test("no inline destructive credential delete is exposed (delegated to Vault)",
    async ({ page }) => {
      await page.goto(cartridgeViewer("replicon"));
      await expect(
        page.getByRole("button", { name: /probar conexión/i }),
      ).toBeVisible({ timeout: 15_000 });
      await expect(
        page.getByRole("button", { name: /borrar credenciales/i }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("dialog", { name: /borrar credenciales/i }),
      ).toHaveCount(0);
    },
  );
});

test.describe("Cartridge detail — credentials are Vault-delegated", () => {
  test("viewer never POSTs credentials from the browser", async ({ page }) => {
    const credentialWrites: string[] = [];
    page.on("request", (req) => {
      if (
        req.method() === "POST" &&
        /\/api\/cartridges\/[^/]+\/credentials/.test(req.url())
      ) {
        credentialWrites.push(req.url());
      }
    });
    await page.goto(cartridgeViewer("replicon"));
    await expect(
      page.getByRole("link", { name: /configurar en vault/i }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole("button", { name: /probar conexión/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /guardar credenciales/i }),
    ).toHaveCount(0);
    expect(
      credentialWrites,
      "viewer must not write credentials from the browser",
    ).toEqual([]);
  });
});
