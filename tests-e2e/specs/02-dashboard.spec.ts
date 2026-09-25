import { test, expect } from "../fixtures/auth";
import type { Page } from "@playwright/test";

const KPI_LABELS = [
  "Cartuchos conectados",
  "Extracciones hoy",
  "Usuarios activos",
  "Acciones copiloto",
] as const;

const kpiCard = (page: Page, label: string) =>
  page.locator(`[data-testid="kpi-card"][data-label="${label}"]`);

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
    for (const label of KPI_LABELS) {
      const card = kpiCard(page, label);
      await expect(card).toBeVisible({ timeout: 15_000 });
      await expect(card.getByText(label, { exact: false })).toBeVisible();
    }
    await expect(page.getByTestId("kpi-card").locator(".animate-pulse")).toHaveCount(0, {
      timeout: 15_000,
    });
  });

  test("'Cartuchos conectados' renders a numeric value", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    const value = kpiCard(page, "Cartuchos conectados")
      .getByTestId("kpi-card-value");
    await expect(value).toHaveAttribute("data-numeric-value", /^\d+$/, {
      timeout: 15_000,
    });
    await expect(value).toBeVisible();
    await expect(value).toContainText(/\d/);
  });

  test("'Extracciones hoy' renders a numeric value", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    const value = kpiCard(page, "Extracciones hoy")
      .getByTestId("kpi-card-value");
    await expect(value).toHaveAttribute("data-numeric-value", /^\d+$/, {
      timeout: 15_000,
    });
    await expect(value).toBeVisible();
    await expect(value).toContainText(/\d/);
  });

  test("'Frescura de datos' table renders rows", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    await expect(page.getByText(/frescura de datos/i)).toBeVisible();
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
    const directLinks = page.locator('a[href^="/cartridges"]');
    await expect(directLinks.first()).toBeAttached({ timeout: 10_000 });
  });

  test("clicking a cartridge link navigates to /cartridges/viewer?id=<id>", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    const link = page
      .locator('a[href^="/cartridges/viewer?id="], a[href^="/cartridges/viewer/?id="]')
      .first();
    await expect(link).toBeVisible({ timeout: 10_000 });
    const href = await link.getAttribute("href");
    expect(href).toMatch(/^\/cartridges\/viewer\/?\?id=[a-z_]+$/);
    await link.click();
    await page.waitForURL(/\/cartridges\/viewer\/?\?id=[a-z_]+/);
  });
});
