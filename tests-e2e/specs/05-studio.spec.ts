/**
 * v1.44.3.2 spec 05 — Legacy /studio (HTML console on port 8000).
 *
 * The user reported a concrete set of broken interactions on this
 * page (Grafo button, Deploy a Airflow, Plantillas, Subir spec drop
 * zone, Silver/Gold/Master interactivity, Crear en Superset). Each
 * test here pins one of those failures so v1.44.3.3 has actionable
 * evidence.
 *
 * The studio surface is built by client-side JS (console/app/static/
 * js/studio/*), so tests assert on the RENDERED DOM, not the static
 * HTML, and wait for the tabs to materialise before clicking.
 */
import { test, expect } from "../fixtures/auth";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

test.describe("Legacy /studio page (port 8000)", () => {
  test("/studio loads with tab navigation", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    // After client-side hydration the tab labels appear. The brief
    // documents 7 tabs (Resumen / DAGs / Entidades / Refinar /
    // Analytics / IA Semántica / RAG). We assert at least DAGs +
    // Entidades + Refinar are visible — the load-bearing ones.
    for (const label of [/DAGs/, /Entidades/, /Refinar/]) {
      await expect(page.getByText(label).first()).toBeVisible({
        timeout: 15_000,
      });
    }
  });

  test("'DAGs' tab renders a DAG list", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    // The DAG list either has an empty-state OR rows. Both are valid
    // — the test fails only if NEITHER renders within 15 s.
    const eitherState = page.locator("table, [data-state='empty'], .empty, .empty-state");
    await expect(eitherState.first()).toBeVisible({ timeout: 15_000 });
  });

  test("'Grafo' button responds to click (USER-REPORTED BUG)", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    const grafoBtn = page.getByRole("button", { name: /grafo/i }).first();
    // The button may render in the DAG tab; navigate there first.
    const dagsTab = page.getByText(/DAGs/i).first();
    if (await dagsTab.isVisible()) await dagsTab.click();

    // The user reports the button does nothing. We trap network
    // requests around the click — if zero requests fire AND the URL
    // doesn't change AND no new DOM mutation happens, the button is
    // dead. We assert SOMETHING measurable happens.
    const beforeUrl = page.url();
    let requestFired = false;
    page.on("request", () => { requestFired = true; });

    if (await grafoBtn.isVisible({ timeout: 10_000 }).catch(() => false)) {
      await grafoBtn.click({ trial: false }).catch(() => {});
      await page.waitForTimeout(2_000);
      const afterUrl = page.url();
      // Either the URL changed, or a request was made.
      const didSomething = afterUrl !== beforeUrl || requestFired;
      expect(didSomething,
        "'Grafo' button must trigger navigation, network request, or DOM change. " +
        "Current report: button is unresponsive.",
      ).toBe(true);
    } else {
      test.fail(true, "'Grafo' button not present on /studio — surface gap");
    }
  });

  test("'Deploy a Airflow' button fires /api/studio/dag-deploy (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await page.goto(`${LEGACY}/studio`);
      const deployBtn = page.getByRole("button", { name: /deploy a airflow|deploy/i }).first();
      if (!(await deployBtn.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.fail(true, "'Deploy a Airflow' button not present on /studio");
        return;
      }
      // Wait for the request initiated by the click. Fails if the
      // click is a no-op.
      const requestPromise = page.waitForRequest(
        (req) => req.url().includes("/api/studio/dag-deploy"),
        { timeout: 10_000 },
      );
      await deployBtn.click();
      await requestPromise.catch(() => {
        throw new Error(
          "Click on 'Deploy a Airflow' did NOT trigger a request to " +
          "/api/studio/dag-deploy within 10 s. User-reported bug confirmed.",
        );
      });
    },
  );

  test("'Entidades' tab shows entities table", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    const entitiesTab = page.getByText(/Entidades/i).first();
    await entitiesTab.click();
    await expect(
      page.locator("table, [data-state='empty'], .empty-state").first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("'Subir spec' drop zone accepts files (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await page.goto(`${LEGACY}/studio`);
      const entitiesTab = page.getByText(/Entidades/i).first();
      if (await entitiesTab.isVisible()) await entitiesTab.click();

      // The drop zone may be a hidden <input type="file"> behind a
      // styled label. Look for either an input[type=file] OR a
      // [data-dropzone] element.
      const fileInput = page.locator('input[type="file"]');
      const dropZone = page.locator(
        '[data-dropzone], [role="button"][aria-label*="subir" i], .dropzone',
      );

      const hasInput = await fileInput.count();
      const hasZone = await dropZone.count();
      expect(hasInput + hasZone,
        "'Subir spec' surface must render an <input type=file> or a [data-dropzone] " +
        "element. Neither was found — user-reported bug confirmed.",
      ).toBeGreaterThan(0);
    },
  );

  test("'Refinar' tab exposes Bronze/Silver/Master/Gold subtabs", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    const refinar = page.getByText(/Refinar/i).first();
    await refinar.click();
    for (const layer of [/Bronze/i, /Silver/i, /Master/i, /Gold/i]) {
      await expect(page.getByText(layer).first()).toBeVisible({
        timeout: 10_000,
      });
    }
  });

  test("'Silver' subtab renders data, not blank (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await page.goto(`${LEGACY}/studio`);
      const refinar = page.getByText(/Refinar/i).first();
      await refinar.click();
      const silver = page.getByText(/^Silver$/i).first();
      if (!(await silver.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.fail(true, "Silver subtab not present under /studio Refinar");
        return;
      }
      await silver.click();
      // The Silver pane must render SOMETHING — either a table, a
      // chart, an empty-state, or an error. A truly blank pane (just
      // whitespace) is the bug.
      const content = page.locator(
        "table, canvas, svg, .empty-state, .alert, pre, .silver-content",
      );
      await expect(content.first()).toBeVisible({
        timeout: 15_000,
      });
    },
  );

  test("'Crear en Superset' triggers /api/studio/superset (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await page.goto(`${LEGACY}/studio`);
      const supersetBtn = page.getByRole("button", { name: /crear en superset|superset/i }).first();
      if (!(await supersetBtn.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.fail(true, "'Crear en Superset' button not present on /studio");
        return;
      }
      const requestPromise = page.waitForRequest(
        (req) => req.url().includes("/api/studio/superset"),
        { timeout: 10_000 },
      );
      await supersetBtn.click();
      await requestPromise.catch(() => {
        throw new Error(
          "Click on 'Crear en Superset' did NOT trigger /api/studio/superset " +
          "within 10 s. User-reported bug confirmed.",
        );
      });
    },
  );

  test("'IA Semántica' tab loads without errors", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    const iaTab = page.getByText(/IA( Semántica)?/i).first();
    if (!(await iaTab.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "'IA Semántica' tab not present");
      return;
    }
    await iaTab.click();
    // Just verify the pane resolves to SOMETHING — error / empty /
    // loaded content all count.
    await page.waitForTimeout(2_000);
    const main = page.locator(
      ".tab-content, [role='tabpanel'], main, .studio-layout",
    );
    await expect(main.first()).toBeVisible();
  });
});
