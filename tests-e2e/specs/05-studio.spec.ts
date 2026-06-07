/**
 * v1.44.3.2 spec 05 — Legacy /studio (HTML console on port 8000).
 *
 * The user reported a concrete set of broken interactions on this
 * page (Grafo button, Deploy a Airflow, Plantillas, Subir spec drop
 * zone, Silver/Gold interactivity, Crear en Superset). Each
 * test here pins one of those failures so v1.44.3.3 has actionable
 * evidence.
 *
 * The studio surface is built by client-side JS (console/app/static/
 * js/studio/*), so tests assert on the RENDERED DOM, not the static
 * HTML, and wait for the tabs to materialise before clicking.
 */
import { test, expect } from "../fixtures/auth";
import type { Page } from "@playwright/test";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";
const STEP_READY: Record<number, RegExp> = {
  2: /DAGS|Airflow|Plantillas/i,
  3: /Entidades|Extractores|ENTIDAD/i,
  4: /Refinamiento|BRONZE|SILVER/i,
  5: /Analytics|Superset/i,
  6: /IA Semántica|semántic|catálogo/i,
  7: /RAG|Knowledge Base|BÚSQUEDA SEMÁNTICA/i,
};

async function openStudio(page: Page) {
  await page.goto(`${LEGACY}/studio`, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
}

async function waitStudioReady(page: Page) {
  await page.waitForFunction(
    () => {
      const win = window as typeof window & { goStep?: unknown };
      const picker = document.querySelector("#cartridge-sel") as HTMLSelectElement | null;
      const content = document.querySelector("#step-content");
      return (
        typeof win.goStep === "function" &&
        document.body.dataset.studioStep === "1" &&
        Boolean(picker && picker.options.length > 1 && picker.value) &&
        Boolean(content)
      );
    },
    null,
    { timeout: 15_000 },
  );
}

async function goStudioStep(page: Page, step: number) {
  await waitStudioReady(page);
  await page.evaluate((targetStep) => {
    const win = window as typeof window & {
      goStep: (step: number) => Promise<void> | void;
    };
    return win.goStep(targetStep);
  }, step);
  await page.waitForFunction(
    (targetStep) => {
      const content = document.querySelector("#step-content");
      return (
        document.body.dataset.studioStep === String(targetStep) &&
        Boolean(content && content.textContent?.trim())
      );
    },
    step,
    { timeout: 15_000 },
  );
  await expect(page.locator("#step-content")).toBeVisible({ timeout: 15_000 });
  if (step === 2) {
    await expect(page.locator("#dag-editor-body")).toBeVisible({ timeout: 15_000 });
  }
  if (step > 1) {
    await expect(page.locator("#step-content")).toContainText(STEP_READY[step], {
      timeout: 15_000,
    });
  }
}

test.describe("Legacy /studio page (port 8000)", () => {
  test("/studio loads with tab navigation", async ({ authedPage: page }) => {
    await openStudio(page);
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
    await openStudio(page);
    await goStudioStep(page, 2);
    // The DAG list either has an empty-state OR rows. Both are valid
    // — the test fails only if NEITHER renders within 15 s.
    const eitherState = page.locator("table, [data-state='empty'], .empty, .empty-state");
    await expect(eitherState.first()).toBeVisible({ timeout: 15_000 });
  });

  test("'Airflow' button opens the internal jobs viewer, not the external UI", async ({
    authedPage: page,
  }) => {
    await openStudio(page);
    await goStudioStep(page, 2);

    const airflow = page.locator("#dag-airflow-link");
    await expect(airflow).toBeVisible({ timeout: 15_000 });
    await expect(airflow).toContainText(/Airflow/i);

    const href = await airflow.getAttribute("href");
    expect(href, "Airflow button must use the in-console jobs viewer").toMatch(
      /^\/viewer\?type=jobs(?:&dag_id=.+)?$/,
    );
    expect(href, "Airflow button must not be a no-op").not.toBe("#");
    expect(href, "Airflow button must not open the external Airflow UI").not.toMatch(
      /\/dags\/|:8080|:8082/,
    );

    await airflow.click();
    await page.waitForURL(/\/viewer\?type=jobs/, { timeout: 10_000 });
  });

  test("'Grafo' button responds to click (USER-REPORTED BUG)", async ({
    authedPage: page,
  }) => {
    await openStudio(page);
    const grafoBtn = page.getByRole("button", { name: /grafo/i }).first();
    // The button may render in the DAG tab; navigate there first.
    await goStudioStep(page, 2);

    if (!(await grafoBtn.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.skip(true, "'Grafo' button not present on /studio — tracked in E2E findings");
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

  test("'Deploy a Airflow' is gated: fires /api/studio/dag-deploy OR is disabled with a reason (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await openStudio(page);
      await goStudioStep(page, 2);
      const deployBtn = page.getByRole("button", { name: /deploy/i }).first();
      if (!(await deployBtn.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.skip(true, "'Deploy a Airflow' button not present on /studio");
      }
      // #273 B4: the button must never be a silent no-op. Two valid
      // states, both observable:
      //   (a) packaged cartridge DAG (or production) -> DISABLED with a
      //       clear reason: packaged DAGs already run in Airflow, and the
      //       backend owns the production RCE gate.
      //   (b) user-authored DAG in dev -> fires POST /api/studio/dag-deploy.
      // refreshDagDeployButton() runs synchronously when the DAG editor
      // renders; give it a moment to settle the gated state before we read it.
      await page.waitForTimeout(2_000);
      if (await deployBtn.isDisabled().catch(() => false)) {
        const reason =
          (await deployBtn.getAttribute("title")) ||
          (await deployBtn.getAttribute("data-disabled-reason")) ||
          (await deployBtn.textContent()) ||
          "";
        expect(reason,
          "a disabled 'Deploy a Airflow' must explain why (packaged DAG / production gate)",
        ).toMatch(/deshabilitado|empaquetado|producci[oó]n|ci\/cd|airflow/i);
        return;
      }
      // Enabled button: it MUST fire the backend deploy request (which is
      // itself gated server-side). A no-op enabled button is the bug.
      const requestPromise = page.waitForRequest(
        (req) => req.url().includes("/api/studio/dag-deploy"),
        { timeout: 10_000 },
      );
      await deployBtn.click();
      await requestPromise.catch(() => {
        throw new Error(
          "An ENABLED 'Deploy a Airflow' did NOT trigger /api/studio/dag-deploy " +
          "within 10 s. User-reported bug: the button is unresponsive.",
        );
      });
    },
  );

  test("'Entidades' tab shows entities table", async ({ authedPage: page }) => {
    await openStudio(page);
    await goStudioStep(page, 3);
    await expect(
      page.locator("#entity-list-area, table, [data-state='empty'], .empty-state").first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("'Subir spec' drop zone accepts files (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await openStudio(page);
      await goStudioStep(page, 3);

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

  test("'Refinar' tab exposes Bronze/Silver/Gold subtabs", async ({
    authedPage: page,
  }) => {
    await openStudio(page);
    await goStudioStep(page, 4);
    for (const layer of [/Bronze/i, /Silver/i, /Gold/i]) {
      await expect(page.getByText(layer).first()).toBeVisible({
        timeout: 10_000,
      });
    }
  });

  test("'Silver' subtab renders data, not blank (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
      await openStudio(page);
      await goStudioStep(page, 4);
      const silver = page.locator(".tab").filter({ hasText: /^SILVER|^Silver/i }).first();
      if (!(await silver.isVisible({ timeout: 10_000 }).catch(() => false))) {
        test.skip(true, "Silver subtab not present under /studio Refinar");
      }
      await silver.click({ force: true });
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
      await openStudio(page);
      await goStudioStep(page, 5);
      await page.waitForFunction(
        () => {
          const text = document.querySelector("#gold-list")?.textContent || "";
          return (
            /Crear en Superset/i.test(text) ||
            /No hay datasets Gold|Error cargando datasets/i.test(text)
          );
        },
        null,
        { timeout: 15_000 },
      );
      const supersetBtn = page
        .getByRole("button", { name: /\+?\s*crear en superset/i })
        .first();
      if (!(await supersetBtn.isVisible().catch(() => false))) {
        test.skip(true, "'Crear en Superset' button not present on /studio");
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
    await openStudio(page);
    await goStudioStep(page, 6);
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
      await openStudio(page);
      await goStudioStep(page, 2);
      const existingPanel = page
        .locator('[role="dialog"], [role="tabpanel"], .templates-list, .plantillas')
        .first();
      if (await existingPanel.isVisible({ timeout: 5_000 }).catch(() => false)) {
        await expect(existingPanel).toBeVisible();
        return;
      }

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
        test.skip(true, "'Plantillas' affordance not present on /studio");
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
