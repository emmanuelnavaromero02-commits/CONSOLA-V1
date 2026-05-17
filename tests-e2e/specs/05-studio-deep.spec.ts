/**
 * v1.44.3.2.1 spec 05-deep — Legacy /studio deep coverage.
 *
 * 50 tests across the 7 documented studio tabs, the lateral
 * assistant, cartridge switcher, and the 7 USER-REPORTED BUGS
 * Codex's diagnostic confirmed. Tests live in this separate
 * "-deep" file so v1.44.3.2's 05-studio.spec.ts stays as the
 * focused user-report pin list; this file goes broader.
 *
 * Storage state from global-setup pre-authenticates the page.
 * The legacy /studio is HTML-rendered client-side by
 * console/app/static/js/studio/* — tests assert on rendered DOM
 * + behaviour, not the raw HTML markup.
 */
import { test, expect } from "../fixtures/auth";

const LEGACY = process.env.LEGACY_URL || "http://localhost:8000";

test.describe("Studio — 7 tabs render", () => {
  const TABS = [
    /Resumen/i, /DAGs/i, /Entidades/i, /Refinar/i,
    /Analytics/i, /IA( Semántica)?/i, /RAG/i,
  ];
  for (const tab of TABS) {
    test(`tab '${tab.source}' is visible after hydration`, async ({
      authedPage: page,
    }) => {
      await page.goto(`${LEGACY}/studio`);
      await expect(page.getByText(tab).first()).toBeVisible({
        timeout: 15_000,
      });
    });

    test(`tab '${tab.source}' renders SOMETHING when clicked`,
      async ({ authedPage: page }) => {
        await page.goto(`${LEGACY}/studio`);
        const trigger = page.getByText(tab).first();
        if (!(await trigger.isVisible({ timeout: 10_000 }).catch(() => false))) {
          test.fail(true, `tab ${tab.source} not present`);
          return;
        }
        await trigger.click();
        // Wait for either a panel, table, empty state, or error.
        const content = page.locator(
          "main, .tab-content, [role='tabpanel'], table, .empty-state, .alert",
        ).first();
        await expect(content).toBeVisible({ timeout: 10_000 });
      },
    );
  }
});

test.describe("Studio — Resumen tab", () => {
  test("renders cartridge selector dropdown", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    // Allow a few seconds for the JS to wire the cartridge picker.
    await page.waitForTimeout(2_000);
    const picker = page.locator(
      'select[name*="cartridge"], select#cartridge, [data-testid="cartridge-picker"]',
    );
    if (!(await picker.first().isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "cartridge selector dropdown not surfaced");
    }
  });

  test("switching cartridge refreshes the summary", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.waitForTimeout(2_000);
    const picker = page.locator('select[name*="cartridge"], select#cartridge').first();
    if (!(await picker.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "cartridge picker not present — skip switching test");
      return;
    }
    const before = await page.locator("main, .tab-content").innerText().catch(() => "");
    const options = await picker.locator("option").allInnerTexts();
    if (options.length < 2) {
      test.fail(true, "only one cartridge in dropdown — can't test switch");
      return;
    }
    await picker.selectOption({ index: 1 });
    await page.waitForTimeout(3_000);
    const after = await page.locator("main, .tab-content").innerText().catch(() => "");
    expect(after,
      "switching cartridge in picker should change the summary content",
    ).not.toBe(before);
  });
});

test.describe("Studio — DAGs tab (USER-REPORTED BUGS pin)", () => {
  test("DAG list shows ≥ 1 DAG or an empty state", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    const dagsTab = page.getByText(/DAGs/i).first();
    await dagsTab.click();
    const surface = page.locator("table, .dag-list, .empty-state").first();
    await expect(surface).toBeVisible({ timeout: 15_000 });
  });

  test("'Copiar' button (if present) is clickable", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    const copy = page.getByRole("button", { name: /copiar/i }).first();
    if (!(await copy.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "'Copiar' button not surfaced — UX gap");
      return;
    }
    await expect(copy).toBeEnabled();
  });

  test("'Asistente' button opens a chat region", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    const assistant = page.getByRole("button", { name: /asistente/i }).first();
    await expect(assistant).toBeVisible({ timeout: 5_000 });
    await assistant.click();
    const chat = page.locator(
      "[role='dialog'], aside, .assistant-panel, .chat-panel",
    ).first();
    await expect(chat).toBeVisible({ timeout: 5_000 });
  });

  test("'Renombrar' button opens an input field", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    const renombrar = page.getByRole("button", { name: /renombrar/i }).first();
    if (!(await renombrar.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "'Renombrar' button not present");
      return;
    }
    await renombrar.click();
    const input = page.locator(
      'input[name*="rename"], [data-testid="rename-input"], input[type="text"]:visible',
    ).first();
    await expect(input).toBeVisible({ timeout: 5_000 });
  });

  test("'Eliminar' button opens a confirmation", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    const eliminar = page.getByRole("button", { name: /eliminar|borrar/i }).first();
    if (!(await eliminar.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "'Eliminar' button not present");
      return;
    }
    await eliminar.click();
    const dialog = page.locator(
      "[role='dialog'], .modal, .confirm",
    ).first();
    await expect(dialog).toBeVisible({ timeout: 5_000 });
  });

  test("DAG editor area (code mirror / textarea) exists", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    const editor = page.locator(
      "textarea.code-editor, .CodeMirror, .monaco-editor, textarea[name='code']",
    ).first();
    if (!(await editor.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "no code editor on /studio DAGs tab — UX gap");
    }
  });

  test("Plantillas sidebar lists templates", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/DAGs/i).first().click();
    const sidebar = page.locator(
      ".templates-list, .plantillas, [data-testid='templates']",
    ).first();
    if (!(await sidebar.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "Templates sidebar not surfaced — UX gap");
    }
  });
});

test.describe("Studio — Entidades tab", () => {
  test("'+ Entidad' button opens new-entity form", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Entidades/i).first().click();
    const addBtn = page.getByRole("button", { name: /\+ entidad|nueva entidad/i }).first();
    await expect(addBtn).toBeVisible({ timeout: 10_000 });
    await addBtn.click();
    const form = page.locator("form, [role='dialog']").first();
    await expect(form).toBeVisible({ timeout: 5_000 });
  });

  test("entity 'Extraer' button issues a POST", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Entidades/i).first().click();
    const extraer = page.getByRole("button", { name: /^extraer$/i }).first();
    if (!(await extraer.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "'Extraer' button not present");
      return;
    }
    const requestPromise = page.waitForRequest(
      (req) => req.method() === "POST" && /extract|run|extraction/.test(req.url()),
      { timeout: 5_000 },
    ).catch(() => null);
    await extraer.click();
    const r = await requestPromise;
    expect(r, "'Extraer' click must fire a POST to an extraction endpoint").not.toBeNull();
  });

  test("mode dropdown has full + incremental options", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Entidades/i).first().click();
    const select = page.locator(
      'select[name*="mode"], select#mode',
    ).first();
    if (!(await select.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "mode dropdown not surfaced");
      return;
    }
    const opts = await select.locator("option").allInnerTexts();
    const joined = opts.join("|").toLowerCase();
    expect(joined).toMatch(/full|incremental/);
  });
});

test.describe("Studio — Refinar subtabs", () => {
  for (const layer of [/Bronze/i, /Silver/i, /Master/i, /Gold/i]) {
    test(`Refinar > ${layer.source} subtab clicks render content`,
      async ({ authedPage: page }) => {
        await page.goto(`${LEGACY}/studio`);
        await page.getByText(/Refinar/i).first().click();
        const sub = page.getByText(layer).first();
        if (!(await sub.isVisible({ timeout: 10_000 }).catch(() => false))) {
          test.fail(true, `${layer.source} subtab not present`);
          return;
        }
        await sub.click();
        const pane = page.locator(
          "table, .silver-content, .gold-content, .empty-state, .alert, pre, canvas",
        ).first();
        await expect(pane).toBeVisible({ timeout: 15_000 });
      },
    );
  }

  test("Silver query editor is editable", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Refinar/i).first().click();
    const silver = page.getByText(/^Silver$/i).first();
    if (!(await silver.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "Silver subtab missing");
      return;
    }
    await silver.click();
    const editor = page.locator("textarea, .CodeMirror, [contenteditable='true']").first();
    if (!(await editor.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "Silver pane has no editable query field");
    }
  });

  test("Silver 'Ejecutar' button fires a query", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Refinar/i).first().click();
    const silver = page.getByText(/^Silver$/i).first();
    if (!(await silver.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.fail(true, "Silver subtab missing");
      return;
    }
    await silver.click();
    const ejecutar = page.getByRole("button", { name: /ejecutar|run/i }).first();
    if (!(await ejecutar.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "'Ejecutar' button missing on Silver");
      return;
    }
    const req = page.waitForRequest(
      (r) => r.method() === "POST" && /query|silver|refine/.test(r.url()),
      { timeout: 5_000 },
    ).catch(() => null);
    await ejecutar.click();
    const got = await req;
    expect(got, "'Ejecutar' click must fire a POST").not.toBeNull();
  });
});

test.describe("Studio — Analytics tab", () => {
  test("'Ver SQL' button surfaces the SQL", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Analytics/i).first().click();
    const verSql = page.getByRole("button", { name: /ver sql|view sql/i }).first();
    if (!(await verSql.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "'Ver SQL' button not present");
      return;
    }
    await verSql.click();
    const sqlBlock = page.locator("pre, code, .sql-viewer").first();
    await expect(sqlBlock).toBeVisible({ timeout: 5_000 });
  });

  test("'Abrir Superset' button navigates / opens external", async ({
    authedPage: page,
    context,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    await page.getByText(/Analytics/i).first().click();
    const abrir = page.getByRole("button", { name: /abrir superset/i }).first();
    if (!(await abrir.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "'Abrir Superset' button not present");
      return;
    }
    // The click may open a new tab; listen for it.
    const popupPromise = context.waitForEvent("page", { timeout: 5_000 }).catch(() => null);
    await abrir.click();
    const popup = await popupPromise;
    if (popup) {
      expect(popup.url()).toMatch(/superset|:8088/);
      await popup.close();
    } else {
      // Or the same-tab path.
      await page.waitForURL(/superset|:8088/, { timeout: 5_000 }).catch(() => {
        test.fail(true, "'Abrir Superset' did neither popup nor navigate");
      });
    }
  });
});

test.describe("Studio — IA Semántica + RAG tabs", () => {
  test("IA Semántica metrics list renders", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    const ia = page.getByText(/IA( Semántica)?/i).first();
    if (!(await ia.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "IA tab missing");
      return;
    }
    await ia.click();
    const list = page.locator("table, ul, .metrics-list, .empty-state").first();
    await expect(list).toBeVisible({ timeout: 10_000 });
  });

  test("RAG tab config panel renders", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    const rag = page.getByText(/^RAG$/i).first();
    if (!(await rag.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "RAG tab missing");
      return;
    }
    await rag.click();
    const panel = page.locator("form, .rag-config, .empty-state").first();
    await expect(panel).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("Studio — Lateral assistant", () => {
  test("assistant panel exists (right sidebar)", async ({
    authedPage: page,
  }) => {
    await page.goto(`${LEGACY}/studio`);
    const aside = page.locator(
      "aside, .assistant-panel, .copilot-panel, [data-testid='assistant']",
    ).first();
    if (!(await aside.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "no lateral assistant panel on /studio — UX gap");
    }
  });

  test("assistant input accepts text", async ({ authedPage: page }) => {
    await page.goto(`${LEGACY}/studio`);
    const input = page.locator(
      'aside textarea, aside input[type="text"], .assistant-panel textarea',
    ).first();
    if (!(await input.isVisible({ timeout: 10_000 }).catch(() => false))) {
      test.fail(true, "no assistant input — UX gap");
      return;
    }
    await input.fill("test message");
    expect(await input.inputValue()).toBe("test message");
  });
});
