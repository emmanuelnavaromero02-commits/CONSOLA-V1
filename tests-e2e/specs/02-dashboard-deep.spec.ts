/**
 * v1.44.3.2.1 spec 02-deep — Exhaustive dashboard.
 *
 * 30 tests covering layout, the 4 KPI tiles, the freshness table,
 * navigation links, polling, dark-mode toggle, and the error/retry
 * surface. Pre-mounted storageState from global-setup so each test
 * lands on /dashboard already authenticated.
 */
import { test, expect } from "../fixtures/auth";

const EMAIL = process.env.TEST_EMAIL || "emmanuel@local.ai";

test.describe("Dashboard layout", () => {
  test("renders the 'Panel' h1", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByRole("heading", { name: /panel/i, level: 1 }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("renders the dashboard description tagline", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByText(/estado en tiempo real|tiempo real/i).first(),
    ).toBeVisible();
  });

  test("page does NOT have stale skeleton placeholders after 15 s",
    async ({ page }) => {
      // v1.44.3.2.1 R1 Testing F1: was a bare waitForTimeout(15s)
      // + count. expect(...).toHaveCount(0, {timeout}) auto-resolves
      // as soon as the skeletons disappear — short-circuits on a
      // fast backend and still bounds the wait at 15 s.
      await page.goto("/dashboard");
      await expect(page.locator(".animate-pulse")).toHaveCount(0, {
        timeout: 15_000,
      });
    },
  );

  test("displays the current user (emmanuel) somewhere visible",
    async ({ page }) => {
      await page.goto("/dashboard");
      // The user email or its prefix should appear in any user
      // affordance — topbar, sidebar dropdown, etc.
      const userVisible = page.getByText(new RegExp(EMAIL.split("@")[0], "i"));
      await expect(userVisible.first()).toBeVisible({ timeout: 10_000 });
    },
  );
});

test.describe("Dashboard — KPI tiles", () => {
  const KPI_LABELS = [
    /cartuchos conectados/i,
    /extracciones hoy/i,
    /usuarios activos/i,
    /acciones copiloto/i,
  ];

  for (const label of KPI_LABELS) {
    test(`KPI '${label.source}' label is visible`, async ({ page }) => {
      await page.goto("/dashboard");
      await expect(page.getByText(label).first()).toBeVisible({
        timeout: 15_000,
      });
    });

    test(`KPI '${label.source}' value contains a digit`, async ({ page }) => {
      await page.goto("/dashboard");
      const labelLoc = page.getByText(label).first();
      await expect(labelLoc).toBeVisible({ timeout: 15_000 });
      const tile = labelLoc.locator("..");
      const text = (await tile.innerText()).trim();
      expect(text,
        `${label.source} KPI tile must render a numeric value`,
      ).toMatch(/\d/);
    });
  }

  test("polling fires a second /kpis request within 35 s", async ({ page }) => {
    let requests = 0;
    page.on("request", (req) => {
      if (req.url().includes("/api/dashboard/kpis")) requests++;
    });
    await page.goto("/dashboard");
    // The hook's refetchInterval is 30 s; allow a 5 s buffer.
    await page.waitForTimeout(35_000);
    expect(requests,
      "dashboard MUST poll /api/dashboard/kpis on its 30 s interval",
    ).toBeGreaterThanOrEqual(2);
  });

  test("KPI grid is responsive — 1 column on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 400, height: 900 });
    await page.goto("/dashboard");
    await page.waitForLoadState("networkidle");
    // The grid uses md:grid-cols-2 lg:grid-cols-4; at 400 px width
    // the tiles stack. We assert each tile occupies the full content
    // width (within tolerance).
    const tile = page.getByText(/cartuchos conectados/i).first().locator("..");
    const box = await tile.boundingBox();
    expect(box?.width,
      "on mobile the KPI tile should be near-full-width (≥ 280 px)",
    ).toBeGreaterThan(280);
  });
});

test.describe("Dashboard — Freshness table", () => {
  test("renders the 'Frescura de datos' section", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByText(/frescura de datos/i)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("table has at least 4 rows (one per cartridge)", async ({ page }) => {
    await page.goto("/dashboard");
    const rows = page.locator("table tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 15_000 });
    const count = await rows.count();
    expect(count,
      "freshness table should have 4 rows — one per known cartridge",
    ).toBeGreaterThanOrEqual(4);
  });

  test("each row has a cartridge link", async ({ page }) => {
    await page.goto("/dashboard");
    const links = page.locator('table a[href^="/cartridges/viewer"]');
    await expect(links.first()).toBeVisible({ timeout: 15_000 });
    const count = await links.count();
    expect(count).toBeGreaterThanOrEqual(4);
  });

  test("status badge tone is one of the documented 4", async ({ page }) => {
    await page.goto("/dashboard");
    const badges = page.locator("table tbody tr span:has-text('Fresca'), " +
      "table tbody tr span:has-text('Antigua'), " +
      "table tbody tr span:has-text('Muy antigua'), " +
      "table tbody tr span:has-text('Nunca')");
    await expect(badges.first()).toBeVisible({ timeout: 15_000 });
  });

  test("click on freshness row navigates to detail page", async ({ page }) => {
    await page.goto("/dashboard");
    const link = page.locator('table a[href^="/cartridges/viewer"]').first();
    await expect(link).toBeVisible({ timeout: 15_000 });
    const href = await link.getAttribute("href");
    await link.click();
    await page.waitForURL(`**${href}`, { timeout: 10_000 });
  });
});

test.describe("Dashboard — audit row", () => {
  test("'Eventos hoy' card renders", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByText(/eventos hoy/i)).toBeVisible({ timeout: 15_000 });
  });

  test("'Acciones destructivas' card renders", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.getByText(/acciones destructivas/i),
    ).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("Dashboard — error + retry surface", () => {
  test("on backend 500 the page shows a retry button", async ({ page }) => {
    // Intercept the KPI endpoint with a synthetic 500.
    await page.route("**/api/dashboard/kpis", (route) => {
      route.fulfill({ status: 500, body: '{"detail":"boom"}' });
    });
    await page.goto("/dashboard");
    await expect(
      page.getByRole("button", { name: /reintentar|retry/i }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("retry button triggers a fresh fetch", async ({ page }) => {
    let attempts = 0;
    await page.route("**/api/dashboard/kpis", (route) => {
      attempts++;
      route.fulfill({ status: 500, body: '{"detail":"boom"}' });
    });
    await page.goto("/dashboard");
    const before = attempts;
    const retryBtn = page.getByRole("button", { name: /reintentar|retry/i });
    await expect(retryBtn).toBeVisible({ timeout: 10_000 });
    await retryBtn.click();
    await page.waitForTimeout(2_000);
    expect(attempts,
      "Retry click must invoke /api/dashboard/kpis again",
    ).toBeGreaterThan(before);
  });
});

test.describe("Dashboard — internal navigation", () => {
  test("global AppChrome renders exactly once", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.locator('header[role="banner"]')).toHaveCount(1);
    await expect(
      page.getByRole("navigation", { name: "Navegación principal" }),
    ).toHaveCount(1);
  });

  test("navigate from dashboard to /cartridges via link", async ({ page }) => {
    await page.goto("/dashboard");
    const link = page.locator('a[href="/cartridges"], a[href^="/cartridges/viewer"]').first();
    if (!(await link.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "no link to /cartridges from /dashboard — UX gap");
      return;
    }
    await link.click();
    await page.waitForURL(/\/cartridges/, { timeout: 10_000 });
  });

  test("navigate from dashboard to /workspace copilot", async ({ page }) => {
    await page.goto("/dashboard");
    const link = page.locator('a[href="/workspace"]').first();
    await expect(link).toBeVisible({ timeout: 10_000 });
    await link.click();
    await page.waitForURL(/\/workspace/, { timeout: 10_000 });
  });

  test("logo / home link returns to /dashboard from any page",
    async ({ page }) => {
      await page.goto("/cartridges");
      const logo = page.locator(
        'a[href="/"], a[href="/dashboard"], a:has-text("OMEGA")',
      ).first();
      if (!(await logo.isVisible({ timeout: 5_000 }).catch(() => false))) {
        test.fail(true, "no logo/home affordance on /cartridges — UX gap");
        return;
      }
      await logo.click();
      await page.waitForURL(/\/(dashboard)?$/, { timeout: 10_000 });
    },
  );
});

test.describe("Dashboard — dark mode (UI only)", () => {
  test("HTML carries the `class` darkMode strategy on the <html>",
    async ({ page }) => {
      await page.goto("/dashboard");
      // Tailwind config uses darkMode: 'class' — verify the toggle
      // affordance exists. Either a button with aria-label or a
      // documented class change on <html>.
      const toggle = page.locator(
        'button[aria-label*="dark" i], button[aria-label*="modo" i], button[aria-label*="theme" i]',
      );
      if (!(await toggle.first().isVisible({ timeout: 5_000 }).catch(() => false))) {
        test.fail(true, "dark-mode toggle not surfaced on /dashboard — UX gap");
        return;
      }
      const beforeClass = await page.locator("html").getAttribute("class") || "";
      await toggle.first().click();
      await page.waitForTimeout(500);
      const afterClass = await page.locator("html").getAttribute("class") || "";
      expect(afterClass, "<html> class should change after toggle").not.toBe(beforeClass);
    },
  );
});
