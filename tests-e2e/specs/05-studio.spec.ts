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

    if (!(await grafoBtn.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "'Grafo' button not present on /studio — surface gap");
      return;
    }

    // v1.44.3.2 R1 Testing F3 follow-up: use page.waitForRequest with
    // a /studio/* predicate (mirrors the deploy + superset patterns
    // below) instead of a leaky page.on("request") + waitForTimeout
    // combo that picks up background telemetry as false-positives.
    // The button MUST either navigate the page OR fire a request to
    // a studio-scoped endpoint — both are observable signals; a no-op
    // button trips neither.
    const beforeUrl = page.url();
    const requestPromise = page
      .waitForRequest(
        (req) => req.url().includes("/api/studio/") || req.url().includes("/studio/graph"),
        { timeout: 4_000 },
      )
      .then(() => "request" as const)
      .catch(() => null);
    const navPromise = page
      .waitForURL((url) => url.href !== beforeUrl, { timeout: 4_000 })
      .then(() => "navigation" as const)
      .catch(() => null);

    await grafoBtn.click({ trial: false }).catch(() => {});
    const signal = await Promise.race([requestPromise, navPromise]);

    expect(signal,
      "'Grafo' button must trigger navigation OR a /studio/* request " +
      "within 4 s. User-reported bug: the button is unresponsive.",
    ).not.toBeNull();
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
    // The pane must resolve to SOMETHING — error / empty / loaded
    // content all count. expect().toBeVisible auto-waits up to its
    // timeout, so no bare waitForTimeout is needed.
    const main = page.locator(
      ".tab-content, [role='tabpanel'], main, .studio-layout",
    );
    await expect(main.first()).toBeVisible({ timeout: 10_000 });
  });

  test("'Plantillas' tab loads template list (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      // v1.44.3.2 R1 Testing F1 follow-up: the brief's user-report
      // list included "Plantillas no abre" but the original spec
      // missed coverage. The "Plantillas" affordance might be a
      // tab, a button, or a dropdown trigger in the legacy studio
      // — we look for whichever the page exposes.
      await page.goto(`${LEGACY}/studio`);
      const candidates = [
        page.getByRole("tab", { name: /plantilla/i }),
        page.getByRole("button", { name: /plantilla/i }),
        page.getByText(/^plantillas$/i),
      ];

      let trigger = null;
      for (const c of candidates) {
        if (await c.first().isVisible({ timeout: 5_000 }).catch(() => false)) {
          trigger = c.first();
          break;
        }
      }

      if (trigger === null) {
        test.fail(true, "'Plantillas' affordance not present on /studio");
        return;
      }

      // Click must surface a visible content region (list, modal,
      // or panel) within 10 s. Anything is fine — empty state, full
      // list, error — what's not fine is a no-op click that leaves
      // the surface unchanged.
      const beforeUrl = page.url();
      const navOrPanel = Promise.race([
        page
          .waitForURL((url) => url.href !== beforeUrl, { timeout: 8_000 })
          .then(() => "navigation" as const)
          .catch(() => null),
        page
          .waitForRequest(
            (req) => req.url().includes("/api/studio/templates"),
            { timeout: 8_000 },
          )
          .then(() => "request" as const)
          .catch(() => null),
      ]);

      await trigger.click().catch(() => {});
      // Also accept "a new dialog or panel appeared" as a positive
      // signal so a purely client-side modal counts.
      const panelAppeared = page
        .locator('[role="dialog"], [role="tabpanel"], .templates-list, .plantillas')
        .first()
        .isVisible({ timeout: 8_000 })
        .catch(() => false);

      const signal = await Promise.race([navOrPanel, panelAppeared.then((v) => v ? "panel" : null)]);
      expect(signal,
        "'Plantillas' click must surface navigation, a /api/studio/templates " +
        "request, or a visible panel/dialog within 8 s. User-reported bug: " +
        "click is a no-op.",
      ).not.toBeNull();
    },
  );
});
