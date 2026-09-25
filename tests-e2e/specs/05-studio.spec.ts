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
    for (const label of [/DAGs/, /Entidades/, /Refinar/]) {
      await expect(page.getByText(label).first()).toBeVisible({
        timeout: 15_000,
      });
    }
  });

  test("'DAGs' tab renders a DAG list", async ({ authedPage: page }) => {
    await openStudio(page);
    await goStudioStep(page, 2);
    const eitherState = page.locator("table, [data-state='empty'], .empty, .empty-state");
    await expect(eitherState.first()).toBeVisible({ timeout: 15_000 });
  });

  test("'Airflow' button opens the external Airflow UI deep link, not generic jobs", async ({
    authedPage: page,
  }) => {
    await openStudio(page);
    await goStudioStep(page, 2);

    const airflow = page.locator("#dag-airflow-link");
    await expect(airflow).toBeVisible({ timeout: 15_000 });
    await expect(airflow).toContainText(/Airflow/i);

    const config = await page.evaluate(async () => {
      const r = await fetch("/api/config", { credentials: "same-origin" });
      return r.ok ? r.json() : {};
    });
    const expectedBase = String((config as { airflow_url?: string }).airflow_url || "").replace(
      /\/+$/,
      "",
    );

    await page.waitForFunction(
      (base) => {
        const href = document.querySelector("#dag-airflow-link")?.getAttribute("href") || "";
        if (!href || href === "#") return false;
        if (href.includes("type=jobs")) return false;
        if (base) return href.startsWith(base) && /\/dags\/.+\/grid$/.test(href);
        return /\/dags\/.+\/grid$/.test(href);
      },
      expectedBase,
      { timeout: 15_000 },
    );

    const href = await airflow.getAttribute("href");
    expect(href, "Airflow button must not be a no-op").not.toBe("#");
    expect(href, "Airflow button must not open the generic Jobs viewer").not.toContain(
      "type=jobs",
    );
    expect(href, "Airflow button must not open the in-console monitor as primary action").not.toContain(
      "/viewer?type=airflow",
    );
    expect(href, "Airflow button must deep-link to the selected DAG in Airflow").toMatch(
      /\/dags\/.+\/grid$/,
    );
    if (expectedBase) {
      expect(href, "Airflow button must use the configured Airflow public URL").toContain(
        expectedBase,
      );
    }
    await expect(airflow).toHaveAttribute("target", "_blank");
  });

  test("'Grafo' button responds to click (USER-REPORTED BUG)", async ({
    authedPage: page,
  }) => {
    await openStudio(page);
    const grafoBtn = page.getByRole("button", { name: /grafo/i }).first();
    await goStudioStep(page, 2);

    await expect(grafoBtn,
      "'Grafo' button is a visible Studio control; absence must fail instead of skip.",
    ).toBeVisible({ timeout: 10_000 });

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
      await expect(deployBtn,
        "'Deploy a Airflow' is a visible Studio control; absence must fail instead of skip.",
      ).toBeVisible({ timeout: 10_000 });
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
      await expect(silver,
        "Silver subtab is a visible Studio control; absence must fail instead of skip.",
      ).toBeVisible({ timeout: 10_000 });
      await silver.click({ force: true });
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
        await expect(page.locator("#gold-list"),
          "When Superset creation is not visible, Studio must explain that Gold/Superset is unavailable instead of skipping.",
        ).toContainText(/No hay datasets Gold|Error cargando datasets|Superset.*interno|VPN|seguridad/i);
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
    await openStudio(page);
    await goStudioStep(page, 6);
    const main = page.locator(
      ".tab-content, [role='tabpanel'], main, .studio-layout",
    );
    await expect(main.first()).toBeVisible({ timeout: 10_000 });
  });

  test("'Plantillas' tab loads template list (USER-REPORTED BUG)",
    async ({ authedPage: page }) => {
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

      expect(trigger,
        "'Plantillas' affordance is a visible Studio control; absence must fail instead of skip.",
      ).not.toBeNull();

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

      await trigger!.click().catch(() => {});
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
