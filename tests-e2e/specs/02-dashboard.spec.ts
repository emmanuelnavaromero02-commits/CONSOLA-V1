/**
 * v1.44.3.2 spec 02 — Next.js dashboard.
 *
 * The dashboard is the home of the Next.js console; if KPIs don't
 * land within a few seconds the operator can't tell whether the
 * platform is alive. These tests fail loudly on a stuck skeleton
 * or a 4xx from /api/dashboard/kpis.
 */
import { test, expect } from "../fixtures/auth";

test.describe("Dashboard (Next.js, /dashboard)", () => {
  test("renders the 'Panel' heading", async ({ authedPage: page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByRole("heading", { name: /panel/i, level: 1 }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("renders 4 KPI tiles (not stuck on the skeleton)", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    // The KpiCard label section uses uppercase letter-spaced text;
    // we match the documented labels directly so a re-skin can't
    // false-positive this test.
    for (const label of [
      /cartuchos conectados/i,
      /extracciones hoy/i,
      /usuarios activos/i,
      /acciones copiloto/i,
    ]) {
      await expect(page.getByText(label)).toBeVisible({ timeout: 15_000 });
    }
    // Pulse-animation skeletons share the `.animate-pulse` class.
    // After 15 s every tile should be populated and no skeleton
    // should remain on screen.
    await page.waitForTimeout(1_500);
    const stillPulsing = await page.locator(".animate-pulse").count();
    expect(stillPulsing,
      "KPI skeleton placeholders should resolve to real values within 15 s",
    ).toBe(0);
  });

  test("'Cartuchos conectados' renders a numeric value", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    // The tile's value sits in a 3xl text node directly under the
    // label. We just need to verify it's parseable as either "N / N"
    // or a single integer — both shapes are valid per the KPI helper.
    const label = page.getByText(/cartuchos conectados/i);
    await expect(label).toBeVisible({ timeout: 15_000 });
    // Walk up to the tile container and read the big number node.
    const tile = label.locator("..");
    const text = (await tile.innerText()).trim();
    expect(text,
      "Cartuchos KPI value must contain at least one digit",
    ).toMatch(/\d/);
  });

  test("'Extracciones hoy' renders a numeric value", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    const label = page.getByText(/extracciones hoy/i);
    await expect(label).toBeVisible({ timeout: 15_000 });
    const tile = label.locator("..");
    const text = (await tile.innerText()).trim();
    expect(text).toMatch(/\d/);
  });

  test("'Frescura de datos' table renders rows", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    await expect(page.getByText(/frescura de datos/i)).toBeVisible();
    // The table has a 'Cartucho' header and at least one cartridge row
    // (replicon / sap_hcm / sap_s4hana / sap_successfactors) — even
    // if every status is 'never' we still render the four rows.
    const table = page.locator("table");
    await expect(table.first()).toBeVisible({ timeout: 10_000 });
    const rows = await table.first().locator("tbody tr").count();
    expect(rows,
      "freshness table must have at least one cartridge row",
    ).toBeGreaterThan(0);
  });

  test("'Eventos hoy' KPI loads", async ({ authedPage: page }) => {
    await page.goto("/dashboard");
    await expect(page.getByText(/eventos hoy/i)).toBeVisible({ timeout: 15_000 });
  });

  test("navigation surface is visible (link to cartridges)", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    // The dashboard freshness table contains <Link href="/cartridges/<id>">
    // entries — those are the documented affordance to navigate to
    // cartridges from the dashboard. If neither a topbar nor an
    // in-table link exists, the user is stranded.
    const directLinks = page.locator('a[href^="/cartridges"]');
    await expect(directLinks.first()).toBeAttached({ timeout: 10_000 });
  });

  test("clicking a cartridge link navigates to /cartridges/<id>", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    const link = page.locator('a[href^="/cartridges/"]').first();
    await expect(link).toBeVisible({ timeout: 10_000 });
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^\/cartridges\/[a-z_]+$/);
    await link.click();
    await page.waitForURL(/\/cartridges\/[a-z_]+/);
  });
});
